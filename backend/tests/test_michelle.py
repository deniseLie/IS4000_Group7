"""Real SQL/API attestation, lifecycle and isolation. Signed samples are not AI or blob checks."""
import asyncio
import hashlib
import hmac
import os
from datetime import datetime,timezone
from uuid import UUID,uuid4
import pytest
from app.schemas.contracts import ActionCommand
from app.schemas.prechecks import PrecheckReport,Finding
from app.services import prechecks
from tests.test_actions_api import context,headers,owned_case,propose
from tests.test_document_uploads import object_metadata,upload_target

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native JWT/RLS Data API')


def registered(api,client,cid=1002):
    import psycopg
    ctx=owned_case(api,context(api,cid));target=upload_target(api,cid);object_metadata(api,cid,target)
    with psycopg.connect(api.database_uri) as db:
        db.execute("update compliance.requested_items set precheck_rule_id='proof_address' where item_id=%s",(target['item_id'],))
    outcome=propose(client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:register:document.register',target=target))
    assert outcome.receipt.outcome=='succeeded'
    return ctx,target


def signed_report(api,target,cid=1002,status='no_issues'):
    import psycopg
    from postgrest import SyncPostgrestClient
    from datetime import timedelta
    ctx=context(api,cid)
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        source=db.rpc('customer_precheck_context',{'p_customer_id':cid,'p_document_id':target['document_id']}).execute().data['data']
    checked=datetime.now(timezone.utc)
    report=PrecheckReport(scope='notice_based',requirements_hash=source['requirements_hash'],check_id=uuid4(),document_id=target['document_id'],case_id=target['case_id'],item_id=target['item_id'],
        customer_id=cid,rule_id=source['rule']['rule_id'],rule_version=1,sha256=hashlib.sha256(b'synthetic checked bytes').hexdigest(),
        status=status,findings=[] if status=='no_issues' else [Finding(code='missing_field',field='Address')],page_count=1,
        extraction='text',checked_at=checked,expires_at=checked+timedelta(hours=source['policy']['validity_hours']))
    payload=report.model_dump_json()
    with psycopg.connect(api.database_uri) as db:key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    return report,payload,hmac.new(key.encode(),payload.encode(),hashlib.sha256).hexdigest()


def record(api,ctx,payload,signature):
    from postgrest import SyncPostgrestClient
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        return db.rpc('document_precheck_record',{'p_payload':payload,'p_signature':signature}).execute().data


def test_signature_prevents_browser_forgery_and_cross_customer_reads(local_data_api,native_api_client):
    from postgrest.exceptions import APIError
    from postgrest import SyncPostgrestClient
    ctx,target=registered(local_data_api,native_api_client)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError):db.schema('compliance').from_('precheck_keys').select('*').execute()
    report,payload,signature=signed_report(local_data_api,target)
    with pytest.raises(APIError):record(local_data_api,ctx,payload,'0'*64)
    with pytest.raises(APIError):record(local_data_api,context(local_data_api,1001),payload,signature)
    result=record(local_data_api,ctx,payload,signature)
    assert result['data']['check_id']==str(report.check_id)
    assert record(local_data_api,ctx,payload,signature)['data']==result['data']
    for cid in [1001,1003]:
        assert native_api_client.get(f"/documents/{target['document_id']}/precheck",headers=headers(context(local_data_api,cid))).json()['data'] is None
    assert native_api_client.get('/documents/workspace').status_code==401


def test_submission_is_receipted_idempotent_and_never_bank_approval(local_data_api,native_api_client):
    import psycopg
    ctx,target=registered(local_data_api,native_api_client)
    report,payload,signature=signed_report(local_data_api,target);record(local_data_api,ctx,payload,signature)
    url=f"/documents/{target['document_id']}/submit"
    first=native_api_client.post(url,headers=headers(ctx));assert first.status_code==200,first.text
    saved=first.json();assert saved['receipt']['outcome']=='succeeded'
    assert native_api_client.post(url,headers=headers(ctx)).json()['receipt']==saved['receipt']
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select submission_status from compliance.requested_items where item_id=%s',(target['item_id'],)).fetchone()==('submitted_awaiting_review',)
        assert db.execute('select status from compliance.cases where case_id=%s',(target['case_id'],)).fetchone()==('review_opened',)
        assert db.execute("select count(*) from actions.receipts r join actions.commands c using(command_id) where c.type='document.submit'").fetchone()==(1,)
        delivered=db.execute("select notify.deliver(e) from events.outbox e where event_type='document.submitted'").fetchone()[0]
        assert delivered['disposition']=='delivered'
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@pytest.mark.parametrize('change',['new_upload','accepted','closed','outbox'])
def test_changed_targets_and_rollback_cannot_submit_old_findings(local_data_api,native_api_client,change):
    import psycopg
    ctx,target=registered(local_data_api,native_api_client)
    report,payload,signature=signed_report(local_data_api,target);record(local_data_api,ctx,payload,signature)
    with psycopg.connect(local_data_api.database_uri) as db:
        if change=='new_upload':
            db.execute('update compliance.requested_items set current_document_id=null where item_id=%s',(target['item_id'],))
        elif change=='accepted':db.execute("update compliance.requested_items set submission_status='accepted' where item_id=%s",(target['item_id'],))
        elif change=='closed':db.execute("update compliance.cases set status='closed' where case_id=%s",(target['case_id'],))
        else:db.execute("alter table events.outbox add constraint no_submission check(event_type<>'document.submitted')")
    result=native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx))
    if change=='outbox':assert result.status_code==503
    else:assert result.status_code==200 and result.json()['receipt']['outcome']=='failed',result.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select submission_status from compliance.requested_items where item_id=%s',(target['item_id'],)).fetchone()[0]==('accepted' if change=='accepted' else 'not_submitted')
        if change=='outbox':assert db.execute("select count(*) from actions.commands where type='document.submit'").fetchone()==(0,)


def test_incomplete_checks_and_unconfigured_keys_never_claim_submission(local_data_api,native_api_client):
    ctx,target=registered(local_data_api,native_api_client)
    report,payload,signature=signed_report(local_data_api,target,status='issues_found');record(local_data_api,ctx,payload,signature)
    response=native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx))
    assert response.status_code==422
    workspace=native_api_client.get('/documents/workspace',headers=headers(ctx))
    assert workspace.status_code==200,workspace.text
    assert workspace.json()['data']['checks'][0]['status']=='issues_found'
    assert 'synthetic checked bytes' not in workspace.text


def test_missing_private_key_or_rule_cannot_record_a_passing_result(local_data_api,native_api_client,monkeypatch):
    import psycopg
    from app.core.config import settings
    ctx,target=registered(local_data_api,native_api_client)
    monkeypatch.setattr(settings,'document_precheck_signing_key',None)
    response=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
    assert response.status_code==503
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from compliance.prechecks').fetchone()==(0,)
        db.execute('update compliance.requested_items set precheck_rule_id=null where item_id=%s',(target['item_id'],))
    response=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
    assert response.status_code==503
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select precheck_rule_id from compliance.requested_items where item_id=%s',(target['item_id'],)).fetchone()==('notice',)
        assert db.execute("select submission_status from compliance.requested_items where item_id=%s",(target['item_id'],)).fetchone()==('not_submitted',)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@pytest.mark.skipif(os.getenv('TEST_MICHELLE_LIVE')!='1',reason='Real document interpretation provider opt-in')
@pytest.mark.parametrize('rule',['proof_address','wealth_declaration','bank_statements','tax_residency'])
def test_actual_bytes_precheck_correction_and_submission_over_http(local_data_api,native_api_client,tmp_path,monkeypatch,rule):
    import httpx
    import psycopg
    from datetime import date
    from pydantic import SecretStr
    from app.core.config import settings
    from tests.storage_support import local_storage,template_pdf
    if os.getenv('TEST_MICHELLE_TRANSPORT')=='codex':monkeypatch.setenv('CHAT_TRANSPORT','codex')
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    with psycopg.connect(local_data_api.database_uri) as db:
        instruction={'proof_address':'Full name, address and document date within the last 3 months.', 'wealth_declaration':'Full name, source of wealth and signature.', 'tax_residency':'Full name, country of tax residence and signature.', 'bank_statements':'Full name and bank name. Statements for the last 3 months.'}[rule]
        db.execute("update compliance.requested_items set precheck_rule_id=%s,label=%s,instructions=%s where item_id=%s",(rule,'Requested evidence',instruction,target['item_id']))
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key))
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        for valid in [False,True]:
            from zoneinfo import ZoneInfo
            today=datetime.now(ZoneInfo('Asia/Singapore')).date();lines=['Full name: Synthetic Demo'];last=''
            if rule=='proof_address':lines+=['Document date: '+today.isoformat()];last='Address: Fictional Lane'
            elif rule in ['wealth_declaration','tax_residency']:
                lines+=['Declaration date: '+today.isoformat(),'Source of wealth: Synthetic demo' if rule=='wealth_declaration' else 'Country of tax residence: Fictional country'];last='Signature entry: Synthetic demo'
            else:
                lines+=['Consolidated bank statement']
                for offset in (1,2,3):
                    year,month=divmod(today.year*12+today.month-1-offset,12);lines.append(f'Statement period: {year:04d}-{month+1:02d}')
                last='Bank name: Fictional bank'
            if valid:lines.append(last)
            payload=template_pdf(lines,3 if rule=='bank_statements' else 1)
            target={**target,'document_id':str(uuid4()),'file_name':'Synthetic Address.pdf','mime_type':'application/pdf','size_bytes':len(payload)}
            path=f"1002/{target['case_id']}/{target['document_id']}"
            uploaded=httpx.post(url+'/storage/v1/object/demobank-compliance-private/'+path,headers={**headers(ctx),'Content-Type':'application/pdf'},content=payload)
            assert uploaded.status_code==200,uploaded.text
            propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:{uuid4().hex}:document.register',target=target))
            checked=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
            assert checked.status_code==200,checked.text
            assert checked.json()['data']['status']==('no_issues' if valid else 'issues_found'),checked.json()['data']['findings']
            assert checked.json()['data']['sha256']==hashlib.sha256(payload).hexdigest()
            assert 'Fictional Lane' not in checked.text
            submitted=native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx))
            assert submitted.status_code==(200 if valid else 422),submitted.text
        view=native_api_client.get('/documents/workspace',headers=headers(ctx));assert view.status_code==200,view.text
        assert len(view.json()['data']['checks'])==1
        with psycopg.connect(local_data_api.database_uri) as db:
            assert db.execute('select count(*) from compliance.prechecks').fetchone()==(2,)
            assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@pytest.mark.skipif(os.getenv('TEST_MICHELLE_LIVE')!='1',reason='Real document interpretation provider opt-in')
@pytest.mark.parametrize('outdated',[False,True])
def test_expired_check_is_blocked_in_api_and_sql_and_reassesses_real_bytes(local_data_api,native_api_client,tmp_path,monkeypatch,outdated):
    import calendar
    import httpx
    import psycopg
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from pydantic import SecretStr
    from app.core.config import settings
    from tests.storage_support import local_storage,template_pdf
    if os.getenv('TEST_MICHELLE_TRANSPORT')=='codex':monkeypatch.setenv('CHAT_TRANSPORT','codex')
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    today=datetime.now(ZoneInfo('Asia/Singapore')).date()
    year,month=divmod(today.year*12+today.month-1-3,12)
    boundary=today.replace(year=year,month=month+1,day=min(today.day,calendar.monthrange(year,month+1)[1]))
    recorded=boundary-timedelta(days=1) if outdated else today
    payload=template_pdf(['Utility bill','Full name: Synthetic Demo','Address: Fictional Lane','Document date: '+recorded.isoformat()])
    target.update(file_name='Synthetic freshness.pdf',mime_type='application/pdf',size_bytes=len(payload))
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set precheck_rule_id='proof_address',label='Proof of address',instructions='Full name, address and document date within the last 3 months.' where item_id=%s",(target['item_id'],))
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key))
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        uploaded=httpx.post(url+f"/storage/v1/object/demobank-compliance-private/1002/{target['case_id']}/{target['document_id']}",headers={**headers(ctx),'Content-Type':'application/pdf'},content=payload)
        assert uploaded.status_code==200
        propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:upload:document.register',target=target))
        report,_,_=signed_report(local_data_api,target)
        report=report.model_copy(update={'sha256':hashlib.sha256(payload).hexdigest()})
        serialized=report.model_dump_json();signature=hmac.new(key.encode(),serialized.encode(),hashlib.sha256).hexdigest()
        record(local_data_api,ctx,serialized,signature)
        # Owner-controlled fixture establishes yesterday's stored report; no model is replaced.
        with psycopg.connect(local_data_api.database_uri) as db:
            db.execute("update compliance.prechecks set report=jsonb_set(jsonb_set(report,'{checked_at}',to_jsonb(clock_timestamp()-interval '2 days')),'{expires_at}',to_jsonb(clock_timestamp()-interval '1 day')) where check_id=%s",(report.check_id,))
        assert native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx)).json()['error']['code']=='precheck_expired'
        bypass=propose(native_api_client,ctx,ActionCommand(type='document.submit',idempotency_key=f'{ctx.case_id}:bypass:document.submit',target={'document_id':target['document_id'],'check_id':str(report.check_id)}))
        assert bypass.receipt.outcome=='failed'
        refreshed=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
        assert refreshed.status_code==200,refreshed.text
        value=refreshed.json()['data'];assert value['check_id']!=str(report.check_id)
        assert value['status']==('issues_found' if outdated else 'no_issues'),value['findings']
        if outdated:assert any(f['code']=='date_outdated' for f in value['findings'])
        repeated=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx)).json()['data']['check_id']
        assert (repeated==value['check_id']) is (not outdated)
        workspace=native_api_client.get('/documents/workspace',headers=headers(ctx)).json()['data']
        assert len(workspace['checks'])==1 and workspace['checks'][0]['check_id']==repeated
        result=native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx))
        assert result.status_code==(422 if outdated else 200),result.text
        with psycopg.connect(local_data_api.database_uri) as db:
            assert db.execute('select count(*) from compliance.prechecks').fetchone()==(3 if outdated else 2,)
            assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_changed_notice_invalidates_api_workspace_and_direct_sql_submission(local_data_api,native_api_client):
    import psycopg
    ctx,target=registered(local_data_api,native_api_client)
    report,payload,signature=signed_report(local_data_api,target);record(local_data_api,ctx,payload,signature)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set instructions='Provide a utility bill within the last 2 months.' where item_id=%s",(target['item_id'],))
    assert native_api_client.get(f"/documents/{target['document_id']}/precheck",headers=headers(ctx)).json()['data'] is None
    assert native_api_client.get('/documents/workspace',headers=headers(ctx)).json()['data']['checks']==[]
    assert native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx)).status_code==422
    bypass=propose(native_api_client,ctx,ActionCommand(type='document.submit',idempotency_key=f'{ctx.case_id}:oldnotice:document.submit',target={'document_id':target['document_id'],'check_id':str(report.check_id)}))
    assert bypass.receipt.outcome=='failed'
    from postgrest.exceptions import APIError
    with pytest.raises(APIError):record(local_data_api,ctx,payload,signature)
    new,payload,signature=signed_report(local_data_api,target)
    assert new.requirements_hash!=report.requirements_hash
    record(local_data_api,ctx,payload,signature)
    assert native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx)).json()['receipt']['outcome']=='succeeded'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from compliance.prechecks').fetchone()==(2,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_inability_attempt_can_be_retried_without_uploading_again(local_data_api,native_api_client):
    import psycopg
    ctx,target=registered(local_data_api,native_api_client)
    _,payload,signature=signed_report(local_data_api,target,status='unable_to_assess');record(local_data_api,ctx,payload,signature)
    report,payload,signature=signed_report(local_data_api,target);record(local_data_api,ctx,payload,signature)
    assert native_api_client.get(f"/documents/{target['document_id']}/precheck",headers=headers(ctx)).json()['data']['check_id']==str(report.check_id)
    assert native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx)).json()['receipt']['outcome']=='succeeded'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from compliance.prechecks').fetchone()==(2,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
