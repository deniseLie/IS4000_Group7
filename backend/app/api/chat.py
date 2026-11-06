import json
import time
import httpx
import langsmith
from postgrest.exceptions import APIError
from pydantic import ValidationError
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask
from app.api import chat_store
from app.api.conversations import response_for
from app.core.auth import get_customer_context
from app.core.data_api import database_code
from app.core import data_api
from app.core.config import settings
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event, request_facts, start_trace, span
from app.schemas.chat import ChatRequest
from app.schemas.contracts import RequestContext
from app.schemas.conversations import StoredFailure
from app.schemas.events import AnswerEvent
from app.services.answers import make_answer
from app.services.conversations import ConversationIntegrityError
from app.services.guardrail import assess_request
from app.services.audit import AuditWriteError, start_batch
from app.services import orchestrator,rm_handover,talking_points

router=APIRouter()
FIXED_ROUTES={'secrets','lost_card','human_support','guest_balance','other_customer_data','instruction_override'}

def audit_model_calls(batch,trace):
    """Record real model-call metadata before the reply is released."""
    for call in trace.llm_calls:
        batch.add('agent','agent.llm_call','success' if call['ok'] else 'failed',details={k:call[k] for k in ('model','prompt_version','transport','input_tokens','output_tokens','ms')})


def audit_reply(batch,trace,result=None,error=None):
    """Model calls and the reply outcome from the trace: ids, enums and counts only."""
    audit_model_calls(batch,trace)
    route=trace.attrs.get('route')
    if error:
        batch.add('chat','chat.reply_sent','failed',details={'route':route,'error_type':type(error).__name__[:64]})
        return
    batch.add('chat','chat.fixed_response' if route in FIXED_ROUTES else 'chat.reply_sent',details={
        'route':route,'origin':result['response_origin'],'source_check':result['source_check'],'needs_human':result['needs_human'],
        'privacy_warning':result['privacy_warning'],'history_trimmed':result['history_trimmed'],'prompt_version':result['prompt_version'],
        # Audit's released citation format is article@vN#anchor (without the knowledge namespace).
        'reject_reason':trace.attrs.get('reject_reason'),'source_ids':[x['id'].removeprefix('kb:') for x in result['sources']]})

@router.get('/capabilities')
def capabilities() -> dict[str,str]:
    return {'conversation_store':settings.conversation_store}


@router.post('/chat')
async def chat(req:ChatRequest,request:Request,ctx:RequestContext=Depends(get_ctx)):
    """The gateway owns identity; saved chats load server history and persist the guarded question first."""
    p,db=ctx.principal,request.state.db
    if req.mode=='personal' and p.kind!='customer':raise ApiError(401,'auth_required','Please sign in to use your account.')
    if req.mode=='personal' and p.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    if req.record_context is not None:
        from app.api.customer import read_result
        from app.services import prechecks,compliance_selection
        snapshot=await read_result(lambda:prechecks.workspace(ctx),request,ctx,'compliance.workspace')
        compliance_selection.validate(req.record_context,prechecks.Workspace.model_validate(snapshot.data))
    prepared=None
    if settings.conversation_store=='database':
        prepared=await response_for(lambda:chat_store.prepare(req,request,ctx),ctx,request,'conversation.start')
        req,risk=prepared.request,prepared.risk
        ctx=ctx.model_copy(update={'case_id':prepared.turn.turn.case_id})
    else:
        if req.conversation_id is not None:raise ApiError(503,'conversation_unavailable','Saved conversations are not enabled on this server.')
        risk=assess_request(req)
    if settings.pipeline=='v2' and req.mode=='personal' and risk.fixed_response is None and prepared is None:
        raise ApiError(503,'personal_unavailable','Personal workflows require saved conversations to be enabled.')
    trace=start_trace()
    started=time.perf_counter()
    trace.attrs.update(mode=req.mode,language=req.language[:32],authenticated=p.kind=='customer',history_len=len(req.messages))
    # Root LangSmith run: opened here and closed in the stream's finally, so pre-stream reads nest under it too.
    root=langsmith.trace('chat','chain',inputs={'messages':[m.model_dump() for m in req.messages],'mode':req.mode,'language':req.language},
        metadata={'request_id':str(ctx.request_id),'session_id':str(ctx.session_id),'pipeline':settings.pipeline})
    run=root.__enter__()
    batch=start_batch(db,session_id=ctx.session_id)
    batch.add('chat','chat.message_received',details={'mode':req.mode,'history_len':len(req.messages)})
    async def personal_context() -> str:
        with span('customer_context','tool') as attrs:
            result=await run_in_threadpool(get_customer_context,db,p.customer_id)
            attrs['chars']=len(result)
        batch.add('data','data.read',resource_type='customer_context',details={'tool':'customer_context'})
        return result
    try:context=await personal_context() if prepared is None and req.mode=='personal' and risk.fixed_response is None else None
    except BaseException as error:
        root.__exit__(type(error),error,error.__traceback__)
        raise
    after={}  # lane 1 of talking points (0108): runs once the answer has been sent, for a new reply in a signed-in saved chat
    async def events():
        failure=None
        turn=prepared.turn if prepared is not None else None
        if prepared is not None:yield {'event':'conversation','data':json.dumps(chat_store.metadata(prepared,turn))}
        yield {'event':'status','data':'Checking the available information…'}
        try:
            if turn is not None and turn.response is not None:
                if isinstance(turn.response,StoredFailure):raise chat_store.SavedReplyFailed(turn.response.error.message)
                result=turn.response.model_dump(mode='json')
                trace.attrs.update(route='conversation_replay',source_check=result['source_check'],needs_human=result['needs_human'])
            else:
                current_context=await personal_context() if settings.pipeline!='v2' and prepared is not None and req.mode=='personal' and risk.fixed_response is None else context
                if settings.pipeline=='v2' and risk.fixed_response is None:
                    tctx=orchestrator.TurnContext(request=ctx,turn_id=turn.turn.turn_id if turn is not None else None,
                        conversation_id=turn.turn.session_id if turn is not None else None)
                    reply=None
                    async for kind,item in orchestrator.stream(req,risk,tctx):
                        # A section as each agent finishes; the saved answer below stays the reply of record.
                        if kind=='section':trace.attrs.setdefault('first_section_ms',round((time.perf_counter()-started)*1000))
                        if kind in ('plan','section'):yield {'event':kind,'data':item.model_dump_json()}
                        else:reply=item
                    if reply is None:raise ValueError('The turn ended without an answer')
                    if reply.offer is not None:
                        trace.attrs['offered']=len(reply.offer.offers)  # the admin dashboard's offer acceptance
                        yield {'event':'offer','data':reply.offer.model_dump_json()}
                    result=reply.model_dump(mode='json')
                    trace.attrs.update(route='llm',source_check=result['source_check'],needs_human=result['needs_human'],
                        privacy_warning=False,prompt_version=result['prompt_version'],history_trimmed=result['history_trimmed'])
                else:result=await make_answer(req,current_context,ctx)
                result['care']=risk.care  # both pipelines; the answer text is unchanged (guardrail.CARE)
                if risk.fixed_response is not None and risk.fixed_response.code=='human_support' and p.kind=='customer' and prepared is not None:
                    # "Talk to a person" in a signed-in saved chat goes to the client's RM (migrations 0073, 0082); no model call. With
                    # rm_handover.confirm_first (the default) she sees a preview and confirms before anything is recorded (CR8).
                    # An open RM ticket on the same topic is reused; a failure is explained instead of a generic error (CR9).
                    selected=[req.record_context.document_id] if req.record_context is not None and req.record_context.document_id else []
                    if rm_handover.confirm_first():
                        # CR8: show what would be sent; her "Send to my RM" on the next turn records it.
                        result.update(answer='Of course. '+await rm_handover.offer(risk.redacted_message,req.messages,ctx),needs_human=True)
                    else:
                        handover=await rm_handover.route(risk.redacted_message,'P2',ctx,None,rm_handover.task_key('person',turn.turn.turn_id),
                            history=req.messages,document_ids=selected)
                        result.update(answer=handover.reply() if handover.failed else 'Of course. '+handover.reply(),needs_human=True)
                elif 'lost_card' in risk.risk_flags and p.kind=='customer' and prepared is not None:
                    # CR12: the safety steps come first; an urgent report is offered, and her yes on the next turn sends it (act).
                    text,group=await orchestrator.lost_card_offer(risk.redacted_message,ctx,turn.turn.turn_id)
                    result.update(answer=result['answer']+'\n\n'+text,needs_human=True,offer=group.model_dump(mode='json') if group else None)
                    if group is not None:
                        trace.attrs['offered']=len(group.offers)
                        yield {'event':'offer','data':group.model_dump_json()}
                if prepared is not None:
                    turn=await chat_store.finish(prepared,turn,AnswerEvent.model_validate(result),ctx)
                    yield {'event':'conversation','data':json.dumps(chat_store.metadata(prepared,turn))}
                    if isinstance(turn.response,StoredFailure):raise chat_store.SavedReplyFailed(turn.response.error.message)
                    result=turn.response.model_dump(mode='json')
            if prepared is not None:result['history_trimmed']=result['history_trimmed'] or prepared.history_trimmed
            audit_reply(batch,trace,result)
            await batch.flush()
            if prepared is not None and p.kind=='customer' and trace.attrs.get('route')!='conversation_replay':after['turn']=turn.turn.turn_id
            trace.attrs['outcome']='ok'
            run.add_outputs(result)
            yield {'event':'answer','data':json.dumps(result,ensure_ascii=False)}
            yield {'event':'done','data':''}
        except Exception as error:
            failure=error
            code=database_code(error) if isinstance(error,APIError) else None
            log_event('chat_failed',error_type=type(error).__name__,db_code=code)
            trace.attrs.update(outcome='error',error_type=type(error).__name__[:64],route=trace.attrs.get('route') or 'error')
            message=str(error) if isinstance(error,chat_store.SavedReplyFailed) else chat_store.unavailable().error.message
            if prepared is not None and turn.turn.state=='processing':
                try:
                    turn=await chat_store.finish(prepared,turn,chat_store.unavailable(),ctx)
                    yield {'event':'conversation','data':json.dumps(chat_store.metadata(prepared,turn))}
                except (APIError,httpx.TransportError,ValidationError,ConversationIntegrityError) as storage_error:
                    log_event('conversation_save_failed',error_type=type(storage_error).__name__)
                    message='The reply failed and could not be saved. Reopen this chat to check it or cancel the pending reply.'
            if not isinstance(error,AuditWriteError):
                audit_reply(batch,trace,error=error)
                try:await batch.flush()
                except AuditWriteError as audit_error:log_event('chat_failure_audit_unavailable',error_type=type(audit_error).__name__)
            yield {'event':'error','data':message}
        finally:
            trace.attrs.setdefault('outcome','error')
            trace.attrs.setdefault('route','error')
            trace.attrs.update(request_facts(trace))
            run.add_metadata(trace.attrs)
            root.__exit__(*((type(failure),failure,failure.__traceback__) if failure else (None,None,None)))
    async def topics_after_reply():
        if 'turn' in after:await talking_points.after_reply(ctx,after['turn'],req.messages,risk,req.offer is not None)
    return EventSourceResponse(events(),background=BackgroundTask(topics_after_reply))
