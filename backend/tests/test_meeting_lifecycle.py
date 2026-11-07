"""Real gateway -> PostgREST -> RLS/SQL -> worker -> approved follow-up; no simulated model.

TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 enables native integration. TEST_MEETING_LIVE=1 additionally runs Codex.
All evidence is synthetic. The tests use signed local JWTs verified independently by the gateway and PostgREST.
"""
import asyncio
from datetime import datetime,timedelta,timezone
import json,os,time
from pathlib import Path
from typing import NamedTuple
from fastapi.testclient import TestClient
from uuid import UUID,uuid4
import jwt,psycopg,pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
from pydantic import SecretStr
from tests.database_support import SOPHIE_LOGIN,persona_id
from tests.data_api_support import mint_token
from app.schemas.meeting_lifecycle import MinutesContent
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.graph import process_transcript

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1' or os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Native PostgreSQL and PostgREST opt-in required')
SOPHIE='00000000-0000-4000-8000-100200000002'

class Flow(NamedTuple):
    http: TestClient
    meeting: UUID
    notice: UUID
    tokens: dict[str,str]
    worker: WorkerDatabase
    uri: str

@pytest.fixture
def flow(local_data_api,native_api_client,monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings,'meetings_enabled',True)
    monkeypatch.setattr(settings,'meetings_checkpoint_signing_key',SecretStr('native-meeting-checkpoint-signing-key-'*2))
    user='meeting_test_'+uuid4().hex
    meeting=uuid4()
    with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:
        db.execute(sql.SQL('create role {} login').format(sql.Identifier(user)))
        db.execute(sql.SQL('grant demobank_meetings to {}').format(sql.Identifier(user)))
        db.execute("insert into rm.appointments(appointment_id,customer_id,advisor_id,starts_at,ends_at,format,status,purpose) values(%s,1002,%s,clock_timestamp()-interval '40 minutes',clock_timestamp()-interval '10 minutes','video','confirmed','Review our agreed questions')",(meeting,SOPHIE))
        notice=db.execute("select notice_id from meetings.notice_versions where version='v1'").fetchone()[0]
    tokens={'client':mint_token(local_data_api,1002,'aal1',uuid4()),'outsider':mint_token(local_data_api,1001,'aal1',uuid4()),
        'rm':jwt.encode({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aud':'authenticated','session_id':str(uuid4()),'aal':'aal1','exp':int(time.time())+3600,
            'app_metadata':{'demobank_role':'rm','rm_id':SOPHIE}},local_data_api.signing_key,algorithm='ES256',headers={'kid':'native-test-key'})}
    params=conninfo_to_dict(local_data_api.database_uri);params['user']=user
    worker=WorkerDatabase(SecretStr(make_conninfo(**params)));worker.verify_identity()
    yield Flow(native_api_client,meeting,notice,tokens,worker,local_data_api.database_uri)


def headers(flow,role):return {'Authorization':'Bearer '+flow.tokens[role]}
def post(flow,role,suffix,body):
    r=flow.http.post(f'/meetings/{flow.meeting}{suffix}',headers=headers(flow,role),json=body)
    assert r.status_code==200,r.text
    return r.json()
def view(flow,role):
    r=flow.http.get(f'/meetings/{flow.meeting}',headers=headers(flow,role));assert r.status_code==200,r.text;return r.json()
def start(flow,role):
    post(flow,role,'/start',{'notice_id':str(flow.notice),'purpose':'Review our agreed questions','outcome':'Clarify responsibilities and next steps','command_id':str(uuid4())})
    for actor in ['rm','client']:post(flow,actor,'/notice-seen',{'notice_id':str(flow.notice),'revision':1})
    now=datetime.now(timezone.utc)
    for path,at in [('/documented-start',now-timedelta(minutes=3)),('/documented-end',now-timedelta(minutes=1))]:post(flow,'rm',path,{'revision':1,'at':at.isoformat(),'command_id':str(uuid4())})
def claim_transcript(flow):
    for _ in range(20):
        claim=flow.worker.claim();assert claim is not None
        if claim.event_type=='transcript.ready':return claim
        flow.worker.apply_lifecycle(claim);flow.worker.finish(claim)
    raise AssertionError('Transcript event not reached')
def export_body():return {'revision':1,'filename':'synthetic.txt','text':'RM: The document is awaiting bank review.\nClient: I will send the updated details.\nRM: I will check the review status.','speakers':[{'label':'RM','role':'rm'},{'label':'Client','role':'client'}],'coverage':'partial','command_id':str(uuid4())}
def approve_body(d):return {**{k:d[k] for k in ['draft_id','version','content_hash','revision','consent_epoch']},'command_id':str(uuid4())}

@pytest.mark.parametrize('initiator',['client','rm'])
def test_gateway_to_native_transcript_approval_and_both_owners(flow,initiator):
    start(flow,initiator);body=export_body();post(flow,'rm','/transcript-import',body)
    claim=claim_transcript(flow);context=flow.worker.context(claim)
    # Explicit human-entered minutes fixture validates effects without claiming a model result.
    content=MinutesContent.model_validate({'title':'Review and next steps','summary':[{'text':'The document is awaiting bank review.','quote':'The document is awaiting bank review.','cue_id':'cue1'}],'decisions':[],
        'actions':[{'action_id':str(uuid4()),'text':text,'cue_id':cue,'quote':quote,'owner':owner,'commitment':'agreed','condition':None,'due_on':None,'due_quote':None} for text,cue,quote,owner in [('Send updated details','cue2','I will send the updated details.','client'),('Check review status','cue3','I will check the review status.','rm')]],
        'client_recap':'The document is awaiting bank review. Please send the updated details; your RM will check the status.'})
    flow.worker.save(claim,context,content);flow.worker.finish(claim)
    draft=view(flow,'rm')['draft'];assert 'draft' not in view(flow,'client')
    assert flow.http.post(f'/meetings/{flow.meeting}/approve-minutes',headers=headers(flow,'client'),json=approve_body(draft)).status_code==403
    for changed in [{'content_hash':'0'*64},{'version':draft['version']+1},{'revision':draft['revision']+1}]:
        assert flow.http.post(f'/meetings/{flow.meeting}/approve-minutes',headers=headers(flow,'rm'),json={**approve_body(draft),**changed}).status_code==409
    approval=approve_body(draft)
    assert post(flow,'rm','/approve-minutes',approval)==post(flow,'rm','/approve-minutes',approval)
    post(flow,'rm','/followups',{**approve_body(draft),'action_ids':[str(a.action_id) for a in content.actions]})
    assert view(flow,'client')['shared_recap'] is None
    post(flow,'rm','/share-recap',approve_body(draft))
    wrong_owner=next(action for action in content.actions if action.owner=='client')
    assert flow.http.post(f'/meetings/{flow.meeting}/followups/{wrong_owner.action_id}/complete',headers=headers(flow,'rm'),json={'evidence':'Wrong owner must be refused','command_id':str(uuid4())}).status_code==403
    for action in content.actions:post(flow,action.owner,f'/followups/{action.action_id}/complete',{'evidence':'Synthetic task completed','command_id':str(uuid4())})
    assert view(flow,'rm')['open_followups']==0
    assert flow.http.get(f'/meetings/{flow.meeting}',headers=headers(flow,'outsider')).status_code==403


def test_notice_withdrawal_and_export_limits_at_real_http_boundary(flow):
    start(flow,'client')
    assert flow.http.post(f'/meetings/{flow.meeting}/transcript-import',headers=headers(flow,'client'),json=export_body()).status_code==403
    for text in ['\n'.join('RM: Statement.' for _ in range(1001)),'RM: '+('x'*1048576),'\n'.join('RM: '+('x'*1800) for _ in range(80))]:
        r=flow.http.post(f'/meetings/{flow.meeting}/transcript-import',headers=headers(flow,'rm'),json={**export_body(),'text':text});assert r.status_code==422,r.text[:300]
    post(flow,'rm','/transcript-import',export_body());post(flow,'client','/transcript-revoke',{'command_id':str(uuid4())})
    with pytest.raises(psycopg.ProgrammingError,match='recording permission changed') as denied:flow.worker.context(claim_transcript(flow))
    assert denied.value.sqlstate=='P1001'
    assert view(flow,'client')['recording_allowed'] is False


@pytest.mark.parametrize('filename,text,expected_cues',[
    ('hundred.txt','\n'.join('RM: Statement.' for _ in range(100)),100),
    ('thousand.txt','\n'.join('RM: Statement.' for _ in range(1000)),1000),
    ('subsecond.vtt','WEBVTT\n\n1\n00:00:00.001 --> 00:00:00.100\n<v RM>Hello.</v>\n\n2\n00:00:00.100 --> 00:00:00.200\n<v Other>Thanks.</v>\n',2),
])
def test_supported_export_boundaries_through_gateway_and_sql(flow,filename,text,expected_cues):
    start(flow,'client')
    post(flow,'rm','/transcript-import',{**export_body(),'filename':filename,'text':text})
    context=flow.worker.context(claim_transcript(flow))
    assert len(context.cues)==expected_cues
    if filename.endswith('.vtt'):
        assert context.cues[1].speaker=='unmapped'
        assert context.cues[0].start_seconds==0


def test_proactive_rm_proposal_uses_existing_client_confirmation(flow):
    identity=flow.http.get('/meetings/session',headers=headers(flow,'rm'));assert identity.status_code==200,identity.text
    free=flow.http.get('/meetings/slots/1002',headers=headers(flow,'rm'));assert free.status_code==200,free.text
    request_id=str(uuid4());r=flow.http.post('/meetings/proposals',headers=headers(flow,'rm'),json={'customer_id':1002,'starts_at':free.json()['slots'][0]['starts_at'],'purpose':'Discuss the agreed agenda','topics':['other'],'command_id':request_id})
    assert r.status_code==200,r.text
    with psycopg.connect(flow.uri) as db:assert db.execute('select status from rm.meeting_requests where request_id=%s',(request_id,)).fetchone()==('countered',)
    assert flow.http.get('/meetings/slots/1006',headers=headers(flow,'rm')).status_code==403


@pytest.mark.skipif(os.getenv('TEST_MEETING_LIVE')!='1',reason='Real model opt-in required')
def test_real_codex_notes_after_withdrawal_on_main_gateway(flow,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');start(flow,'client')
    post(flow,'client','/transcript-revoke',{'command_id':str(uuid4())})
    cues=[{'cue_id':'cue'+str(i),'speaker':speaker,'start_seconds':None,'text':text} for i,(speaker,text) in enumerate([('rm','The document is awaiting bank review.'),('client','I will send the updated details.'),('rm','I will check the review status.')],1)]
    post(flow,'rm','/notes',{'revision':1,'cues':cues,'command_id':str(uuid4())})
    receipt=post(flow,'rm','/generate-notes-draft',{'command_id':str(uuid4())})
    draft=view(flow,'rm')['draft'];assert draft['source_kind']=='rm_notes'
    assert {a['owner'] for a in draft['content']['actions'] if a['commitment']=='agreed'}=={'client','rm'}
    post(flow,'rm','/approve-minutes',{**receipt,'command_id':str(uuid4())});post(flow,'rm','/share-recap',{**receipt,'command_id':str(uuid4())})
    assert view(flow,'client')['shared_recap']


@pytest.mark.skipif(os.getenv('TEST_ZOOM_LIVE')!='1',reason='Existing private own-user Zoom OAuth grant required')
def test_real_zoom_link_via_integrated_main_api(flow,monkeypatch):
    from app.core.config import settings
    from app.services.meetings.zoom import Zoom,refreshed_token
    directory=Path(os.environ['TEST_ZOOM_OAUTH_DIRECTORY'])
    monkeypatch.setattr(settings,'zoom_oauth_directory',directory)
    with psycopg.connect(flow.uri,autocommit=True) as db:
        at=db.execute("select starts_at from rm.free_slots(%s,rm.today()+1,rm.today()+40,400) where rm.slot_problem(%s,1002,starts_at,null) is null order by starts_at limit 1",(SOPHIE,SOPHIE)).fetchone()[0]
        db.execute("update rm.appointments set starts_at=%s,ends_at=%s+interval '30 minutes' where appointment_id=%s",(at,at,flow.meeting))
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Review the agreed questions','outcome':'Agree next steps','command_id':str(uuid4())})
    link=post(flow,'rm','/zoom',{})
    provider=Zoom(refreshed_token(directory))
    try:
        remote=provider.inspect(link['provider_id'],flow.meeting)
        assert remote.start_time==at
        for role in ['client','rm']:
            r=flow.http.get(f'/meetings/{flow.meeting}/zoom',headers=headers(flow,role));assert r.status_code==200 and r.json()==link
        assert flow.http.get(f'/meetings/{flow.meeting}/zoom',headers=headers(flow,'outsider')).status_code==403
        assert post(flow,'rm','/zoom',{})==link
    finally:provider.cancel(link['provider_id'],flow.meeting);provider.close()
