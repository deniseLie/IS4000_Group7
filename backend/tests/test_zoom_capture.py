"""Native JWT/Gateway/PostgREST/SQL + signed synthetic webhook contracts; no simulated Zoom provider.

Live provider and model paths are opt-in separately. Fixtures here are explicit
synthetic evidence; they do not claim a real webhook delivery or speech capture.
"""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import hmac,json,secrets,time
from uuid import uuid4
import httpx,psycopg,pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
from pydantic import SecretStr
from fastapi.testclient import TestClient
from tests.test_meeting_lifecycle import flow,start,post,view,headers,approve_body
from app.services.meetings.zoom_events import IngressDatabase,application
from app.services.meetings.my_notes import Transcript
from app.services.meetings.zoom_ingest import transcript_evidence
from app.schemas.meeting_lifecycle import MinutesContent

pytestmark=pytest.mark.usefixtures('local_data_api')

def bound(flow,local_data_api):
    start(flow,'client')
    appointment=flow.http.get(f'/meetings/{flow.meeting}/appointment',headers=headers(flow,'rm')).json()
    body={'p_meeting_id':str(flow.meeting),'p_provider_id':91827364500,'p_join_url':'https://zoom.us/j/91827364500',
        'p_starts_at':appointment['starts_at'],'p_account_id':'synthetic-account','p_host_id':'synthetic-host','p_original_uuid':uuid4().hex}
    result=httpx.post(local_data_api.url+'/rpc/meeting_zoom_link_bind',headers=headers(flow,'rm'),json=body)
    assert result.status_code==200,result.text
    return body

@pytest.fixture
def ingress(flow):
    user='ingress_'+uuid4().hex
    with psycopg.connect(flow.uri,autocommit=True) as db:
        db.execute(sql.SQL('create role {} login').format(sql.Identifier(user)))
        db.execute(sql.SQL('grant demobank_meeting_ingress to {}').format(sql.Identifier(user)))
    params=conninfo_to_dict(flow.uri);params['user']=user
    connector=IngressDatabase(SecretStr(make_conninfo(**params)));connector.verify_identity()
    secret=SecretStr(secrets.token_urlsafe(16))
    with TestClient(application(connector,secret)) as client:yield client,secret,connector

def note_event(binding):
    return {'event':'my_notes.transcript_generated','event_ts':int(time.time()*1000),
        'payload':{'account_id':binding['p_account_id'],'operator_id':binding['p_host_id'],
            'object':{'meeting_id':binding['p_original_uuid'],'note_id':'synthetic-note','note_link':'https://untrusted.example/ignore-this-link','extra':'private speech must not enter queue'}}}

def send(ingress,body):
    raw=json.dumps(body,separators=(',',':')).encode();at=str(int(time.time()))
    signature='v0='+hmac.new(ingress[1].get_secret_value().encode(),b'v0:'+at.encode()+b':'+raw,'sha256').hexdigest()
    return ingress[0].post('/zoom/events',content=raw,headers={'x-zm-request-timestamp':at,'x-zm-signature':signature})

def fetch_claim(flow):
    for _ in range(20):
        claim=flow.worker.claim();assert claim is not None
        if claim.event_type=='transcript.fetch':return claim
        flow.worker.apply_lifecycle(claim);flow.worker.finish(claim)
    raise AssertionError('Zoom fetch job was not reached')

def evidence():
    return transcript_evidence(Transcript.model_validate({'items':[{'start_time':'00:00:01.000','end_time':'00:00:02.000','speaker_id':'provider-speaker','text':'I will check the review status.'}],
        'speakers':[{'speaker_id':'provider-speaker','display_name':'Untrusted role label'}]}))

def test_real_sql_binding_deduped_webhook_and_immutable_speaker_review(flow,local_data_api,ingress):
    binding=bound(flow,local_data_api);body=note_event(binding)
    first=send(ingress,body);assert first.status_code==200,first.text
    body['event']='my_notes.note_completed'
    assert send(ingress,body).json()==first.json()
    with psycopg.connect(flow.uri) as db:
        queued=db.execute("select payload from meetings.events where event_type='transcript.fetch' and meeting_id=%s",(flow.meeting,)).fetchall()
        assert len(queued)==1 and set(queued[0][0])=={'job_id','revision'}
    claim=fetch_claim(flow);permit=flow.worker.fetch_permit(claim)
    assert permit.original_uuid==binding['p_original_uuid'] and not permit.source_exists
    data=evidence();event=flow.worker.commit_transcript(claim,permit,data)
    assert flow.worker.commit_transcript(claim,permit,data)==event
    assert flow.worker.fetch_permit(claim).source_exists
    flow.worker.finish(claim)
    source=flow.http.get(f'/meetings/{flow.meeting}/zoom/evidence',headers=headers(flow,'rm')).json()
    assert source['cues'][0]['speaker']=='unmapped' and source['coverage']=='unknown'
    assert flow.http.get(f'/meetings/{flow.meeting}/zoom/evidence',headers=headers(flow,'client')).status_code==403
    mapping={key:source[key] for key in ('source_id','source_hash','revision','consent_epoch')}
    mapping.update(mapping={'speaker1':'rm'},command_id=str(uuid4()))
    post(flow,'rm','/zoom/speakers',mapping)
    assert post(flow,'rm','/zoom/speakers',mapping)['source_id']==mapping['command_id']
    reviewed=flow.http.get(f'/meetings/{flow.meeting}/zoom/evidence',headers=headers(flow,'rm')).json()
    assert reviewed['cues'][0]['speaker']=='rm'
    assert view(flow,'rm')['source_provenance']=={'origin':'zoom_my_notes_api','coverage':'unknown','speaker_basis':'rm_asserted_mapping'}
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select cues->0->>\'speaker\' from meetings.sources where source_id=%s',(source['source_id'],)).fetchone()==('unmapped',)

@pytest.mark.parametrize('phase',['before_fetch','during_fetch'])
def test_withdrawal_stops_api_permit_or_commit(flow,local_data_api,ingress,phase):
    binding=bound(flow,local_data_api);assert send(ingress,note_event(binding)).status_code==200
    claim=fetch_claim(flow)
    if phase=='during_fetch':permit=flow.worker.fetch_permit(claim)
    post(flow,'client','/transcript-revoke',{'command_id':str(uuid4())})
    with pytest.raises(psycopg.Error) as failure:
        if phase=='before_fetch':flow.worker.fetch_permit(claim)
        else:flow.worker.commit_transcript(claim,permit,evidence())
    assert failure.value.sqlstate in ('42501','P1001')
    with psycopg.connect(flow.uri) as db:assert db.execute('select count(*) from meetings.sources where meeting_id=%s',(flow.meeting,)).fetchone()==(0,)

@pytest.mark.parametrize('change',['account','operator','meeting','missing_meeting','signature','different_note'])
def test_event_authorisation_and_conflict(flow,local_data_api,ingress,change):
    binding=bound(flow,local_data_api);body=note_event(binding)
    if change=='different_note':
        assert send(ingress,body).status_code==200;body['payload']['object']['note_id']='another-note'
    elif change=='account':body['payload']['account_id']='other-account'
    elif change=='operator':body['payload']['operator_id']='other-host'
    elif change=='meeting':body['payload']['object']['meeting_id']='other-occurrence'
    elif change=='missing_meeting':del body['payload']['object']['meeting_id']
    if change=='signature':response=ingress[0].post('/zoom/events',json=body)
    else:response=send(ingress,body)
    assert response.status_code=={'signature':401,'missing_meeting':422,'different_note':409}.get(change,403),response.text

def test_separate_ingress_and_worker_cannot_read_private_tables(flow,ingress):
    for uri in (ingress[2].uri.get_secret_value(),flow.worker.uri.get_secret_value()):
        with psycopg.connect(uri,autocommit=True) as db:
            for name in ('meetings.zoom_bindings','meetings.zoom_fetch_jobs','meetings.zoom_evidence','banking.customer_profiles'):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):db.execute('select * from '+name)
    assert ingress[0].get('/docs').status_code==404
    assert ingress[0].get('/meetings/session').status_code==404


def test_note_waits_without_retrying_until_call_end_and_delayed_start_is_idempotent(flow,local_data_api,ingress):
    binding=bound(flow,local_data_api)
    # Prepare a distinct pre-call workflow state; no evidence exists yet.
    with psycopg.connect(flow.uri) as db:
        db.execute("delete from meetings.events where meeting_id=%s",(flow.meeting,))
        db.execute("update meetings.workflows set state='scheduled',started_at=null,ended_at=null,occurrence_id=null where meeting_id=%s",(flow.meeting,))
    note=send(ingress,note_event(binding));assert note.status_code==200,note.text
    with psycopg.connect(flow.uri) as db:
        assert db.execute("select status,attempts from meetings.events where meeting_id=%s and event_type='transcript.fetch'",(flow.meeting,)).fetchone()==('waiting',0)
    now=datetime.now(timezone.utc)
    obj={'id':binding['p_provider_id'],'uuid':binding['p_original_uuid'],'host_id':binding['p_host_id'],
        'start_time':(now-timedelta(minutes=3)).isoformat(),'end_time':(now-timedelta(minutes=1)).isoformat()}
    event={'event':'meeting.ended','event_ts':int(time.time()*1000),'payload':{'account_id':binding['p_account_id'],'object':obj}}
    ended=send(ingress,event);assert ended.status_code==200,ended.text
    event['event']='meeting.started'
    assert send(ingress,event).status_code==200
    assert view(flow,'client')['state']=='ended'
    claim=fetch_claim(flow);assert claim.attempts==1
    assert flow.worker.fetch_permit(claim).original_uuid==binding['p_original_uuid']


def test_claim_replacement_refuses_old_fetch_and_preserves_source(flow,local_data_api,ingress):
    binding=bound(flow,local_data_api);assert send(ingress,note_event(binding)).status_code==200
    old=fetch_claim(flow);permit=flow.worker.fetch_permit(old)
    with psycopg.connect(flow.uri) as db:db.execute("update meetings.events set available_at=clock_timestamp()-interval '1 second' where event_id=%s",(old.event_id,))
    fresh=flow.worker.claim();assert fresh.event_id==old.event_id and fresh.claim_token!=old.claim_token
    with pytest.raises(psycopg.Error) as denied:flow.worker.commit_transcript(old,permit,evidence())
    assert denied.value.sqlstate=='P1002'
    flow.worker.commit_transcript(fresh,flow.worker.fetch_permit(fresh),evidence())
    assert flow.worker.fetch_permit(fresh).source_exists


def test_signature_crc_and_no_extra_data_is_persisted(flow,local_data_api,ingress):
    body={'event':'endpoint.url_validation','event_ts':int(time.time()*1000),'payload':{'plainToken':'synthetic-challenge'}}
    response=send(ingress,body);assert response.status_code==200
    assert response.json()['encryptedToken']==hmac.new(ingress[1].get_secret_value().encode(),b'synthetic-challenge','sha256').hexdigest()
    binding=bound(flow,local_data_api);body=note_event(binding);raw=json.dumps(body,separators=(',',':')).encode();at=str(int(time.time()))
    signature='v0='+hmac.new(ingress[1].get_secret_value().encode(),b'v0:'+at.encode()+b':'+raw,'sha256').hexdigest()
    response=ingress[0].post('/zoom/events',content=raw+b' ',headers={'x-zm-request-timestamp':at,'x-zm-signature':signature})
    assert response.status_code==401


def test_minutes_approval_closes_only_its_bound_grant_once(flow):
    from tests.test_meeting_lifecycle import export_body,claim_transcript
    start(flow,'client');post(flow,'rm','/transcript-import',export_body())
    claim=claim_transcript(flow);context=flow.worker.context(claim)
    content=MinutesContent.model_validate({'title':'Approval boundary','summary':[{'text':'Awaiting review','cue_id':'cue1','quote':'The document is awaiting bank review.'}],
        'decisions':[],'actions':[],'client_recap':'The document is awaiting bank review.'})
    flow.worker.save(claim,context,content);flow.worker.finish(claim)
    with psycopg.connect(flow.uri) as db:
        grant=db.execute("update consent.grants set until_kind='meeting',until_meeting_id=%s,until_request_id=null where customer_id=1002 and categories=array['transaction_insights'] and revoked_at is null and scope is not null returning grant_id",(flow.meeting,)).fetchone()[0]
    draft=view(flow,'rm')['draft'];body=approve_body(draft)
    rejected=flow.http.post(f'/meetings/{flow.meeting}/approve-minutes',headers=headers(flow,'rm'),json={**body,'version':draft['version']+1})
    assert rejected.status_code==409
    with psycopg.connect(flow.uri) as db:assert db.execute('select revoked_at from consent.grants where grant_id=%s',(grant,)).fetchone()==(None,)
    assert post(flow,'rm','/approve-minutes',body)==post(flow,'rm','/approve-minutes',body)
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select end_reason from consent.grants where grant_id=%s',(grant,)).fetchone()==('meeting_closed',)
        assert db.execute("select count(*) from audit.events where action='consent.closed' and resource_id=%s",(str(grant),)).fetchone()==(1,)
