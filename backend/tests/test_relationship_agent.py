"""Real scoped relationship snapshots; provider calls require a separate explicit opt-in.

Run the live check separately from the browser check so the provider's cached async
client remains on one event loop. No AI or customer service is replaced with samples.
"""
import asyncio
import json
import os
from collections.abc import Iterator
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.core.config import settings
from app.schemas.contracts import Task
from app.schemas.relationship_records import RelationshipRecords
from app.services import audit
from app.services.agents import relationship,relationship_records
from app.services.authorization import PolicyDenied
from app.services.checkpoint_codec import CheckpointCodec
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers


@pytest.fixture(autouse=True)
def relationship_audit_context() -> Iterator[None]:
    token=audit.current_batch.set(None)
    try:yield
    finally:audit.current_batch.reset(token)


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_relationship_snapshots_are_dynamic_owned_bounded_and_checkpoint_safe(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    ctx=context(local_data_api,cid).model_copy(update={'agent':'relationship'})
    records=asyncio.run(relationship_records.load(ctx));evidence=relationship.evidence_for(records)
    assert records.assignment.customer_id==cid and len(evidence)==(5 if cid==1003 else 4)
    assert records.search.to_date-records.search.from_date==timedelta(days=13)
    assert CheckpointCodec().loads_typed(CheckpointCodec().dumps_typed(evidence))==evidence
    body=json.dumps([item.model_dump() for item in evidence])
    assert not any(key in body for key in ['request_id','rm_id','advisor_id','appointment_id','customer_id','transition_reason_code','team_restructure','demobank.example'])
    if cid==1003:
        assert records.assignment.data.current.name=='Marcus Chua (DEMO)'
        assert records.assignment.data.previous.name=='Rachel Goh (DEMO)'
        assert records.requests.data[0].status=='seen' and records.slots.data
        assert json.loads(evidence[1].text)['records'][0]['advisor_name']=='Rachel Goh (DEMO)'
        with psycopg.connect(local_data_api.database_uri) as db:db.execute("update rm.requests set status='answered' where customer_id=1003")
        assert asyncio.run(relationship_records.load(ctx)).requests.data[0].status=='answered'
    else:  # 0063: Alex and Michelle have one long-standing advisor and no change, so no transition wording
        assert records.assignment.data.current.name==('Priya Nair (DEMO)' if cid==1001 else 'Daniel Tan (DEMO)') and records.assignment.data.previous is None
        assert records.requests.data==[] and records.appointments.data==[] and records.slots.data
        assert evidence[-1].id=='relationship-transition' if records.introduction.data.notice else len(evidence)==4
    with pytest.raises(PolicyDenied):asyncio.run(relationship_records.load(ctx.model_copy(update={'agent':'retail'})))
    with psycopg.connect(local_data_api.database_uri) as db:
        tools={row[0] for row in db.execute("select details->>'tool' from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchall()}
        assert {'rm.get_assignment','rm.get_introduction','requests.list','appointments.list','appointments.find_slots'}<=tools
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_relationship_rejects_cross_customer_duplicate_and_out_of_window_records(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    records=asyncio.run(relationship_records.load(context(local_data_api,1003).model_copy(update={'agent':'relationship'})))
    original=records.model_dump(mode='json')
    for field,change in [('requests',{'customer_id':1001}),('slots',{'data':records.slots.model_dump(mode='json')['data']*2}),
        ('requests',{'data':records.requests.model_dump(mode='json')['data']*2})]:
        with pytest.raises(ValidationError):RelationshipRecords.model_validate({**original,field:{**original[field],**change}})
    shifted={**original['search'],'from_date':(records.search.to_date+timedelta(days=1)).isoformat(),
        'to_date':(records.search.to_date+timedelta(days=2)).isoformat()}
    with pytest.raises(ValidationError):RelationshipRecords.model_validate({**original,'search':shifted})


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_relationship_missing_reader_never_substitutes_available_records(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.customer_read_requests(bigint)')
    with pytest.raises(APIError):asyncio.run(relationship_records.load(context(local_data_api,1003).model_copy(update={'agent':'relationship'})))


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_relationship_validator_rejects_wrong_sources_dates_and_action_claims(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    records=asyncio.run(relationship_records.load(context(local_data_api,1003).model_copy(update={'agent':'relationship'})))
    evidence=relationship.evidence_for(records);task=Task(task_id='t1',domain='relationship',intent='answer_question')
    draft={'answer':'Your request has a recorded deadline of 2026-11-30.','source_ids':['relationship-requests'],
        'answer_kind':'banking','status':'ok','clarification_question':''}
    assert relationship.validate_result(task,json.dumps(draft),evidence).status=='ok'
    for invalid in [{**draft,'answer':'Your request has a recorded deadline of 2035-12-25.'},
        {**draft,'source_ids':['relationship-appointments']},{**draft,'source_ids':['unowned-source']},
        {**draft,'answer':'I have booked your appointment.'},{**draft,'answer':'We will send your request.'}]:
        assert relationship.validate_result(task,json.dumps(invalid),evidence).status=='error'


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_missing_relationship_provider_withholds_answer_and_records_support_handoff(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-relationship-key-with-at-least-32-characters')
    monkeypatch.delenv('LLM_MODEL',raising=False)
    ctx=context(local_data_api,1003);sid=uuid4()
    assert native_api_client.post('/conversations',headers=headers(ctx),json={'session_id':str(sid),'mode':'personal','language':'English'}).status_code==200
    response=native_api_client.post('/chat',headers=headers(ctx),json={'mode':'personal','messages':[{'role':'user','content':'Who is my relationship manager?'}],
        'conversation_id':str(sid),'turn_id':str(uuid4()),'revision':0})
    assert 'event: answer' in response.text and '"response_origin": "validation"' in response.text,response.text
    assert 'v5+82c591e9' in response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select type,state from actions.commands').fetchall()==[('handoff.create','succeeded')]
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1,)
        assert db.execute('select reason from support.handoffs').fetchall()==[('general_support',)]
        assert db.execute("select count(*) from audit.events where action='data.read' and details->>'tool'='rm.get_assignment'").fetchone()==(1,)


@pytest.mark.skipif(os.getenv('TEST_RELATIONSHIP_LIVE')!='1',reason='Requires approval for actual synthetic relationship provider checks')
def test_real_relationship_provider_explains_owned_assignments_and_request_status(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    async def run() -> None:
        for cid in [1001,1002,1003]:
            task=Task(task_id='live1',domain='relationship',intent='answer_question',entities={'question':'Who is my relationship manager?'})
            result,_,prompt=await relationship.respond(task,[],'English',context(local_data_api,cid).model_copy(update={'agent':'relationship'}),[])
            assert result.status in {'ok','needs_human'} and prompt.startswith('v2+'),result.model_dump_json()
            if cid==1003:
                assert 'Marcus Chua' in result.text and 'relationship-assignment' in {item.source_id for item in result.citations},result.text
            else:assert 'Marcus' not in result.text and 'Rachel' not in result.text
        task=Task(task_id='live2',domain='relationship',intent='answer_question',entities={'question':'What is the recorded status of my relationship request?'})
        result,_,_=await relationship.respond(task,[],'English',context(local_data_api,1003).model_copy(update={'agent':'relationship'}),[])
        assert result.status in {'ok','needs_human'} and 'seen' in result.text.lower(),result.model_dump_json()
        assert 'relationship-requests' in {item.source_id for item in result.citations}
    asyncio.run(run())
