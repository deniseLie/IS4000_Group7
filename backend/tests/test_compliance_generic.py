"""Generic notices, configured policy, exact selections and claimed-staff integration."""
import asyncio
import os
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from pydantic import ValidationError
from app.core.errors import ApiError
from app.schemas.chat import ChatRequest,RecordSelection
from app.schemas.contracts import ActionCommand
from app.services.compliance_selection import validate
from app.services.prechecks import is_fresh
from scripts.prepare_compliance_notice import Notice,render


def test_selected_record_requires_exact_current_owned_document_and_checkpoint_roundtrip():
    ids=[uuid4() for _ in range(4)]
    selected=RecordSelection(kind='document',case_id=ids[0],item_id=ids[1],document_id=ids[2],check_id=ids[3])
    request=ChatRequest(mode='personal',messages=[{'role':'user','content':'Explain this check'}],record_context=selected)
    assert ChatRequest.model_validate_json(request.model_dump_json()).record_context==selected
    from app.services.checkpoint_codec import CheckpointCodec
    codec=CheckpointCodec();assert codec.loads_typed(codec.dumps_typed(request)).record_context==selected
    view=SimpleNamespace(review=SimpleNamespace(case_id=ids[0]),current_documents={ids[1]:ids[2]},checks=[selected])
    validate(selected,view)
    view.current_documents[ids[1]]=uuid4()
    with pytest.raises(ApiError):validate(selected,view)
    with pytest.raises(ValidationError):ChatRequest(messages=request.messages,record_context=selected)
    with pytest.raises(ValidationError):RecordSelection(kind='information_request',case_id=ids[0],request_id=uuid4(),item_id=ids[1])


def test_check_freshness_uses_explicit_policy_expiry_instead_of_local_midnight():
    now=datetime(2026,10,24,16,1,tzinfo=timezone.utc)
    report=SimpleNamespace(checked_at=now-timedelta(minutes=2),expires_at=now+timedelta(hours=1))
    assert is_fresh(report,now)
    assert not is_fresh(report,report.expires_at)
    assert not is_fresh(report,now-timedelta(hours=2))


def notice():
    return Notice(synthetic=True,customer_id=1001,title="Synthetic owner's $notice$ review",notice_text='Please update the listed information.',
        notice_date='2026-10-24',items=[dict(label='Business registration',instructions="Provide the business registration number. $notice$ ' ; DROP TABLE compliance.cases; --")])


def test_notice_import_is_synthetic_only_and_does_not_use_a_fixed_sql_delimiter():
    n=notice();sql=render(n)
    assert 'do $notice_' in sql and "owner''s" in sql
    with pytest.raises(ValueError):render(n.model_copy(update={'synthetic':False}))


native=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Native JWT/RLS Data API')


@native
def test_new_customer_notice_gets_generic_checks_and_policy_changes_invalidate_attestation(local_data_api,native_api_client):
    import psycopg
    from tests.test_actions_api import context,headers
    from tests.test_michelle import registered,signed_report,record
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute(render(notice()))
        assert db.execute("select precheck_rule_id from compliance.requested_items where label='Business registration'").fetchone()==('notice',)
    # Another customer's existing document uses the same generic policy without persona activation.
    ctx,target=registered(local_data_api,native_api_client,1001)
    old,payload,sig=signed_report(local_data_api,target,1001);record(local_data_api,ctx,payload,sig)
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("update compliance.check_policy set validity_hours=48,status_stale_days=2,numeric_date_order='day_first'")
    workspace=native_api_client.get('/documents/workspace',headers=headers(ctx)).json()['data']
    assert workspace['policy']['validity_hours']==48 and workspace['policy']['status_stale_days']==2
    assert native_api_client.get(f"/documents/{target['document_id']}/precheck",headers=headers(ctx)).json()['data'] is None
    fresh,payload,sig=signed_report(local_data_api,target,1001);record(local_data_api,ctx,payload,sig)
    assert fresh.requirements_hash!=old.requirements_hash
    assert (fresh.expires_at-fresh.checked_at)==timedelta(hours=48)


@native
def test_newer_failed_assessment_blocks_old_positive_and_forced_checks_remain_immutable(local_data_api,native_api_client):
    from tests.test_actions_api import context,propose,headers
    from tests.test_michelle import registered,signed_report,record
    ctx,target=registered(local_data_api,native_api_client)
    positive,payload,sig=signed_report(local_data_api,target);record(local_data_api,ctx,payload,sig)
    negative,payload,sig=signed_report(local_data_api,target,status='issues_found');record(local_data_api,ctx,payload,sig)
    result=propose(native_api_client,ctx,ActionCommand(type='document.submit',idempotency_key=f'{ctx.case_id}:old-positive:document.submit',target={'document_id':target['document_id'],'check_id':str(positive.check_id)}))
    assert result.receipt.outcome=='failed'
    current=native_api_client.get(f"/documents/{target['document_id']}/precheck",headers=headers(ctx)).json()['data']
    assert current['check_id']==str(negative.check_id)


@native
def test_scoped_support_is_owned_idempotent_and_only_readable_by_claiming_mfa_staff(local_data_api,native_api_client):
    import psycopg
    from postgrest import SyncPostgrestClient
    from postgrest.exceptions import APIError
    from app.services.actions import staff
    from tests.test_staff_support import staff_user,staff_token
    from tests.test_actions_api import context,headers,propose
    from tests.test_michelle import registered,signed_report,record
    ctx,target=registered(local_data_api,native_api_client)
    _,payload,sig=signed_report(local_data_api,target,status='issues_found');record(local_data_api,ctx,payload,sig)
    selected=dict(session_id=str(ctx.session_id),case_id=str(uuid4()),review_case_id=target['case_id'],item_id=target['item_id'])
    response=native_api_client.post('/documents/support',headers=headers(ctx),json=selected)
    assert response.status_code==200,response.text
    summary=response.json()['data']['summary'];assert 'missing_field' in summary and 'issues_found' in summary
    assert native_api_client.post('/documents/support',headers=headers(ctx),json=selected).json()['data']==response.json()['data']
    denied=native_api_client.post('/documents/support',headers=headers(context(local_data_api,1003)),json={**selected,'case_id':str(uuid4())})
    assert denied.status_code!=200
    scoped=ctx.model_copy(update={'case_id':UUID(selected['case_id'])})
    outcome=propose(native_api_client,scoped,ActionCommand(type='handoff.create',idempotency_key=f'{scoped.case_id}:support:handoff.create',target={'reason':'general_support','priority':'P2','summary':summary}))
    hid=outcome.receipt.result.handoff_id
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError):db.rpc('staff_compliance_context',{'p_handoff_id':str(hid)}).execute()
        user=staff_user(local_data_api);db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        with pytest.raises(APIError):db.rpc('staff_compliance_context',{'p_handoff_id':str(hid)}).execute()
        assert asyncio.run(staff.claim(uuid4(),hid,db)).receipt.outcome=='succeeded'
        details=db.rpc('staff_compliance_context',{'p_handoff_id':str(hid)}).execute().data
        assert details['item']['item_id']==target['item_id'] and details['precheck']['status']=='issues_found'
        assert 'storage_path' not in str(details)
        db.auth(staff_token(local_data_api,staff_user(local_data_api),'aal2',{'demobank_role':'support'}))
        with pytest.raises(APIError):db.rpc('staff_compliance_context',{'p_handoff_id':str(hid)}).execute()
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_document_model_evidence_excludes_internal_requirement_references():
    from app.services.agents.document import report_evidence
    from app.services.checkpoint_codec import CheckpointCodec
    from app.schemas.prechecks import PrecheckReport,NoticeRequirement
    now=datetime.now(timezone.utc)
    report=PrecheckReport(check_id=uuid4(),document_id=uuid4(),case_id=uuid4(),item_id=uuid4(),customer_id=1002,rule_id='notice',rule_version=1,sha256='a'*64,extraction='text',status='no_issues',findings=[],checked_at=now,expires_at=now+timedelta(hours=24),requirements=[NoticeRequirement(reference='5210670325573566',quote='Proof of residential address',kind='presence')])
    import json
    evidence=json.dumps(report_evidence(report))
    assert '5210670325573566' not in evidence and 'reference' not in evidence
    assert CheckpointCodec().dumps_typed(evidence)


from tests.test_notifications import worker_uri


@native
@pytest.mark.parametrize('service',[False,True])
def test_policy_controls_reminder_window_preferences_and_approved_locale_fallback(local_data_api,native_api_client,worker_uri,service):
    import psycopg
    from tests.test_notifications import produce_reminders,drain
    from tests.test_actions_api import context,headers
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('update compliance.info_requests set due_date=null')
        db.execute("update compliance.cases set due_date=(clock_timestamp() at time zone 'Asia/Singapore')::date+3 where customer_id=1002")
        db.execute('update compliance.check_policy set reminder_days=2,reminders_are_service=%s',(service,))
        db.execute("update banking.customer_profiles set preferred_language='zh' where customer_id=1002")
        db.execute("update notify.preferences set channels='{}' where customer_id=1002")
    assert asyncio.run(produce_reminders(worker_uri))==0
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('update compliance.check_policy set reminder_days=4')
    assert asyncio.run(produce_reminders(worker_uri))==1
    asyncio.run(drain(worker_uri))
    response=native_api_client.get('/notifications',headers=headers(context(local_data_api,1002)))
    assert response.status_code==200,response.text
    assert len(response.json()['items'])==(1 if service else 0)
    if service:
        assert response.json()['items'][0]['locale']=='en'
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
