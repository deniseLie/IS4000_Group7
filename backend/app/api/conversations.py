"""Create, list and reopen owned chats. Completed turns are written internally by the answer pipeline."""
from collections.abc import Awaitable,Callable
from typing import TypeVar
from uuid import UUID
import httpx
from fastapi import APIRouter,Depends,Request
from postgrest.exceptions import APIError
from pydantic import TypeAdapter,ValidationError
from app.api.audited import flush
from app.core import data_api
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.contracts import RequestContext
from app.schemas.conversations import ConversationAccess,ConversationHistory,ConversationSession,CreateConversation,GuestToken,TurnResponse
from app.services import audit,conversations

router=APIRouter()
T=TypeVar('T')


def access_for(session_id: UUID,request: Request,ctx: RequestContext) -> ConversationAccess:
    token=request.headers.get('X-Conversation-Token') if ctx.principal.kind=='guest' else None
    if token is not None:
        try:TypeAdapter(GuestToken).validate_python(token)
        except ValidationError as error:
            raise ApiError(400,'conversation_invalid','The guest conversation access token is invalid.') from error
    return ConversationAccess(session_id=session_id,guest_token=token)


async def response_for(operation: Callable[[],Awaitable[T]],ctx: RequestContext,request: Request,tool: str) -> T:
    if ctx.principal.kind=='customer' and ctx.principal.customer_id is None:
        raise ApiError(403,'profile_missing','A linked demo profile is required for saved conversations.')
    try:return await operation()
    except (APIError,httpx.TransportError,ValidationError,conversations.ConversationIntegrityError,PermissionError) as error:
        code=data_api.database_code(error) if isinstance(error,APIError) else None
        batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
        batch.add('data','data.read','denied' if code in {'P0002','42501'} or isinstance(error,PermissionError) else 'failed',
            details={'tool':tool,'db_code':code,'error_type':type(error).__name__})
        await flush(batch)
        log_event('conversation_request_failed',tool=tool,db_code=code,error_type=type(error).__name__)
        if code=='P0002':raise ApiError(404,'conversation_not_found','This conversation was not found or has expired.') from error
        if code=='42501' or isinstance(error,PermissionError):raise ApiError(403,'conversation_forbidden','This session cannot access saved conversations.') from error
        if code=='23505':raise ApiError(409,'conversation_conflict','This conversation changed. Reload it before retrying.') from error
        if code=='55P03':raise ApiError(409,'conversation_busy','A reply is still processing. Reopen this conversation to check it or cancel the pending reply.') from error
        if code=='22023':raise ApiError(422,'conversation_invalid','The conversation content or settings are invalid.') from error
        raise ApiError(503,'conversation_unavailable','Saved conversations are unavailable right now. Please retry.') from error


@router.post('/conversations',response_model=ConversationSession)
async def create(query: CreateConversation,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ConversationSession:
    async def operation() -> ConversationSession:
        with data_api.connect(ctx) as db:return await conversations.create(query,access_for(query.session_id,request,ctx),ctx,db)
    return await response_for(operation,ctx,request,'conversation.create')


@router.get('/conversations',response_model=list[ConversationSession])
async def recent(request: Request,ctx: RequestContext=Depends(get_ctx)) -> list[ConversationSession]:
    async def operation() -> list[ConversationSession]:
        with data_api.connect(ctx) as db:results=await conversations.recent(ctx,db)
        batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
        batch.add('data','data.read',resource_type='conversation.sessions',details={'tool':'conversation.list','result_count':len(results)})
        await flush(batch)
        return results
    return await response_for(operation,ctx,request,'conversation.list')


@router.get('/conversations/{session_id}',response_model=ConversationHistory)
async def load(session_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ConversationHistory:
    async def operation() -> ConversationHistory:
        with data_api.connect(ctx) as db:return await conversations.load(access_for(session_id,request,ctx),ctx,db)
    return await response_for(operation,ctx,request,'conversation.read')


@router.post('/conversations/{session_id}/turns/{turn_id}/cancel',response_model=TurnResponse)
async def cancel(session_id: UUID,turn_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> TurnResponse:
    async def operation() -> TurnResponse:
        with data_api.connect(ctx) as db:return await conversations.cancel_turn(turn_id,access_for(session_id,request,ctx),ctx,db)
    return await response_for(operation,ctx,request,'conversation.cancel')
