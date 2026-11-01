"""My requests (TK1/TK2) and the request category catalogue (F20). Creating a ticket is a reviewed
ticket.create action through /commands, so the customer always sees and confirms it before it exists."""
import httpx
from fastapi import APIRouter,Depends,Request
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.api.audited import flush
from app.core.data_api import database_code
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.tickets import Reference,RequestCategory,TicketFollowUp,TicketFollowUpResult
from app.services import audit,customer_data,rm_handover
from app.services.actions.store import ActionIntegrityError
from app.services.privacy import redact
from app.services.authorization import PolicyDenied

router=APIRouter()


@router.post('/tickets/{reference}/follow-up',response_model=TicketFollowUpResult)
async def follow_up(reference: Reference,body: TicketFollowUp,request: Request,ctx: RequestContext=Depends(get_ctx)) -> TicketFollowUpResult:
    """Append a reviewed client message without changing the server-owned compliance context."""
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to update your request.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    message,_=redact(body.message)
    if len(message)>480:raise ApiError(422,'follow_up_too_long','Shorten the message to 480 characters after private details are removed.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:added=await rm_handover.follow_up(reference,message,ctx)
    except (PolicyDenied,PermissionError) as error:
        await flush(batch)
        raise ApiError(403,'tickets_forbidden','This session cannot update this request.') from error
    except (APIError,httpx.TransportError,ActionIntegrityError) as error:
        code=database_code(error) if isinstance(error,APIError) else None
        batch.add('data','data.write','failed',details={'tool':'tickets.follow_up','error_type':type(error).__name__,'db_code':code})
        await flush(batch)
        log_event('ticket_follow_up_failed',error_type=type(error).__name__,db_code=code)
        if code=='42501':raise ApiError(403,'tickets_forbidden','Only your own open request to your relationship manager can take a follow-up.') from error
        raise ApiError(503,'ticket_follow_up_unavailable','Your message could not be recorded. The existing request is unchanged; retry the message.') from error
    await flush(batch)
    return TicketFollowUpResult(reference=reference,added=added)


@router.get('/tickets/categories',response_model=list[RequestCategory])
async def categories(ctx: RequestContext=Depends(get_ctx)) -> list[RequestCategory]:
    try:return await customer_data.get_categories(ctx)
    except (APIError,httpx.TransportError,ValidationError,PolicyDenied) as error:
        log_event('ticket_categories_failed',error_type=type(error).__name__)
        raise ApiError(503,'categories_unavailable','Request categories cannot be loaded right now. Please retry.') from error


@router.get('/tickets',response_model=ToolResult)
async def tickets(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to see your requests.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:result=await customer_data.get_tickets(ctx)
    except (PolicyDenied,PermissionError) as error:
        await flush(batch)
        raise ApiError(403,'tickets_forbidden','This session cannot read these requests.') from error
    except (APIError,httpx.TransportError,ValidationError,customer_data.CustomerDataIntegrityError) as error:
        code=database_code(error) if isinstance(error,APIError) else None
        batch.add('data','data.read','failed',details={'tool':'tickets.list','error_type':type(error).__name__,'db_code':code})
        await flush(batch)
        log_event('tickets_read_failed',error_type=type(error).__name__,db_code=code)
        raise ApiError(503,'tickets_unavailable','Your requests cannot be loaded right now. Please retry.') from error
    await flush(batch)
    return result
