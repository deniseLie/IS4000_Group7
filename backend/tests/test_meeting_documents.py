"""Actual authenticated PDF bytes from saved SQL documents, including sharing, stale-version and Unicode boundaries."""
from io import BytesIO
import os
from pathlib import Path
from uuid import uuid4
import httpx,psycopg
from pypdf import PdfReader
from tests.test_meeting_lifecycle import flow,headers
from tests.test_meeting_packs import setup_pack,pack_inputs,approval


def test_pdf_pack_uses_saved_terms_and_enforces_sharing_and_exact_version(flow,local_data_api):
    initial,body=setup_pack(flow,local_data_api)
    amounts=[{**body['p_amounts'][0],'label':'Budget <review> 中文'}]
    inputs=pack_inputs(flow,local_data_api,amounts)
    stored=httpx.post(local_data_api.url+'/rpc/meeting_save_pack',headers=headers(flow,'rm'),json={
        **body,'p_amounts':amounts,'p_source_hash':inputs['source_hash'],'p_command_id':str(uuid4()),
        'p_plan':{**body['p_plan'],'agenda':['goals','product_terms','next_steps'],'questions':['review_catalogue_product']}})
    assert stored.status_code==200,stored.text
    pack=stored.json();query={key:pack[key] for key in ('document_id','version','content_hash')}
    url=f'/meetings/{flow.meeting}/pack/pdf'
    draft=flow.http.get(url,headers=headers(flow,'rm'),params=query)
    assert draft.status_code==200,draft.text[:100]
    assert draft.headers['content-type']=='application/pdf' and draft.content.startswith(b'%PDF-')
    assert draft.headers['cache-control']=='private, no-store' and draft.headers['content-disposition'].endswith('.pdf"')
    reader=PdfReader(BytesIO(draft.content));text='\n'.join(page.extract_text() for page in reader.pages)
    assert '3.1234%' in text and '12,345.67' in text and '99,999,999,999,999.99' in text
    assert 'Budget <review> 中文' in text and 'draft' in text
    assert 'Review the listed product terms' in text
    assert 'Which terms of the listed product would you like to review' in text
    assert str(flow.meeting) not in text and pack['document_id'] not in text and pack['content_hash'] not in text
    assert flow.http.get(url,headers=headers(flow,'client'),params=query).status_code==404
    assert flow.http.get(url,headers=headers(flow,'outsider'),params=query).status_code==403
    assert flow.http.get(url,headers=headers(flow,'rm'),params={**query,'content_hash':'0'*64}).status_code==409
    assert flow.http.get(url,headers=headers(flow,'rm'),params={**query,'version':0}).status_code==422
    for suffix in ('approve','share'):
        response=flow.http.post(f'/meetings/{flow.meeting}/pack/{suffix}',headers=headers(flow,'rm'),json=approval(pack))
        assert response.status_code==200,response.text
    visible=flow.http.get(url,headers=headers(flow,'client'),params=query)
    assert visible.status_code==200 and visible.content.startswith(b'%PDF-')
    preview=os.environ.get('TEST_MEETING_PDF_PREVIEW')
    if preview:Path(preview).write_bytes(visible.content)
    with psycopg.connect(flow.uri) as db:db.execute("update meetings.catalogue_terms set annual_rate=4.5678,version=3 where code='demo-fixed-deposit-sgd'")
    assert flow.http.get(url,headers=headers(flow,'client'),params=query).status_code==409
