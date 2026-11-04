"""Relationship manager workspace API (Persona 2: RM1-RM12, RM16).

Every route requires a signed-in user whose verified JWT carries administrator-owned app_metadata demobank_role=rm and
an rm_id (never user_metadata). The SQL functions check the same claim against rm.staff_logins and the client book again,
and audit every read, refusal and write. The assistant route streams status, then one structured answer (SSE, like
/chat). Write routes run only what the RM approved on screen; the assistant itself never writes.
"""
import asyncio
from datetime import datetime,timedelta,timezone
import json
import logging
from uuid import UUID
import httpx
import langsmith
from fastapi import APIRouter,Depends,Query,Request
from postgrest.exceptions import APIError
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from app.api.audited import flush
from app.core import auth
from app.core.data_api import database_code
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event,request_facts,start_trace
from app.schemas.contracts import RequestContext
from app.schemas.rm import (Block,ClientMessage,ConversationSave,Dismissal,InteractionApproval,MeetingDecision,MeetingMove,RmAssistantRequest,
    ServiceRequestDraft,ServiceRequestSubmit,TasksApproval,TaskStatus,WorkingHours)
from app.services import audit,customer_data
from app.services.llm_gateway import LLMError
from app.services.privacy import redact
from app.services.rm import graph,records
from app.services.rm.records import RmDenied,RmForbidden,RmSession,rm_scope

SGT=timezone(timedelta(hours=8))
router=APIRouter(prefix='/rm')
BUSINESS={'22023':400,'P1001':409,'23505':409,'P0002':404,'42501':403}
NOT_IN_BOOK='This client is not in your book. The attempt was recorded.'
SLOT_REFUSED='that time cannot be booked'  # rm.slot_problem refusals (0106): working hours, blocks, overlap, weekly limit


class Caller:
    def __init__(self,ctx: RequestContext,session: RmSession):self.ctx,self.session=ctx,session


def is_rm(claims: dict) -> UUID|None:
    """The advisor id when the verified token's app_metadata names an RM; user_metadata is never trusted."""
    meta=claims.get('app_metadata') or {}
    if meta.get('demobank_role')!='rm':return None
    try:return UUID(str(meta.get('rm_id')))
    except ValueError:return None


async def rm_caller(ctx: RequestContext=Depends(get_ctx)) -> Caller:
    if ctx.principal.kind!='customer' or not ctx.principal.jwt or not ctx.principal.user_id:raise ApiError(401,'auth_required','Please sign in.')
    claims=await run_in_threadpool(auth.verify_token,ctx.principal.jwt.get_secret_value())
    advisor=is_rm(claims)
    if advisor is None:raise ApiError(403,'rm_only','This workspace is for relationship managers.')
    return Caller(ctx,RmSession(ctx.principal.user_id,advisor))


def failure(error: Exception,tool: str) -> ApiError:
    if isinstance(error,RmDenied):return ApiError(403,'rm_not_in_book',NOT_IN_BOOK)
    if isinstance(error,RmForbidden):return ApiError(403,'rm_only','This workspace is for relationship managers.')
    code=database_code(error) if isinstance(error,APIError) else None
    log_event('rm_request_failed',logging.WARNING,tool=tool,error_type=type(error).__name__,db_code=code)
    if code in BUSINESS:
        message=(getattr(error,'message',None) or '').strip() or 'The request was refused.'
        if code=='P1001' and message.startswith(SLOT_REFUSED):return ApiError(409,'rm_slot_unavailable',message[:1].upper()+message[1:300])
        return ApiError(BUSINESS[code],'rm_refused' if code!='42501' else 'rm_forbidden',message[:300])
    return ApiError(503,'rm_unavailable','Your records could not be read right now. Nothing was changed. Please retry.')


async def run(operation,request: Request,caller: Caller,tool: str):
    batch=audit.start_batch(request.state.db,session_id=caller.ctx.session_id)
    try:
        with rm_scope(caller.session):result=await operation()
    except (RmDenied,RmForbidden,APIError,httpx.TransportError,ValidationError,PermissionError) as error:
        batch.add('data','data.read','failed',details={'tool':tool,'error_type':type(error).__name__[:64]})
        await flush(batch)
        raise failure(error,tool) from error
    await flush(batch)
    return result


# Reads ------------------------------------------------------------------------------------------------------------
@router.get('/me')
async def me(request: Request,caller: Caller=Depends(rm_caller)):return await run(lambda:records.profile(caller.ctx),request,caller,'rm.profile')

@router.get('/book')
async def book(request: Request,q: str|None=Query(None,max_length=80),caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.book(caller.ctx,q),request,caller,'rm.book')

@router.get('/clients/{customer_id}')
async def client(customer_id: int,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.client_profile(caller.ctx,customer_id),request,caller,'rm.client_profile')

@router.get('/clients/{customer_id}/notes')
async def notes(customer_id: int,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.meeting_notes(caller.ctx,customer_id),request,caller,'rm.meeting_notes')

@router.get('/tickets')
async def tickets(request: Request,caller: Caller=Depends(rm_caller)):return await run(lambda:records.tickets(caller.ctx),request,caller,'rm.tickets')

@router.get('/tickets/{reference}')
async def ticket(reference: str,request: Request,caller: Caller=Depends(rm_caller)):
    if not reference.startswith('TCK-'):raise ApiError(400,'bad_request','Use a TCK- reference.')
    return await run(lambda:records.ticket(caller.ctx,reference),request,caller,'rm.ticket')

@router.get('/appointments')
async def appointments(request: Request,past_days: int=Query(0,ge=0,le=30),caller: Caller=Depends(rm_caller)):
    # past_days: also list recent meetings (up to 30 days back) so they can be documented; the range stays within 62 days.
    start=datetime.now(SGT).date()-timedelta(days=past_days) if past_days else None
    end=(start+timedelta(days=13+past_days)) if start else None
    return await run(lambda:records.appointments(caller.ctx,start,end),request,caller,'rm.appointments')

@router.get('/availability')
async def availability(request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.availability(caller.ctx),request,caller,'rm.availability')

@router.get('/followups')
async def followups(request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.followups(caller.ctx),request,caller,'rm.followups')

@router.get('/tasks')
async def tasks(request: Request,customer_id: int|None=Query(None,gt=0),caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.tasks(caller.ctx,customer_id),request,caller,'rm.tasks')

@router.get('/categories')
async def categories(request: Request,caller: Caller=Depends(rm_caller)):
    async def read():return [c.model_dump(mode='json') for c in await customer_data.get_categories(caller.ctx) if c.customer_raisable]
    return await run(read,request,caller,'tickets.categories')


# Approved writes ----------------------------------------------------------------------------------------------------
@router.post('/interactions')
async def save_interaction(body: InteractionApproval,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.save_interaction(caller.ctx,body),request,caller,'rm.save_interaction')

@router.post('/tasks')
async def create_tasks(body: TasksApproval,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.create_tasks(caller.ctx,body),request,caller,'rm.create_tasks')

@router.patch('/tasks/{task_id}')
async def update_task(task_id: UUID,body: TaskStatus,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.update_task(caller.ctx,task_id,body.status),request,caller,'rm.update_task')

@router.post('/requests/check')
async def check_request(body: ServiceRequestDraft,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.check_service_request(caller.ctx,body.customer_id,body.category,body.summary,body.details,body.urgency),request,caller,'rm.check_service_request')

@router.post('/requests')
async def create_request(body: ServiceRequestSubmit,request: Request,caller: Caller=Depends(rm_caller)):
    if not body.client_verified:raise ApiError(400,'verification_required','Confirm the client’s identity check and authorisation before submitting.')
    return await run(lambda:records.create_service_request(caller.ctx,body),request,caller,'rm.create_service_request')

@router.post('/messages')
async def send_message(body: ClientMessage,request: Request,caller: Caller=Depends(rm_caller)):
    """RM9: only the approved version is sent. A failure is recorded as failed and never shown as sent."""
    try:return await run(lambda:records.send_client_update(caller.ctx,body),request,caller,'rm.send_client_update')
    except ApiError as error:
        if error.code!='rm_not_in_book':
            try:await run(lambda:records.record_failed_message(caller.ctx,body,error.code),request,caller,'rm.record_failed_message')
            except ApiError:log_event('rm_failed_message_not_recorded',logging.WARNING)
        raise

@router.put('/availability/hours')
async def set_hours(body: WorkingHours,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.set_working_hours(caller.ctx,body.weekday,body.starts,body.ends),request,caller,'rm.set_working_hours')

@router.post('/availability/blocks')
async def add_block(body: Block,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.add_block(caller.ctx,body.starts_at,body.ends_at,body.reason),request,caller,'rm.add_block')

@router.delete('/availability/blocks/{block_id}')
async def remove_block(block_id: UUID,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.remove_block(caller.ctx,block_id),request,caller,'rm.remove_block')

@router.post('/followups/dismiss')
async def dismiss(body: Dismissal,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.dismiss_followup(caller.ctx,body.item_key),request,caller,'rm.dismiss_followup')


# Meeting requests (0106, build plan 2a): she accepts a preferred time, proposes another or declines; moves a meeting.
# A time SQL refuses is 409 rm_slot_unavailable; a request or meeting outside her book is 403 rm_not_in_book.
@router.get('/meeting-requests')
async def meeting_requests(request: Request,status: str=Query('open',pattern='^(open|scheduled|closed)$'),caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.meeting_requests(caller.ctx,status),request,caller,'rm.meeting_requests')

@router.post('/meeting-requests/{request_id}/decide')
async def decide_meeting_request(request_id: UUID,body: MeetingDecision,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.decide_meeting_request(caller.ctx,request_id,body),request,caller,'rm.meeting_request_decide')

@router.post('/appointments/{appointment_id}/reschedule')
async def reschedule_meeting(appointment_id: UUID,body: MeetingMove,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.reschedule_meeting(caller.ctx,appointment_id,body),request,caller,'rm.meeting_reschedule')


# Saved RM conversations (0091) ------------------------------------------------------------------------------------
def scrub(value):
    """Redact every string before a conversation is stored (card numbers, emails, ID numbers, codes)."""
    if isinstance(value,str):return redact(value)[0]
    if isinstance(value,list):return [scrub(v) for v in value]
    if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
    return value

@router.get('/conversations')
async def conversations(request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.conversations(caller.ctx),request,caller,'rm.conversations')

@router.get('/conversations/{conversation_id}')
async def conversation(conversation_id: UUID,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.conversation(caller.ctx,conversation_id),request,caller,'rm.conversation')

@router.put('/conversations/{conversation_id}')
async def save_conversation(conversation_id: UUID,body: ConversationSave,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.save_conversation(caller.ctx,conversation_id,redact(body.title)[0],scrub(body.messages),scrub(body.context)),
        request,caller,'rm.save_conversation')

@router.delete('/conversations/{conversation_id}')
async def delete_conversation(conversation_id: UUID,request: Request,caller: Caller=Depends(rm_caller)):
    return await run(lambda:records.delete_conversation(caller.ctx,conversation_id),request,caller,'rm.delete_conversation')


# The RM assistant (SSE: status -> answer -> done, or error) ------------------------------------------------------------
def reply_details(reply: dict) -> dict:
    v=reply.get('validation') or {}
    return {'intent':reply.get('intent'),'status':reply.get('status'),'origin':reply.get('origin'),'withheld':v.get('withheld',0),
        'reasons':[r for r in v.get('reasons',[])][:10],'sources':len(reply.get('sources') or []),'prompt_version':reply.get('prompt_version')}


@router.post('/assistant')
async def rm_assistant(req: RmAssistantRequest,request: Request,caller: Caller=Depends(rm_caller)):
    db=request.state.db
    async def events():
        trace=start_trace()
        trace.attrs.update(side='rm',role='rm')  # the root run's request facts (admin Agents and Failures filters)
        batch=audit.start_batch(db,session_id=caller.ctx.session_id)
        batch.add('chat','chat.rm_message_received',details={'history_len':len(req.history),'intent_hint':req.intent,
            'has_client':req.context.client_id is not None})
        root=langsmith.trace('rm_assistant','chain',inputs={'message':req.message,'intent':req.intent},
            metadata={'request_id':str(caller.ctx.request_id),'session_id':str(caller.ctx.session_id)})
        run_tree=root.__enter__()
        queue: asyncio.Queue=asyncio.Queue()
        async def say(text: str):await queue.put(('status',text))
        async def work():
            with rm_scope(caller.session):
                try:await queue.put(('answer',await graph.respond(req,caller.ctx,say)))
                except Exception as error:await queue.put(('error',error))
        task=asyncio.create_task(work())
        failure_=None
        answered=False
        try:
            yield {'event':'status','data':'Reading your records…'}
            while True:
                kind,value=await queue.get()
                if kind=='status':yield {'event':'status','data':value};continue
                for call in trace.llm_calls:
                    batch.add('agent','agent.llm_call','success' if call['ok'] else 'failed',
                        details={k:call[k] for k in ('model','prompt_version','transport','input_tokens','output_tokens','ms')})
                if kind=='answer':
                    batch.add('chat','chat.rm_reply_sent',details=reply_details(value))
                    await batch.flush()
                    run_tree.add_outputs(value)
                    answered=True
                    yield {'event':'answer','data':json.dumps(value,ensure_ascii=False,default=str)}
                    yield {'event':'done','data':''}
                else:
                    failure_=value
                    log_event('rm_assistant_failed',logging.WARNING,error_type=type(value).__name__,db_code=database_code(value) if isinstance(value,APIError) else None,
                        reason=getattr(value,'reason',None),detail=str(value)[:160] if isinstance(value,LLMError) else None,
                        cause=type(value.__cause__).__name__ if value.__cause__ else None)
                    batch.add('chat','chat.rm_reply_sent','failed',details={'error_type':type(value).__name__[:64]})
                    try:await batch.flush()
                    except audit.AuditWriteError:log_event('rm_failure_audit_unavailable',logging.WARNING)
                    too_long=isinstance(value,LLMError) and type(value.__cause__).__name__=='LengthFinishReasonError'
                    message=(NOT_IN_BOOK if isinstance(value,RmDenied) else
                        'The answer was longer than the model output limit, so it was not shown. Nothing was changed. '
                        'Ask for a shorter or narrower answer, or raise MAX_TOKENS on the server.' if too_long else
                        'The assistant model is unavailable right now, so no answer was generated. Nothing was changed. Please retry.' if isinstance(value,LLMError) else
                        'Your records could not be read right now. Nothing was changed. Please retry.')
                    yield {'event':'error','data':message}
                break
        finally:
            if not task.done():task.cancel()
            run_tree.add_metadata({**trace.attrs,**request_facts(trace),'outcome':'ok' if answered else 'error',
                'error_type':type(failure_).__name__[:64] if failure_ else None})
            root.__exit__(*((type(failure_),failure_,failure_.__traceback__) if failure_ else (None,None,None)))
    return EventSourceResponse(events())
