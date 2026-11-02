"""Real DOCX/scan bytes through isolated parsing, local OCR, JWT/RLS, checks and submission.

Run from backend with TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1. OCR pass cases require the app's actual
DOCUMENT_OCR_COMMAND (see docs/workflows/workflow-1-client.md); no model or OCR output is fabricated.
"""
import asyncio
import os
from collections.abc import Callable
from datetime import datetime,timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from pypdf import PdfReader,PdfWriter
from app.core.config import settings
from app.schemas.contracts import ActionCommand
from app.schemas.document_uploads import DocumentMime
from app.services.document_parser_process import parse
from tests.data_api_support import NativeAPI
from tests.document_format_support import address_lines,docx_bytes,scan_jpeg,scan_pdf,scan_png
from tests.storage_support import local_storage,template_pdf
from tests.test_actions_api import context,headers,owned_case,propose
from tests.test_document_uploads import upload_target

DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
native=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native PostgreSQL/PostgREST')


@native
@pytest.mark.parametrize('kind,extension,expected,extraction,pages',[
    ('complete','docx','no_issues','text',None),('complete','png','no_issues','ocr',1),
    ('complete','jpg','no_issues','ocr',1),('complete','pdf','no_issues','ocr',1),
    ('blank','docx','unable_to_assess','unavailable',None),('blank','png','unable_to_assess','unavailable',None),
    ('low_resolution','png','unable_to_assess','unavailable',None),
    ('old_due','docx','issues_found','text',None),('future','docx','issues_found','text',None),
    ('ambiguous','docx','issues_found','text',None),
    ('max_bytes','pdf','no_issues','text',1),
    ('encrypted','pdf','unable_to_assess','unavailable',None),('page_limit','pdf','unable_to_assess','unavailable',None),
    ('mismatch','png','unable_to_assess','unavailable',None),('corrupt','docx','unable_to_assess','unavailable',None),
    ('blank_pages','pdf','unable_to_assess','unavailable',2),
])
def test_formats_preserve_check_and_bank_state(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,kind: str,extension: str,expected: str,extraction: str,pages: int | None) -> None:
    if extraction=='ocr' and not settings.document_ocr_command:pytest.skip('Configure an actual local OCR helper')
    today=datetime.now(ZoneInfo('Asia/Singapore')).date();lines=address_lines(today)
    if kind=='blank':lines=[]
    elif kind=='old_due':lines=[*address_lines(today-timedelta(days=365)),'Please pay by '+today.strftime('%d %b %Y')]
    elif kind=='future':lines=[*address_lines(today+timedelta(days=1)),'Bill period: '+today.strftime('%d %b %Y')]
    elif kind=='ambiguous':lines=[*lines[:-1],'Bill date: 01/02/'+str(today.year)]
    encoders: dict[str,tuple[DocumentMime,Callable[[list[str]],bytes]]]={
        'docx':(DOCX,docx_bytes),'png':('image/png',scan_png),'jpg':('image/jpeg',scan_jpeg),'pdf':('application/pdf',scan_pdf)}
    mime,encode=encoders[extension];payload=encode(lines)
    if kind=='low_resolution':
        output=BytesIO()
        with Image.open(BytesIO(payload)) as image:image.resize((90,70)).save(output,'PNG')
        payload=output.getvalue()
    if kind=='max_bytes':
        content=template_pdf(lines,1);payload=content+b' '*(10485760-len(content))
    elif kind=='encrypted':
        writer=PdfWriter();writer.append(PdfReader(BytesIO(template_pdf(lines,1))));writer.encrypt('synthetic-only')
        output=BytesIO();writer.write(output);payload=output.getvalue()
    elif kind=='page_limit':payload=template_pdf(lines,21)
    elif kind=='mismatch':payload=scan_jpeg(lines)
    elif kind=='corrupt':payload=b'PK\x03\x04synthetic invalid archive'
    elif kind=='blank_pages':
        writer=PdfWriter()
        for _ in range(2):writer.add_blank_page(width=612,height=792)
        output=BytesIO();writer.write(output);payload=output.getvalue()
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    target.update(file_name=f'Synthetic {kind}.{extension}',mime_type=mime,size_bytes=len(payload))
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set label='Proof of residential address',instructions='A utility bill dated within the last 3 months.' where item_id=%s",(target['item_id'],))
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'precheck_mode','template');monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key))
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        uploaded=httpx.post(url+f"/storage/v1/object/demobank-compliance-private/1002/{target['case_id']}/{target['document_id']}",
            headers={**headers(ctx),'Content-Type':mime},content=payload)
        assert uploaded.status_code==200
        saved=propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:format:document.register',target=target))
        assert saved.receipt.outcome=='succeeded'
        checked=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
        assert checked.status_code==200,checked.text
        report=checked.json()['data']
        assert (report['status'],report['extraction'],report['page_count'])==(expected,extraction,pages),report
        assert '560123' not in checked.text and 'Example Avenue' not in checked.text
        submitted=native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx))
        assert submitted.status_code==(200 if expected=='no_issues' else 422),submitted.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select submission_status from compliance.requested_items where item_id=%s',(target['item_id'],)).fetchone()==(('submitted_awaiting_review' if expected=='no_issues' else 'not_submitted'),)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@native
@pytest.mark.parametrize('size,length,status',[(0,20,422),(1,20,200),(10485760,200,200),(10485761,20,422),(1024,201,422)])
def test_upload_descriptor_boundaries(local_data_api: NativeAPI,native_api_client: TestClient,size: int,length: int,status: int) -> None:
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    target.update(file_name='x'*(length-4)+'.pdf',mime_type='application/pdf',size_bytes=size)
    response=native_api_client.post('/documents/uploads/prepare',headers=headers(ctx),json=target)
    assert response.status_code==status,response.text


def test_exact_image_and_pdf_limits_use_real_bytes() -> None:
    for dimensions,status in [((4000,4000),'image_decoded'),((4000,4001),'limits_exceeded')]:
        output=BytesIO();Image.new('L',dimensions,255).save(output,'PNG')
        assert asyncio.run(parse(output.getvalue(),'image/png')).status==status
    payload=template_pdf(['Synthetic DEMO upload fixture'],1)
    exact=payload+b' '*(10485760-len(payload))
    assert asyncio.run(parse(exact,'application/pdf')).status=='text_extracted'
    assert asyncio.run(parse(exact+b' ','application/pdf')).status=='limits_exceeded'
    assert asyncio.run(parse(template_pdf(['Synthetic DEMO upload fixture'],20),'application/pdf')).page_count==20
    assert asyncio.run(parse(template_pdf(['Synthetic DEMO upload fixture'],21),'application/pdf')).status=='limits_exceeded'
