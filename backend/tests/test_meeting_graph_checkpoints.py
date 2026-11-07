"""Real durable interrupts, crash acknowledgement recovery, stale-run fencing and HMAC tamper rejection."""
import asyncio,json
from uuid import uuid4
import psycopg,pytest
from app.core.config import settings
from app.schemas.meeting_reviews import LifecycleState
from app.services.checkpoint_codec import CheckpointIntegrityError
from app.services.meetings.lifecycle import build,dispatch
from app.services.meetings.registered_worker import RegisteredWorkerDatabase,Runtime
from tests.test_meeting_lifecycle import flow,post


def test_checkpointed_interrupt_survives_crash_before_ack_and_stale_run_cannot_write(flow):
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Discuss the agreed questions','outcome':'Clarify next steps','command_id':str(uuid4())})
    worker=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    claim=worker.claim();assert claim is not None
    run=uuid4();worker.graph_open(claim,run)
    key=settings.meetings_checkpoint_signing_key;assert key is not None
    graph=build(worker,claim,run,key);config={'configurable':{'thread_id':str(flow.meeting),'checkpoint_ns':''}}
    result=asyncio.run(graph.ainvoke(LifecycleState(meeting_id=flow.meeting,event_id=claim.event_id,status=None),config,durability='sync'))
    assert result['__interrupt__']
    saved=asyncio.run(graph.aget_state(config));assert saved.next==('wait_for_lifecycle_event',) and saved.interrupts
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select status from meetings.events where event_id=%s',(claim.event_id,)).fetchone()==('processing',)
        before=db.execute('select count(*) from meetings.graph_checkpoints where meeting_id=%s',(flow.meeting,)).fetchone()[0]
        payloads=db.execute('select data from meetings.graph_checkpoints where meeting_id=%s',(flow.meeting,)).fetchall()
        text=json.dumps(payloads)
        assert 'Michelle' not in text and 'Discuss the agreed questions' not in text and 'Bearer ' not in text
        db.execute("update meetings.events set available_at=clock_timestamp()-interval '1 second' where event_id=%s",(claim.event_id,))
    reclaimed=worker.claim();assert reclaimed is not None and reclaimed.event_id==claim.event_id and reclaimed.claim_token!=claim.claim_token
    asyncio.run(dispatch(worker,reclaimed))
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select status from meetings.events where event_id=%s',(claim.event_id,)).fetchone()==('done',)
        assert db.execute('select count(*) from meetings.graph_checkpoints where meeting_id=%s',(flow.meeting,)).fetchone()[0]==before
    with pytest.raises(psycopg.ProgrammingError) as denied:worker.graph_status(flow.meeting,run)
    assert denied.value.sqlstate=='P1002'


def test_modified_signature_is_rejected_before_the_next_business_event(flow):
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Discuss the agreed questions','outcome':'Clarify next steps','command_id':str(uuid4())})
    worker=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    first=worker.claim();assert first is not None;asyncio.run(dispatch(worker,first))
    with psycopg.connect(flow.uri) as db:
        db.execute("update meetings.graph_checkpoints set data=jsonb_set(data,'{signature}',to_jsonb(repeat('0',64))) where meeting_id=%s",(flow.meeting,))
        next_event=db.execute("select meetings.enqueue_v2('brief.requested',%s,'{\"revision\":1}',%s)",(flow.meeting,'tamper:'+str(uuid4()))).fetchone()[0]
    next_claim=worker.claim();assert next_claim is not None and next_claim.event_id==next_event
    with pytest.raises(CheckpointIntegrityError,match='signature is invalid'):asyncio.run(dispatch(worker,next_claim))
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select status,last_error_code from meetings.events where event_id=%s',(next_event,)).fetchone()==('failed','meeting_checkpoint_invalid')
