"""Inspect owned registered bytes locally. Contents are never returned, logged, saved or sent to a model.

This performs technical checks only. It does not run OCR, interpret required bank fields,
change the document's precheck state, write acceptance decisions or update a checklist.
"""
import asyncio
from datetime import datetime,timezone
from hashlib import sha256
import logging
import os
from uuid import UUID
import httpx
from app.core.observability import log_event,span
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.document_inspection import DocumentInspection,DocumentObject
from app.services import audit,customer_data,document_parser_process


async def download(source: DocumentObject,ctx: RequestContext) -> bytes:
    if ctx.principal.jwt is None:raise PermissionError('Private document inspection requires the customer JWT')
    url=os.environ['SUPABASE_URL'].rstrip('/')+'/storage/v1/object/'+source.bucket+'/'+source.storage_path
    headers={'apikey':os.environ['SUPABASE_ANON_KEY'],'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10,follow_redirects=False) as client:
                async with client.stream('GET',url,headers=headers) as response:
                    if response.status_code!=200:
                        error_size=0
                        async for chunk in response.aiter_bytes():
                            error_size+=len(chunk)
                            if error_size>4096:
                                raise customer_data.CustomerDataIntegrityError(f'Storage error response for document {source.document_id} exceeded 4 KiB; HTTP {response.status_code}')
                        response.raise_for_status()
                        raise customer_data.CustomerDataIntegrityError(f'Private Storage returned unexpected HTTP {response.status_code} for document {source.document_id}')
                    content_type=response.headers.get('content-type','').split(';')[0].strip().lower()
                    if content_type!=source.mime_type:
                        raise customer_data.CustomerDataIntegrityError(f'Stored document {source.document_id} returned MIME {content_type}, expected {source.mime_type}')
                    pieces: list[bytes]=[]; total=0
                    async for chunk in response.aiter_bytes():
                        total+=len(chunk)
                        if total>source.size_bytes or total>10485760:
                            raise customer_data.CustomerDataIntegrityError(f'Stored document {source.document_id} exceeds recorded byte count {source.size_bytes}')
                        pieces.append(chunk)
                    if total!=source.size_bytes:
                        raise customer_data.CustomerDataIntegrityError(f'Stored document {source.document_id} returned {total} bytes, expected {source.size_bytes}')
                    return b''.join(pieces)
        except (httpx.TransportError,httpx.HTTPStatusError) as error:
            transient=isinstance(error,httpx.TransportError) or error.response.status_code in {408,429,500,502,503,504}
            if not transient or attempt==2:
                raise customer_data.CustomerDataIntegrityError(f'Private Storage download failed for document {source.document_id}; HTTP {error.response.status_code if isinstance(error,httpx.HTTPStatusError) else "transport_error"}') from error
            log_event('document_storage_retry',logging.WARNING,document_id=str(source.document_id),attempt=attempt+1,error_type=type(error).__name__,
                status=error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None)
            await asyncio.sleep(0.5*2**attempt)
    raise RuntimeError('Document download retry loop ended without a response')


async def inspect(document_id: UUID,ctx: RequestContext) -> ToolResult:
    snapshot=await customer_data.get_document_object(document_id,ctx)
    source=snapshot.data
    if source is None:return ToolResult(data=None,call_id=snapshot.call_id,as_of=snapshot.as_of)
    with span('documents.inspect','tool',tool='documents.inspect'):
        payload=await download(source,ctx)
        try:parsed=await document_parser_process.parse(payload,source.mime_type)
        except document_parser_process.DocumentParserError as error:
            raise customer_data.CustomerDataIntegrityError(f'Local inspection failed for document {document_id}; parser output could not be verified') from error
    result=DocumentInspection(document_id=document_id,file_name=source.file_name,mime_type=source.mime_type,
        size_bytes=len(payload),checked_at=datetime.now(timezone.utc),sha256=sha256(payload).hexdigest(),
        status=parsed.status,issue=parsed.issue,page_count=parsed.page_count,width=parsed.width,height=parsed.height,
        extracted_characters=len(parsed.text),scope='technical_only',ocr='not_performed',bank_rules='not_configured')
    audit.add('document','document.inspected',resource_type='compliance.documents',resource_id=str(document_id),
        details={'status':parsed.status,'issue':parsed.issue,'size_bytes':len(payload),'extracted_characters':len(parsed.text),'page_count':parsed.page_count})
    return ToolResult(data=result.model_dump(mode='json'),call_id=snapshot.call_id,as_of=snapshot.as_of)
