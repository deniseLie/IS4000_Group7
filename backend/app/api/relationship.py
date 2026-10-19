"""Audited relationship reads and owned appointment holds; booking decisions use the Action Service."""
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
from app.schemas.relationship import SlotsQuery
from app.schemas.actions import ActionOutcome
from app.schemas.appointments import HoldAppointment
from app.core import data_api
from app.api.actions import response_for
from app.services.actions.store import ActionIntegrityError,checked
from app.services import audit,customer_data
from app.services.authorization import PolicyDenied,require_permission

router=APIRouter()


async def read_result(operation: Callable[[],Awaitable[ToolResult]],request: Request,ctx: RequestContext,tool: str) -> ToolResult:
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to view relationship support.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:result=await operation()
    except (PolicyDenied,PermissionError) as error:
        await flush(batch)
        raise ApiError(403,'relationship_forbidden','This session cannot read these relationship records.') from error
    except (APIError,httpx.TransportError,ValidationError,customer_data.CustomerDataIntegrityError) as error:
        code=database_code(error) if isinstance(error,APIError) else None
        batch.add('data','data.read','failed',details={'tool':tool,'error_type':type(error).__name__,'db_code':code})
        await flush(batch)
        log_event('relationship_read_failed',tool=tool,error_type=type(error).__name__,db_code=code)
        if code=='22023':raise ApiError(422,'slots_invalid','Search today or later, over at most 14 days, with 1–20 suggestions.') from error
        if code=='42501':raise ApiError(403,'relationship_forbidden','Record access does not match the signed-in customer.') from error
        raise ApiError(503,'relationship_unavailable','Relationship records cannot be loaded right now. Please retry.') from error
    await flush(batch)
    return result


@router.get('/relationship/requests',response_model=ToolResult)
async def requests(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_requests(ctx),request,ctx,'requests.list')


@router.post('/relationship/slots',response_model=ToolResult)
async def slots(query: SlotsQuery,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_free_slots(query,ctx),request,ctx,'appointments.find_slots')


@router.get('/relationship/appointments',response_model=ToolResult)
async def appointments(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_appointments(ctx),request,ctx,'appointments.list')


@router.post('/relationship/holds',response_model=ActionOutcome)
async def hold(query: HoldAppointment,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionOutcome:
    async def operation() -> ActionOutcome:
        require_permission(ctx.principal,'appointment.hold',{'customer_id':ctx.principal.customer_id},ctx)
        with data_api.connect(ctx) as db:
            response=await data_api.execute(lambda:db.rpc('appointment_hold',{'p_command_id':str(query.command_id),
                'p_session_id':str(query.session_id),'p_case_id':str(query.case_id),'p_target':query.target.model_dump(mode='json')}).execute(),'appointment.hold')
        result=checked(response.data,ctx)
        if result.command.case_id!=query.case_id or result.command.type!='appointment.hold' or result.command.idempotency_key!=f'{query.case_id}:hold:appointment.hold':
            raise ActionIntegrityError('The returned hold does not match its requested case and operation')
        if result.command.target!=query.target:raise ActionIntegrityError('The held slot differs from the requested slot')
        return result
    return await response_for(operation,ctx,request)
