"""Registered native queue/retention never consumes another meeting's work."""
import asyncio,json,os,subprocess,sys,time
from pathlib import Path
from uuid import uuid4
import psycopg,pytest
from tests.test_meeting_lifecycle import flow,start,post,export_body,claim_transcript
from app.services.meetings.registered_worker import RegisteredWorkerDatabase,Runtime,runtime
from app.services.meetings.lifecycle import dispatch


def test_registered_runner_claims_only_its_meetings_and_retains_the_other_queue(flow):
    start(flow,'client');registered=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    registered.verify_identity()
    with psycopg.connect(flow.uri) as db:
        before=db.execute("select event_id,status,attempts from meetings.events where meeting_id<>%s order by event_id",(flow.meeting,)).fetchall()
    assert before
    while claim:=registered.claim():
        assert claim.meeting_id==flow.meeting
        asyncio.run(dispatch(registered,claim))
    with psycopg.connect(flow.uri) as db:
        assert db.execute("select event_id,status,attempts from meetings.events where meeting_id<>%s order by event_id",(flow.meeting,)).fetchall()==before


def test_registered_retention_purges_only_the_explicit_meeting(flow):
    start(flow,'client');post(flow,'rm','/transcript-import',export_body());claim=claim_transcript(flow)
    other=uuid4();other_source=uuid4()
    with psycopg.connect(flow.uri) as db:
        db.execute("insert into rm.appointments(appointment_id,customer_id,advisor_id,starts_at,ends_at,format,status,purpose) select %s,customer_id,advisor_id,starts_at+interval '1 day',ends_at+interval '1 day',format,status,purpose from rm.appointments where appointment_id=%s",(other,flow.meeting))
        db.execute("insert into meetings.workflows select %s,revision,consent_epoch,meetings.schedule_hash(%s),notice_id,initiator,purpose,outcome,state,occurrence_id,null,null,started_at,ended_at from meetings.workflows where meeting_id=%s",(other,other,flow.meeting))
        db.execute("insert into meetings.sources(source_id,meeting_id,kind,origin,revision,consent_epoch,source_hash,cues,occurrence_id,purge_after) select %s,%s,kind,origin,revision,consent_epoch,source_hash,cues,occurrence_id,clock_timestamp()-interval '1 second' from meetings.sources where meeting_id=%s",(other_source,other,flow.meeting))
        db.execute("update meetings.sources set purge_after=clock_timestamp()-interval '1 second' where meeting_id=%s",(flow.meeting,))
    registered=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    assert registered.purge_transcripts()==1
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select jsonb_array_length(cues),purged_at is null from meetings.sources where source_id=%s',(other_source,)).fetchone()==(3,True)


def test_registered_runtime_refuses_publicly_readable_credentials(tmp_path):
    path=tmp_path/'runtime.json';path.write_text(json.dumps({'database_url':'postgresql://synthetic.invalid/db','meeting_ids':[str(uuid4())]}));path.chmod(0o644)
    with pytest.raises(PermissionError,match='owner-only'):runtime(path)


def test_registered_process_resumes_new_work_after_a_real_restart(flow,tmp_path):
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Discuss the agreed questions','outcome':'Clarify next steps','command_id':str(uuid4())})
    configuration=tmp_path/'registered-runtime.json'
    configuration.write_text(json.dumps({'database_url':flow.worker.uri.get_secret_value(),'meeting_ids':[str(flow.meeting)]}));configuration.chmod(0o600)
    environment={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','TMPDIR'}}
    environment.update(LANGSMITH_TRACING='false',WORKER_POLL_SECONDS='0.2',MEETINGS_CHECKPOINT_SIGNING_KEY='native-meeting-checkpoint-signing-key-'*2)

    def completed(process,event_id):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            assert process.poll() is None,'Registered worker stopped before finishing its recorded event'
            with psycopg.connect(flow.uri) as db:
                row=db.execute('select status from meetings.events where event_id=%s',(event_id,)).fetchone()
            if row==('done',):return
            time.sleep(0.1)
        raise AssertionError('Registered worker did not complete its recorded event within 20 seconds')

    with psycopg.connect(flow.uri) as db:
        scheduled=db.execute('select max(event_id) from meetings.events where meeting_id=%s',(flow.meeting,)).fetchone()[0]
        other=db.execute('select event_id,status,attempts from meetings.events where meeting_id<>%s order by event_id',(flow.meeting,)).fetchall()
    for index in range(2):
        if index==1:
            with psycopg.connect(flow.uri) as db:
                # The terminated process may have claimed the lifecycle projection; advance its recorded lease expiry.
                db.execute("update meetings.events set available_at=clock_timestamp()-interval '1 second' where meeting_id=%s and status='processing'",(flow.meeting,))
                event_id=db.execute("select meetings.enqueue_v2('brief.requested',%s,'{\"revision\":1}',%s)",(flow.meeting,'restart:'+str(uuid4()))).fetchone()[0]
        else:event_id=scheduled
        with (tmp_path/('registered-worker-'+str(index)+'.log')).open('w') as log:
            process=subprocess.Popen([sys.executable,'-m','app.services.meetings.registered_worker','--runtime',str(configuration)],
                cwd=Path(__file__).parents[1],env=environment,stdout=log,stderr=subprocess.STDOUT)
            try:completed(process,event_id)
            finally:
                if process.poll() is None:process.terminate()
                process.wait(timeout=10)
        with psycopg.connect(flow.uri) as db:
            assert db.execute('select event_id,status,attempts from meetings.events where meeting_id<>%s order by event_id',(flow.meeting,)).fetchall()==other
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select stage from meetings.threads where meeting_id=%s',(flow.meeting,)).fetchone()==('preparing',)
