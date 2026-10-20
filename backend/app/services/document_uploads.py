"""Prepare an owned private Storage path; the Action Service records only completed Storage objects.

Preparation is an audited scope read, not a file-presence claim. Bytes are uploaded through
Supabase Storage with the customer's JWT. Registration, receipt, audit and outbox commit atomically.
Closing the page before registration can leave a private unregistered object. No automatic
cleanup or resume is provided, and a metadata record alone is not proof of readable bytes.
"""
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.document_uploads import DocumentUploadTarget,PreparedDocumentUpload
from app.services import audit,customer_data


async def prepare(query: DocumentUploadTarget,ctx: RequestContext) -> ToolResult:
    snapshot=await customer_data.get_document_upload_context(query.case_id,query.item_id,ctx)
    if snapshot.data.case_id!=query.case_id or snapshot.data.item_id!=query.item_id:
        raise customer_data.CustomerDataIntegrityError('Upload context does not match the requested case and checklist item')
    prepared=PreparedDocumentUpload.model_validate({**query.model_dump(),
        'storage_path':f'{ctx.principal.customer_id}/{query.case_id}/{query.document_id}','bucket':'demobank-compliance-private'})
    audit.add('document','document.upload_prepared',resource_type='compliance.documents',resource_id=str(query.document_id),
        details={'size_bytes':query.size_bytes})
    return ToolResult(data=prepared.model_dump(mode='json'),call_id=snapshot.call_id,as_of=snapshot.as_of)
