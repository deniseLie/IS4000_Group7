"""Read-only customer records from permission-checked services; audit must succeed before release."""
from collections.abc import Awaitable,Callable
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
from app.schemas.customer_data import TransactionQuery
from app.services import audit,customer_data
from app.services.authorization import PolicyDenied

router=APIRouter(prefix='/customer')


async def read_result(operation: Callable[[],Awaitable[ToolResult]],request: Request,ctx: RequestContext,tool: str) -> ToolResult:
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to view your synthetic records.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','No synthetic customer profile is linked to this login.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:result=await operation()
    except (PolicyDenied,PermissionError,APIError,httpx.TransportError,ValidationError,customer_data.CustomerDataIntegrityError) as error:
        code=database_code(error) if isinstance(error,APIError) else None
        batch.add('data','data.read','failed',details={'tool':tool,'error_type':type(error).__name__,'db_code':code})
        await flush(batch)
        log_event('customer_read_failed',tool=tool,error_type=type(error).__name__,db_code=code)
        if isinstance(error,PermissionError) or code=='42501':raise ApiError(403,'customer_forbidden','Record access does not match the signed-in customer.') from error
        raise ApiError(503,'customer_unavailable','Your recorded demo data could not be verified. Reload records to retry.') from error
    await flush(batch)
    return result


@router.get('/profile',response_model=ToolResult)
async def profile(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_profile(ctx),request,ctx,'customer.get_profile')


@router.get('/accounts',response_model=ToolResult)
async def accounts(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_accounts(ctx),request,ctx,'customer.get_accounts')


@router.get('/cards',response_model=ToolResult)
async def cards(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_credit_cards(ctx),request,ctx,'customer.get_credit_cards')


@router.get('/loans',response_model=ToolResult)
async def loans(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_loans(ctx),request,ctx,'customer.get_loans')


@router.get('/compliance',response_model=ToolResult)
async def compliance(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_compliance_case(ctx),request,ctx,'compliance.get_case')


@router.get('/advisor',response_model=ToolResult)
async def advisor(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_rm_assignment(ctx),request,ctx,'rm.get_assignment')


@router.get('/documents',response_model=ToolResult)
async def documents(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_documents(ctx),request,ctx,'documents.list')


@router.post('/transactions',response_model=ToolResult)
async def transactions(query: TransactionQuery,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    filters=query.filters.model_dump(mode='json',exclude_none=True) if query.filters else None
    return await read_result(lambda:customer_data.get_transactions(ctx,query.from_date,query.to_date,filters),request,ctx,'customer.get_transactions')
