"""Owned private upload preparation and object-location reads; Storage enforces caller JWT policies."""
from uuid import UUID
from fastapi import APIRouter,Depends,Request
from app.api.customer import read_result
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.document_uploads import DocumentUploadTarget
from app.schemas.prechecks import SupportSelection
from app.core import data_api
from app.services.authorization import require_permission
from app.services import customer_data,document_uploads,document_inspection,prechecks
from app.api.actions import response_for
from app.schemas.actions import ActionOutcome

router=APIRouter(prefix='/documents')


@router.get('/workspace',response_model=ToolResult)
async def workspace(request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:prechecks.workspace(ctx),request,ctx,'compliance.workspace')


@router.post('/support',response_model=ToolResult)
async def support(query: SupportSelection,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    async def prepare_support():
        require_permission(ctx.principal,'actions.create_case',{'customer_id':ctx.principal.customer_id},ctx)
        with data_api.connect(ctx) as db:
            result=await data_api.execute(lambda:db.rpc('prepare_compliance_support',{
                'p_session_id':str(query.session_id),'p_action_case_id':str(query.case_id),'p_review_case_id':str(query.review_case_id),
                'p_item_id':str(query.item_id) if query.item_id else None,'p_request_id':str(query.request_id) if query.request_id else None}).execute(),'compliance.support')
        from app.schemas.customer_data import CustomerEnvelope
        snapshot=CustomerEnvelope[dict].model_validate(result.data)
        if snapshot.customer_id!=ctx.principal.customer_id:raise customer_data.CustomerDataIntegrityError('Support scope changed')
        return ToolResult(data=snapshot.data,call_id=snapshot.call_id,as_of=snapshot.as_of)
    return await read_result(prepare_support,request,ctx,'actions.create_case')


@router.get('/{document_id}/precheck',response_model=ToolResult)
async def precheck(document_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:prechecks.read(document_id,ctx),request,ctx,'documents.precheck')


@router.post('/{document_id}/precheck',response_model=ToolResult)
async def run_precheck(document_id: UUID,request: Request,recheck: bool=False,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:prechecks.run(document_id,ctx,recheck),request,ctx,'documents.precheck')


@router.post('/{document_id}/submit',response_model=ActionOutcome)
async def submit(document_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionOutcome:
    return await response_for(lambda:prechecks.submit(document_id,ctx),ctx,request)


@router.post('/uploads/prepare',response_model=ToolResult)
async def prepare(query: DocumentUploadTarget,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    return await read_result(lambda:document_uploads.prepare(query,ctx),request,ctx,'documents.prepare_upload')


@router.get('/{document_id}/location',response_model=ToolResult)
async def location(document_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    result=await read_result(lambda:customer_data.get_document_location(document_id,ctx),request,ctx,'documents.download')
    if result.data is None:raise ApiError(404,'document_not_available','No owned stored file is available for this document. Reload Records to check its status.')
    return result


@router.get('/{document_id}/inspection',response_model=ToolResult)
async def inspection(document_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ToolResult:
    result=await read_result(lambda:document_inspection.inspect(document_id,ctx),request,ctx,'documents.inspect')
    if result.data is None:raise ApiError(404,'document_not_available','No owned registered object is available for this technical file check. Reload Records.')
    return result
