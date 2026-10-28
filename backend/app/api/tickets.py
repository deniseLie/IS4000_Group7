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
from app.schemas.tickets import RequestCategory
from app.services import audit,customer_data
from app.services.authorization import PolicyDenied

router=APIRouter()


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
