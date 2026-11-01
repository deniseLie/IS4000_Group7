"""Real scoped compliance snapshots and validators; provider checks are separately opt-in.

Run TEST_COMPLIANCE_LIVE=1 against this module separately from test_compliance_browser.py.
The installed model SDK caches its async HTTP client per process; direct checks and the
browser server use different event loops. Neither suite substitutes an AI client.
"""
import asyncio
import json
import os
import re
from collections.abc import Iterator
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from app.core.config import settings
from app.schemas.contracts import Task
from app.services import audit
from app.services.agents import compliance
from app.services.agents.answer import validate_draft
from app.services.authorization import PolicyDenied
from app.services.checkpoint_codec import CheckpointCodec
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers


@pytest.fixture(autouse=True)
def compliance_audit_context() -> Iterator[None]:
    token=audit.current_batch.set(None)
    try:yield
    finally:audit.current_batch.reset(token)


@pytest.fixture(scope='module')
def compliance_provider_runner() -> Iterator[asyncio.Runner]:
    """Both live cases use the event loop owned by the SDK's cached HTTP client."""
    with asyncio.Runner() as runner:yield runner


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_compliance_snapshot_is_owned_and_excludes_internal_assessments(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    ctx=context(local_data_api,cid).model_copy(update={'agent':'compliance'})
    snapshot=asyncio.run(compliance.load(ctx))
    assert snapshot.customer_id==cid and (snapshot.data is not None)==(cid==1002)
    evidence=compliance.evidence_for(snapshot)
    assert len(evidence)==4 and all(item.source.url is None for item in evidence)
    codec=CheckpointCodec()
    assert codec.loads_typed(codec.dumps_typed(evidence))==evidence
    assert not any(value in json.dumps([item.model_dump() for item in evidence]) for value in ['risk_rating','reviewer_note','INTERNAL (DEMO)'])
    if snapshot.data:
        assert len(snapshot.data.requested_items)==4 and snapshot.data.status=='awaiting_client_documents'
        assert str(snapshot.data.due_date)=='2026-11-30' and len(snapshot.data.info_requests)==1
    else:assert json.loads(evidence[0].text)=={'records':None}
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select resource_id from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchall()==[(str(snapshot.call_id),)]
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
    with pytest.raises(PolicyDenied):asyncio.run(compliance.load(ctx.model_copy(update={'agent':'retail'})))


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_compliance_reader_failure_never_substitutes_a_case(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.customer_read_compliance(bigint)')
    with pytest.raises(APIError):asyncio.run(compliance.load(context(local_data_api,1002).model_copy(update={'agent':'compliance'})))


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_compliance_evidence_rejects_unowned_amounts_wrong_datasets_and_action_claims(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    snapshot=asyncio.run(compliance.load(context(local_data_api,1002).model_copy(update={'agent':'compliance'})))
    evidence=compliance.evidence_for(snapshot);task=Task(task_id='t1',domain='compliance',intent='answer_question')
    draft={'answer':'The information request asks about SGD 120,000.','source_ids':['compliance-requests'],'answer_kind':'banking','status':'ok','clarification_question':''}
    assert validate_draft(task,json.dumps(draft),evidence).status=='ok'
    for invalid in [{**draft,'answer':'The information request asks about SGD 250,000.'},{**draft,'source_ids':['compliance-case']},
        {**draft,'source_ids':['not-a-case-source']},{**draft,'answer':'I have submitted your documents.'}]:
        assert validate_draft(task,json.dumps(invalid),evidence).status=='error'


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_missing_compliance_provider_preserves_case_and_checklist(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-compliance-key-with-at-least-32-characters')
    monkeypatch.delenv('LLM_MODEL',raising=False)
    ctx=context(local_data_api,1002);sid=uuid4()
    assert native_api_client.post('/conversations',headers=headers(ctx),json={'session_id':str(sid),'mode':'personal','language':'English'}).status_code==200
    response=native_api_client.post('/chat',headers=headers(ctx),json={'mode':'personal','messages':[{'role':'user','content':'What is my compliance review status?'}],
        'conversation_id':str(sid),'turn_id':str(uuid4()),'revision':0})
    assert 'event: answer' in response.text and '"response_origin": "validation"' in response.text,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        # A provider outage is not an explicit RM request; it must not create an action or handoff.
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(0,)
        assert db.execute('select count(*) from support.handoffs where customer_id=1002').fetchone()==(0,)
        assert db.execute("select status from compliance.cases where customer_id=1002").fetchone()==('awaiting_client_documents',)
        assert db.execute("select count(*) from compliance.requested_items where customer_id=1002 and client_mark='not_prepared'").fetchone()==(4,)
        assert db.execute("select count(*) from audit.events where action='data.read' and details->>'tool'='compliance.get_case'").fetchone()==(1,)


@pytest.mark.skipif(os.getenv('TEST_COMPLIANCE_LIVE')!='1',reason='Opt in to actual synthetic compliance model calls')
def test_real_compliance_provider_keeps_preparation_distinct_from_bank_status(local_data_api: NativeAPI,native_api_client: TestClient,compliance_provider_runner: asyncio.Runner) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set client_mark='prepared',client_marked_at=clock_timestamp() where customer_id=1002 and sort_order=1")
    task=Task(task_id='live1',domain='compliance',intent='answer_question',entities={'question':'Does my prepared proof of residential address mean the bank accepted it?'})
    result,_,prompt=compliance_provider_runner.run(compliance.respond(task,[],'English',context(local_data_api,1002).model_copy(update={'agent':'compliance'}),[]))
    assert result.status in {'ok','needs_human'} and result.answer_kind=='banking',result.model_dump_json()
    assert re.search(r'not.submitted|not.*accept|does not mean|doesn.t mean|not.*confirm',result.text,re.I),result.text
    assert 'compliance-items' in {item.source_id for item in result.citations} and prompt.startswith('v4+')


@pytest.mark.skipif(os.getenv('TEST_COMPLIANCE_LIVE')!='1',reason='Opt in to actual synthetic compliance model calls')
def test_real_provider_does_not_treat_submitted_items_as_missing(local_data_api: NativeAPI,native_api_client: TestClient,compliance_provider_runner: asyncio.Runner) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set submission_status='submitted_awaiting_review',client_mark='not_prepared' where customer_id=1002")
    task=Task(task_id='live-submitted',domain='compliance',intent='answer_question',entities={'question':'Which requested compliance documents have I not sent yet?'})
    result,_,_=compliance_provider_runner.run(compliance.respond(task,[],'English',context(local_data_api,1002).model_copy(update={'agent':'compliance'}),[]))
    assert result.status=='ok' and result.answer_kind=='banking',result.model_dump_json()
    assert re.search(r'all.*(?:submitted|sent|bank)|no.*(?:outstanding|remain|left|need.*send)|(?:nothing|none).*send',result.text,re.I),result.text
    assert not re.search(r'(?:please|need to|must) (?:upload|submit|send)',result.text,re.I),result.text
    assert not re.search(r'(?:bank is|bank has started) (?:now |currently )?reviewing',result.text,re.I),result.text
    assert 'compliance-items' in {item.source_id for item in result.citations}
