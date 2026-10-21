"""Authenticated inbox reads and idempotent read acknowledgements; no customer delivery endpoint."""
from collections.abc import Awaitable,Callable
from typing import TypeVar
from uuid import UUID
import httpx
from fastapi import APIRouter,Depends,Request
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.api.audited import flush
from app.core import data_api
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.contracts import RequestContext
from app.schemas.notifications import Inbox,Notification
from app.services import audit,notifications
from app.services.authorization import PolicyDenied

router=APIRouter()
T=TypeVar('T')


async def response_for(operation: Callable[[],Awaitable[T]],ctx: RequestContext,request: Request) -> T:
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to view notifications.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','A linked demo profile is required for notifications.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:result=await operation()
    except (PolicyDenied,notifications.NotificationIntegrityError,ValidationError,APIError,httpx.TransportError) as error:
        code=data_api.database_code(error) if isinstance(error,APIError) else None
        log_event('notification_request_failed',error_type=type(error).__name__,db_code=code)
        await flush(batch)
        if code=='P0002':raise ApiError(404,'notification_not_found','This notification was not found in your demo profile.') from error
        if code=='42501' or isinstance(error,PolicyDenied):raise ApiError(403,'notification_forbidden','Notification access does not match this session.') from error
        raise ApiError(503,'notification_unavailable','Recorded notifications cannot be verified right now. Reload the inbox to retry.') from error
    await flush(batch)
    return result


@router.get('/notifications',response_model=Inbox)
async def recent(request: Request,ctx: RequestContext=Depends(get_ctx)) -> Inbox:
    async def operation() -> Inbox:
        with data_api.connect(ctx) as db:return await notifications.recent(ctx,db)
    return await response_for(operation,ctx,request)


@router.post('/notifications/{notification_id}/read',response_model=Notification)
async def mark_read(notification_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> Notification:
    async def operation() -> Notification:
        with data_api.connect(ctx) as db:return await notifications.mark_read(notification_id,ctx,db)
    return await response_for(operation,ctx,request)
