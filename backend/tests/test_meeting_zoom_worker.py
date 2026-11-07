"""Real native queue permissions; opt-in Zoom app integration creates, moves and deletes one disposable meeting.

No Zoom response or model is simulated. The live case requires the already approved private own-user grant.
"""
import asyncio,os
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
import psycopg,pytest
from app.core.config import settings
from app.services.meetings.lifecycle import dispatch
from app.services.meetings.registered_worker import RegisteredWorkerDatabase,Runtime
from app.services.meetings.zoom import Zoom,refreshed_token
from tests.test_meeting_lifecycle import flow,headers,post


def test_scheduling_event_initialises_the_agreed_purpose_without_recording_permission(flow):
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.appointments set starts_at=date_trunc('minute',clock_timestamp()+interval '3 days'),ends_at=date_trunc('minute',clock_timestamp()+interval '3 days 30 minutes') where appointment_id=%s",(flow.meeting,))
        db.execute("select meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,null,'client'),%s) from rm.appointments a where appointment_id=%s",('confirmed:'+str(uuid4()),flow.meeting))
    worker=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    claim=worker.claim();assert claim is not None
    context=worker.zoom_schedule_context(claim);assert context is not None
    assert context.purpose=='Review our agreed questions' and context.provider_id is None and not context.linked_current
    assert worker.reserve_zoom_job(claim) is True and worker.reserve_zoom_job(claim) is False
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select initiator,purpose,outcome from meetings.workflows where meeting_id=%s',(flow.meeting,)).fetchone()==('client',context.purpose,context.purpose)
        assert db.execute('select count(*) from meetings.recording_grants where meeting_id=%s',(flow.meeting,)).fetchone()==(0,)
    worker.release_refused_zoom(claim);assert worker.reserve_zoom_job(claim) is True
    worker.finish(claim)


@pytest.mark.skipif(os.getenv('TEST_ZOOM_LIVE')!='1',reason='Approved private own-user Zoom grant required')
def test_real_zoom_worker_scheduling_reschedule_notifications_and_cancel(flow,monkeypatch):
    directory=Path(os.environ['TEST_ZOOM_OAUTH_DIRECTORY'])
    monkeypatch.setattr(settings,'zoom_capture_enabled',True);monkeypatch.setattr(settings,'zoom_oauth_directory',directory)
    response=flow.http.get('/meetings/slots/1002',headers=headers(flow,'rm'));assert response.status_code==200,response.text
    at=datetime.fromisoformat(response.json()['slots'][0]['starts_at'])
    with psycopg.connect(flow.uri) as db:
        db.execute('update rm.appointments set starts_at=%s,ends_at=%s where appointment_id=%s',(at,at+timedelta(minutes=30),flow.meeting))
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Review the agreed synthetic agenda','outcome':'Confirm responsibilities and next steps','command_id':str(uuid4())})
    worker=RegisteredWorkerDatabase(Runtime(database_url=flow.worker.uri,meeting_ids=(flow.meeting,)))
    provider_id=None
    def drain():
        for _ in range(30):
            claim=worker.claim()
            if claim is None:return
            asyncio.run(dispatch(worker,claim))
        raise AssertionError('Registered scheduling queue did not drain within thirty events')
    try:
        drain()
        link=flow.http.get(f'/meetings/{flow.meeting}/zoom',headers=headers(flow,'rm'));assert link.status_code==200,link.text
        provider_id=link.json()['provider_id']
        assert flow.http.get(f'/meetings/{flow.meeting}/zoom',headers=headers(flow,'client')).json()==link.json()
        assert flow.http.get(f'/meetings/{flow.meeting}/zoom',headers=headers(flow,'outsider')).status_code==403
        provider=Zoom(refreshed_token(directory))
        try:
            remote=provider.hosted(provider_id,flow.meeting,provider.owner())
            assert remote.start_time==at and remote.duration==30
            agenda=provider.read('/meetings/'+str(provider_id)).json()['agenda']
            assert 'Review the agreed synthetic agenda' in agenda
        finally:provider.close()
        with psycopg.connect(flow.uri) as db:
            inbox=db.execute("select body from notify.inbox where customer_id=1002 and dedup_key=%s",('zoom_link:'+str(flow.meeting)+':1',)).fetchall()
            assert len(inbox)==1 and link.json()['join_url'] in inbox[0][0] and '{when}' not in inbox[0][0]
            db.execute("select meetings.enqueue_v2('meeting.scheduled',%s,'{\"revision\":1}',%s)",(flow.meeting,'replay:'+str(uuid4())))
        drain()
        assert flow.http.get(f'/meetings/{flow.meeting}/zoom',headers=headers(flow,'rm')).json()['provider_id']==provider_id
        free=flow.http.get('/meetings/slots/1002',headers=headers(flow,'rm'));assert free.status_code==200,free.text
        moved=datetime.fromisoformat(free.json()['slots'][0]['starts_at'])
        result=flow.http.post(f'/rm/appointments/{flow.meeting}/reschedule',headers=headers(flow,'rm'),json={'change_id':str(uuid4()),'starts_at':moved.isoformat(),'reason':'rm_unavailable'})
        assert result.status_code==200,result.text
        drain()
        provider=Zoom(refreshed_token(directory))
        try:assert provider.inspect(provider_id,flow.meeting).start_time==moved
        finally:provider.close()
        with psycopg.connect(flow.uri) as db:
            revision=db.execute('select revision from meetings.workflows where meeting_id=%s',(flow.meeting,)).fetchone()[0]
            db.execute("update rm.appointments set status='cancelled' where appointment_id=%s",(flow.meeting,))
            db.execute("select meetings.enqueue_v2('meeting.cancelled',%s,jsonb_build_object('revision',%s),%s)",(flow.meeting,revision,'cancel:'+str(uuid4())))
        drain()
        with psycopg.connect(flow.uri) as db:assert db.execute('select cancelled_at is not null from meetings.zoom_links where meeting_id=%s',(flow.meeting,)).fetchone()==(True,)
    finally:
        provider=Zoom(refreshed_token(directory))
        try:
            owner=provider.owner()
            if provider_id is None:
                remote=provider.reconcile(flow.meeting,owner)
                if remote is not None:provider_id=remote.id
            if provider_id is not None:provider.cancel_bound(provider_id,flow.meeting,owner)
        finally:provider.close()
