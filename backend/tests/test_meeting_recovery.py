"""Reviewed recovery through genuine native JWT, PostgREST, signed ingress and fenced worker SQL."""
from datetime import datetime,timedelta,timezone
import time
from uuid import uuid4
import psycopg,pytest
from tests.test_meeting_lifecycle import flow,start,post,headers,export_body
from tests.test_zoom_capture import ingress,bound,send,note_event,fetch_claim,evidence


def recovery(flow):
    result=flow.http.get(f'/meetings/{flow.meeting}/recovery',headers=headers(flow,'rm'))
    assert result.status_code==200,result.text
    return result.json()


def test_reviewed_time_correction_and_retry_preserve_claim_fences(flow,local_data_api,ingress):
    binding=bound(flow,local_data_api);assert send(ingress,note_event(binding)).status_code==200
    old=fetch_claim(flow);permit=flow.worker.fetch_permit(old)
    with psycopg.connect(flow.uri) as db:
        db.execute("update meetings.workflows set ended_at=started_at+interval '0.5 seconds' where meeting_id=%s",(flow.meeting,))
    with pytest.raises(psycopg.errors.InvalidParameterValue):flow.worker.commit_transcript(old,permit,evidence())
    flow.worker.reject(old,'zoom_evidence_conflict')
    current=recovery(flow);assert current['failed_jobs'][0]['retry_problem']=='review_and_correct_call_times_first'
    assert not current['evidence_saved']
    change={'revision':1,'expected_start':current['started_at'],'expected_end':current['ended_at'],
        'started_at':current['started_at'],'ended_at':(datetime.fromisoformat(current['started_at'])+timedelta(minutes=2)).isoformat(),
        'reason':'Corrected the short manually recorded test timeline.','command_id':str(uuid4())}
    assert post(flow,'rm','/correct-call-times',change)==post(flow,'rm','/correct-call-times',change)
    assert recovery(flow)['failed_jobs'][0]['retry_problem'] is None
    retry={'revision':1,'reason':'Reviewed the corrected call times.','command_id':str(uuid4())}
    first=post(flow,'rm',f'/jobs/{old.event_id}/retry',retry)
    assert post(flow,'rm',f'/jobs/{old.event_id}/retry',retry)==first
    fresh=fetch_claim(flow);assert fresh.claim_token!=old.claim_token
    with pytest.raises(psycopg.Error) as expired:flow.worker.commit_transcript(old,permit,evidence())
    assert expired.value.sqlstate=='P1002'
    flow.worker.commit_transcript(fresh,flow.worker.fetch_permit(fresh),evidence());flow.worker.finish(fresh)
    locked=flow.http.post(f'/meetings/{flow.meeting}/correct-call-times',headers=headers(flow,'rm'),json={**change,'command_id':str(uuid4())})
    assert locked.status_code==409
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select count(*) from meetings.call_time_corrections where meeting_id=%s',(flow.meeting,)).fetchone()==(1,)


def test_signed_provider_time_conflict_is_durable_and_needs_review(flow,local_data_api,ingress):
    binding=bound(flow,local_data_api);before=recovery(flow)
    now=datetime.now(timezone.utc)
    obj={'id':binding['p_provider_id'],'uuid':binding['p_original_uuid'],'host_id':binding['p_host_id'],
        'start_time':(now-timedelta(minutes=5)).isoformat(),'end_time':(now-timedelta(minutes=1)).isoformat()}
    event={'event':'meeting.ended','event_ts':int(time.time()*1000),'payload':{'account_id':binding['p_account_id'],'object':obj}}
    first=send(ingress,event);assert first.status_code==200,first.text
    assert send(ingress,event).json()==first.json()
    current=recovery(flow)
    assert current['provider_times']['conflict'] and current['started_at']==before['started_at']
    review={'revision':1,'expected_start':before['started_at'],'expected_end':before['ended_at'],
        'reason':'Reviewed the verified provider occurrence times.','command_id':str(uuid4())}
    assert post(flow,'rm','/reconcile-zoom-times',review)==post(flow,'rm','/reconcile-zoom-times',review)
    current=recovery(flow);assert not current['provider_times']['conflict']
    assert datetime.fromisoformat(current['started_at'])==datetime.fromisoformat(obj['start_time'])
    assert datetime.fromisoformat(current['ended_at'])==datetime.fromisoformat(obj['end_time'])
    changed={**event,'payload':{**event['payload'],'object':{**obj,'end_time':(now-timedelta(seconds=30)).isoformat()}}}
    assert send(ingress,changed).status_code==409


@pytest.mark.parametrize('role',['client','outsider'])
def test_recovery_is_rm_only_and_stale_times_cannot_be_overwritten(flow,role):
    start(flow,'client');current=recovery(flow)
    body={'revision':1,'expected_start':current['started_at'],'expected_end':current['ended_at'],
        'started_at':current['started_at'],'ended_at':current['ended_at'],'reason':'Reviewed correction.','command_id':str(uuid4())}
    assert flow.http.get(f'/meetings/{flow.meeting}/recovery',headers=headers(flow,role)).status_code==403
    assert flow.http.post(f'/meetings/{flow.meeting}/correct-call-times',headers=headers(flow,role),json=body).status_code==403
    assert flow.http.post(f'/meetings/{flow.meeting}/correct-call-times',headers=headers(flow,'rm'),json={**body,'expected_end':datetime.now(timezone.utc).isoformat()}).status_code==409


def test_withdrawal_refuses_a_previously_retryable_transcript_job(flow,local_data_api,ingress):
    binding=bound(flow,local_data_api);assert send(ingress,note_event(binding)).status_code==200
    job=fetch_claim(flow);flow.worker.reject(job,'zoom_api_failed')
    post(flow,'client','/transcript-revoke',{'command_id':str(uuid4())})
    body={'revision':1,'reason':'Provider access was restored.','command_id':str(uuid4())}
    result=flow.http.post(f'/meetings/{flow.meeting}/jobs/{job.event_id}/retry',headers=headers(flow,'rm'),json=body)
    assert result.status_code==409
    assert recovery(flow)['failed_jobs'][0]['retry_problem']=='transcript_permission_unavailable'
