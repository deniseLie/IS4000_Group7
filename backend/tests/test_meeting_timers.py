"""Durable real SQL deadlines, signed-in RM reminders and registered-worker scheduling isolation."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from tests.test_meeting_lifecycle import flow,headers,post,export_body,SOPHIE
from tests.test_zoom_capture import bound
from app.services.meetings.registered_worker import RegisteredWorkerDatabase,Runtime


def test_preparation_deadline_is_once_only_scoped_and_cancelled_after_reschedule(flow):
    registered=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    at=datetime.now(timezone.utc)+timedelta(hours=25)
    with psycopg.connect(flow.uri) as db:
        db.execute('update rm.appointments set starts_at=%s,ends_at=%s where appointment_id=%s',(at,at+timedelta(minutes=30),flow.meeting))
    assert registered.schedule_due()==0
    soon=at-timedelta(hours=2)
    with psycopg.connect(flow.uri) as db:
        db.execute('update rm.appointments set starts_at=%s,ends_at=%s where appointment_id=%s',(soon,soon+timedelta(minutes=30),flow.meeting))
        other=uuid4()
        db.execute("insert into rm.appointments(appointment_id,customer_id,advisor_id,starts_at,ends_at,format,status,purpose) values(%s,1002,%s,%s,%s,'video','confirmed','Another synthetic meeting')",
            (other,SOPHIE,soon-timedelta(hours=1),soon-timedelta(minutes=30)))
    assert registered.schedule_due()==1 and registered.schedule_due()==0
    result=flow.http.get('/meetings/reminders',headers=headers(flow,'rm'));assert result.status_code==200,result.text
    rows=[item for item in result.json() if item['meeting_id']==str(flow.meeting)]
    assert len(rows)==1 and rows[0]['kind']=='preparation'
    reminder=rows[0]['reminder_id']
    with psycopg.connect(flow.uri) as db:
        assert db.execute("select status from meetings.timers where meeting_id=%s and kind='preparation'",(other,)).fetchone()==('pending',)
    for role in ('client','outsider'):
        assert flow.http.get('/meetings/reminders',headers=headers(flow,role)).status_code==403
        assert flow.http.post(f'/meetings/reminders/{reminder}/acknowledge',headers=headers(flow,role)).status_code==403
    for _ in range(2):assert flow.http.post(f'/meetings/reminders/{reminder}/acknowledge',headers=headers(flow,'rm')).status_code==200
    assert all(item['reminder_id']!=reminder for item in flow.http.get('/meetings/reminders',headers=headers(flow,'rm')).json())
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.appointments set status='cancelled' where appointment_id=%s",(flow.meeting,))
        assert db.execute("select count(*) from meetings.timers where meeting_id=%s and status in ('pending','queued','notified')",(flow.meeting,)).fetchone()==(0,)


def test_two_hour_deadline_discovers_only_the_bound_occurrence_and_clears_after_evidence(flow,local_data_api):
    binding=bound(flow,local_data_api)
    registered=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    assert registered.schedule_due()==0
    with psycopg.connect(flow.uri) as db:
        # Advance elapsed-call state without waiting two hours; no provider speech or model response is simulated.
        db.execute("update meetings.workflows set started_at=clock_timestamp()-interval '3 hours',ended_at=clock_timestamp()-interval '2 hours 1 second' where meeting_id=%s",(flow.meeting,))
    assert registered.schedule_due()==1 and registered.schedule_due()==0
    discovery=None
    for _ in range(30):
        claim=registered.claim();assert claim is not None
        if claim.event_type=='transcript.discover':discovery=claim;break
        registered.apply_lifecycle(claim);registered.finish(claim)
    assert discovery is not None
    permit=registered.discovery_permit(discovery)
    assert permit.original_uuid==binding['p_original_uuid'] and permit.host_id==binding['p_host_id'] and not permit.source_exists
    registered.report_missing_transcript(discovery);registered.finish(discovery)
    reminders=flow.http.get('/meetings/reminders',headers=headers(flow,'rm'));assert reminders.status_code==200,reminders.text
    assert any(row['meeting_id']==str(flow.meeting) and row['kind']=='missing_transcript' for row in reminders.json())
    post(flow,'rm','/transcript-import',export_body())
    with psycopg.connect(flow.uri) as db:
        assert db.execute("select status from meetings.timers where meeting_id=%s and kind='missing_transcript'",(flow.meeting,)).fetchone()==('resolved',)
