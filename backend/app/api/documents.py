"""Owned private upload preparation and object-location reads; Storage enforces caller JWT policies."""
from uuid import UUID
from fastapi import APIRouter,Depends,Request
from app.api.customer import read_result
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.document_uploads import DocumentUploadTarget
from app.services import customer_data,document_uploads,document_inspection

router=APIRouter(prefix='/documents')


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
