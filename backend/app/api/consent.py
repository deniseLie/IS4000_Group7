"""Read the signed-in customer's consent status; never grant or share through this endpoint."""
import httpx
from app.api.audited import flush as flush_audit
from fastapi import APIRouter,Depends,Request
from postgrest.exceptions import APIError
from pydantic import TypeAdapter,ValidationError
from app.core.errors import ApiError
from app.core.data_api import database_code
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.consent import ConsentQuery,ConsentResult,RelationshipConsent,RmAssignment
from app.schemas.contracts import RequestContext,ToolResult
from app.services import audit,customer_data
from app.services.authorization import PolicyDenied
from app.services.consent import ConsentResponseError,has_consent
from app.services.customer_data import CustomerDataIntegrityError,get_rm_assignment
from app.api.customer import read_result

router=APIRouter()


@router.get('/consent/records',response_model=ToolResult)
async def records(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:customer_data.get_consent_records(ctx),request,ctx,'consent.records')


@router.post('/consent/check',response_model=ConsentResult)
async def check(query: ConsentQuery,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ConsentResult:
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to check data-sharing consent.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:
        result=await has_consent(query,ctx,request.state.db.postgrest)
    except PolicyDenied as error:
        await flush_audit(batch)
        raise ApiError(403,'consent_forbidden','This session cannot check data-sharing consent.') from error
    except (APIError,httpx.TransportError,ConsentResponseError,ValidationError) as error:
        code=database_code(error) if isinstance(error,APIError) else None
        batch.add('consent','consent.checked','failed',details={'error_type':type(error).__name__,'db_code':code})
        await flush_audit(batch)
        log_event('consent_read_failed',error_type=type(error).__name__,db_code=code)
        if code=='42501':raise ApiError(403,'consent_forbidden','Consent access does not match the signed-in customer.') from error
        raise ApiError(503,'consent_unavailable','Consent status cannot be checked right now. Please retry; no data has been shared.') from error
    await flush_audit(batch)
    return result


@router.get('/consent/rm',response_model=RelationshipConsent)
async def relationship_status(request: Request,ctx: RequestContext=Depends(get_ctx)) -> RelationshipConsent:
    """Read-only status for sharing the basic profile with the currently assigned manager."""
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to review data sharing.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:
        assignment=TypeAdapter(RmAssignment | None).validate_python((await get_rm_assignment(ctx)).data)
        recipient=assignment.current if assignment is not None else None
        batch.add('data','data.read',resource_type='rm.assignments',details={'tool':'rm.get_assignment','recipient_count':int(recipient is not None)})
        consent=await has_consent(ConsentQuery(purpose='rm_brief',recipient=recipient.rm_id,categories=['profile']),ctx,request.state.db.postgrest) if recipient is not None else None
        result=RelationshipConsent(purpose='rm_brief',categories=['profile'],recipient=recipient,consent=consent)
    except PolicyDenied as error:
        await flush_audit(batch)
        raise ApiError(403,'consent_forbidden','This session cannot review data sharing.') from error
    except PermissionError as error:
        batch.add('data','data.read','denied',resource_type='rm.assignments',details={'tool':'rm.get_assignment'})
        await flush_audit(batch)
        raise ApiError(403,'profile_missing','A unique demo profile could not be resolved for this session.') from error
    except (APIError,httpx.TransportError,ConsentResponseError,CustomerDataIntegrityError,ValidationError) as error:
        code=database_code(error) if isinstance(error,APIError) else None
        batch.add('consent','consent.checked','failed',details={'error_type':type(error).__name__,'db_code':code})
        await flush_audit(batch)
        log_event('consent_read_failed',error_type=type(error).__name__,db_code=code)
        raise ApiError(503,'consent_unavailable','Data-sharing status cannot be checked right now. Please retry; no data has been shared.') from error
    await flush_audit(batch)
    return result
