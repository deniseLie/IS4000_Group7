import json
import httpx
from postgrest.exceptions import APIError
from pydantic import ValidationError
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from sse_starlette.sse import EventSourceResponse
from app.api import chat_store
from app.api.conversations import response_for
from app.core.auth import get_customer_context
from app.core.data_api import database_code
from app.core import data_api
from app.core.config import settings
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event, start_trace, span
from app.schemas.chat import ChatRequest
from app.schemas.contracts import RequestContext
from app.schemas.conversations import StoredFailure
from app.schemas.events import AnswerEvent,WorkflowPausedEvent
from app.schemas.workflows import OwnedWorkflow
from app.services.answers import make_answer,make_database_answer
from app.services.conversations import ConversationIntegrityError
from app.services.guardrail import assess_request
from app.services.audit import AuditWriteError, start_batch
from app.services.knowledge import INDEX, source
from app.services.trace_store import save_trace
from app.services.orchestrator import answer_personal,answer_public
from app.services.checkpoints import PostgrestCheckpointSaver,signing_key

router=APIRouter()
FIXED_ROUTES={'secrets','lost_card','human_support','guest_balance'}

def audit_model_calls(batch,trace):
    """Record real model-call metadata before either a reply or a review pause is released."""
    for s in trace.spans:
        if s['name']=='llm':
            batch.add('agent','agent.llm_call','success' if s['status']=='ok' else 'failed',details={**{k:s['attrs'].get(k) for k in ('model','prompt_version','transport','input_tokens','output_tokens')},'ms':s.get('ms')})


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

@router.get('/knowledge/{article_id}')
def knowledge(article_id:str):
    article=INDEX.get(article_id)
    if not article:raise HTTPException(404,'Source not found')
    return {**source(article),'content':article.content,'notice':article.notice}

@router.get('/capabilities')
def capabilities() -> dict[str,str]:
    return {'conversation_store':settings.conversation_store}


@router.post('/chat')
async def chat(req:ChatRequest,request:Request,ctx:RequestContext=Depends(get_ctx)):
    """The gateway owns identity; saved chats load server history and persist the guarded question first."""
    p,db=ctx.principal,request.state.db
    if req.mode=='personal' and p.kind!='customer':raise ApiError(401,'auth_required','Please sign in to use your account.')
    if req.mode=='personal' and p.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
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
    trace.attrs.update(mode=req.mode,language=req.language[:32],authenticated=p.kind=='customer',history_len=len(req.messages))
    batch=start_batch(db,session_id=ctx.session_id)
    batch.add('chat','chat.message_received',details={'mode':req.mode,'history_len':len(req.messages)})
    async def personal_context() -> str:
        with span('customer_context','tool') as attrs:
            result=await run_in_threadpool(get_customer_context,db,p.customer_id)
            attrs['chars']=len(result)
        batch.add('data','data.read',resource_type='customer_context',details={'tool':'customer_context'})
        return result
    context=await personal_context() if prepared is None and req.mode=='personal' and risk.fixed_response is None else None
    async def events():
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
                    if prepared is not None:
                        with data_api.connect(ctx) as workflow_db:
                            saver=PostgrestCheckpointSaver(workflow_db,prepared.access,ctx,turn.turn,signing_key())
                            if req.mode=='personal':
                                reply=await answer_personal(req,risk,ctx,OwnedWorkflow(access=prepared.access,turn=turn.turn),saver)
                                if isinstance(reply,WorkflowPausedEvent):
                                    audit_model_calls(batch,trace)
                                    batch.add('chat','chat.workflow_review_ready',details={'action_count':len(reply.actions)})
                                    await batch.flush()
                                    trace.attrs.update(outcome='paused',route='workflow_review')
                                    yield {'event':'workflow_paused','data':reply.model_dump_json()}
                                    yield {'event':'done','data':''}
                                    return
                                result=reply.model_dump(mode='json')
                            else:result=(await answer_public(req,risk,ctx,saver)).model_dump(mode='json')
                    else:result=(await answer_public(req,risk,ctx,None)).model_dump(mode='json')
                    trace.attrs.update(route='llm',source_check=result['source_check'],needs_human=result['needs_human'],
                        privacy_warning=False,prompt_version=result['prompt_version'],history_trimmed=result['history_trimmed'])
                elif settings.knowledge_store=='database':result=(await make_database_answer(req,current_context,ctx)).model_dump(mode='json')
                else:result=await make_answer(req,current_context)
                if prepared is not None:
                    turn=await chat_store.finish(prepared,turn,AnswerEvent.model_validate(result),ctx)
                    yield {'event':'conversation','data':json.dumps(chat_store.metadata(prepared,turn))}
                    if isinstance(turn.response,StoredFailure):raise chat_store.SavedReplyFailed(turn.response.error.message)
                    result=turn.response.model_dump(mode='json')
            if prepared is not None:result['history_trimmed']=result['history_trimmed'] or prepared.history_trimmed
            audit_reply(batch,trace,result)
            await batch.flush()
            trace.attrs['outcome']='ok'
            yield {'event':'answer','data':json.dumps(result,ensure_ascii=False)}
            yield {'event':'done','data':''}
        except Exception as error:
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
            save_trace(trace)
    return EventSourceResponse(events())
