"""Real document bytes, scoped compliance help, RM approval, client receipts and retry isolation."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from tests.test_document_templates import BILL


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native JWT/RLS Data API and storage')
def test_failed_replacement_and_rm_follow_up_keep_submission_and_check_distinct(local_data_api,native_api_client,tmp_path,monkeypatch):
    import httpx
    import psycopg
    from pydantic import SecretStr
    from app.core.config import settings
    from app.schemas.contracts import ActionCommand
    from tests.storage_support import local_storage,template_pdf
    from tests.test_actions_api import context,headers,owned_case,propose
    from tests.test_document_uploads import upload_target
    from uuid import UUID,uuid4
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    today=datetime.now(ZoneInfo('Asia/Singapore')).date()
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set label='Proof of residential address',instructions='A utility bill dated within the last 3 months.' where item_id=%s",(target['item_id'],))
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'precheck_mode','template')
    monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key))
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        for complete in (True,False):
            lines=[line for line in BILL[:5] if complete or 'Singapore' not in line]
            payload=template_pdf([*lines,'Bill date: '+today.strftime('%d %b %Y')])
            upload={**target,'document_id':str(uuid4()),'file_name':'Synthetic replacement.pdf','mime_type':'application/pdf','size_bytes':len(payload)}
            uploaded=httpx.post(url+f"/storage/v1/object/demobank-compliance-private/1002/{target['case_id']}/{upload['document_id']}",
                headers={**headers(ctx),'Content-Type':'application/pdf'},content=payload)
            assert uploaded.status_code==200
            registered=propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f"{ctx.case_id}:{upload['document_id']}:document.register",target=upload))
            assert registered.receipt.outcome=='succeeded'
            checked=native_api_client.post(f"/documents/{upload['document_id']}/precheck",headers=headers(ctx))
            assert checked.status_code==200,checked.text
            submitted=native_api_client.post(f"/documents/{upload['document_id']}/submit",headers=headers(ctx))
            assert submitted.status_code==(200 if complete else 422),submitted.text
            if not complete:
                workspace=native_api_client.get('/documents/workspace',headers=headers(ctx)).json()['data']
                item=next(item for item in workspace['review']['requested_items'] if item['item_id']==target['item_id'])
                assert item['submission_status']=='submitted_awaiting_review'
                assert workspace['current_documents'][target['item_id']]==upload['document_id']
                report=next(report for report in workspace['checks'] if report['document_id']==upload['document_id'])
                assert report['status']=='issues_found' and report['findings'][0]['code']=='missing_field'
                assert len([doc for doc in workspace['documents'] if doc['item_id']==target['item_id']])==2
        advisor=native_api_client.get('/customer/advisor',headers=headers(ctx))
        assert advisor.status_code==200,advisor.text
        scope_id=uuid4()
        prepared=native_api_client.post('/documents/support',headers=headers(ctx),json={'session_id':str(ctx.session_id),
            'case_id':str(scope_id),'review_case_id':target['case_id'],'item_id':target['item_id']})
        assert prepared.status_code==200,prepared.text
        summary=prepared.json()['data']['summary']
        question='Please explain this missing-address finding. My OTP is 123456.'
        before={ticket['reference'] for ticket in native_api_client.get('/tickets',headers=headers(ctx)).json()['data']}
        scoped=ctx.model_copy(update={'case_id':scope_id})
        handoff=propose(native_api_client,scoped,ActionCommand(type='handoff.create',idempotency_key=f'{scope_id}:review-help:handoff.create',
            target={'reason':'general_support','priority':'P2','summary':summary}))
        assert handoff.receipt.outcome=='succeeded',handoff.model_dump_json()
        tickets=native_api_client.get('/tickets',headers=headers(ctx)).json()['data']
        ticket=next(ticket for ticket in tickets if ticket['reference'] not in before)
        follow_url='/tickets/'+ticket['reference']+'/follow-up'
        followed=native_api_client.post(follow_url,headers=headers(ctx),json={'message':question})
        assert followed.status_code==200 and followed.json()['added'],followed.text
        repeated=native_api_client.post(follow_url,headers=headers(ctx),json={'message':question})
        assert repeated.status_code==200 and not repeated.json()['added'],repeated.text
        assert native_api_client.post(follow_url,headers=headers(context(local_data_api,1003)),json={'message':question}).status_code==403
        assert native_api_client.post(follow_url,headers=headers(ctx),json={'message':'x'*481}).status_code==422
        import time
        import jwt
        from tests.database_support import SOPHIE_LOGIN
        token=jwt.encode({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aud':'authenticated','aal':'aal1','session_id':str(uuid4()),
            'exp':int(time.time())+3600,'app_metadata':{'demobank_role':'rm','rm_id':'00000000-0000-4000-8000-100200000002'}},
            local_data_api.signing_key,algorithm='ES256',headers={'kid':'native-test-key'})
        rm_headers={'Authorization':'Bearer '+token}
        detail=native_api_client.get('/rm/tickets/'+ticket['reference'],headers=rm_headers)
        assert detail.status_code==200,detail.text
        assert detail.json()['data']['handover_context']==summary and 'missing_field' in summary
        assert any('Please explain this missing-address finding.' in row['note'] for row in detail.json()['data']['told_by_bank'] if row['note'])
        assert '123456' not in detail.text
        message_id=uuid4();message='Please upload a complete PDF showing your address. It will still need the bank review.'
        approval={'message_id':str(message_id),'customer_id':1002,'reference':ticket['reference'],'body':message,
            'status':'waiting_on_client','draft_revision':1,'edited':False}
        assert native_api_client.post('/rm/messages',headers=headers(ctx),json=approval).status_code==403
        for _ in range(2):
            sent=native_api_client.post('/rm/messages',headers=rm_headers,json=approval)
            assert sent.status_code==200,sent.text
        updated=native_api_client.get('/tickets',headers=headers(ctx)).json()['data']
        mine=next(row for row in updated if row['reference']==ticket['reference'])
        assert mine['status']=='waiting_on_client' and mine['last_update']['note']==message
        inbox=native_api_client.get('/notifications',headers=headers(ctx))
        assert inbox.status_code==200 and 'Update from your relationship manager' in inbox.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from rm.client_messages where message_id=%s',(message_id,)).fetchone()==(1,)
        assert db.execute('select document_id from compliance.support_context where action_case_id=%s',(scope_id,)).fetchone()==(UUID(upload['document_id']),)
        assert db.execute("select count(*) from actions.receipts r join actions.commands c using(command_id) where c.type='document.submit'").fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
