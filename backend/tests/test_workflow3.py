"""Workflow 3 (David): RM introduction with approved wording, request intake/status, handover note and booking.

Unit checks run everywhere. Native checks (TEST_DATA_API_LOCAL=1) use real PostgreSQL/PostgREST, JWT and RLS and drive
the same HTTP sequence as the in-chat panel. No model is called here; the AI explanation path is covered by the opt-in
TEST_RELATIONSHIP_LIVE suite.
"""
import json
import os
from datetime import date,datetime,timedelta,timezone
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.schemas.customer_data import CustomerEnvelope,CustomerProfile
from app.schemas.relationship import RelationshipRequest
from app.schemas.relationship_intro import HandoverDraft,Introduction
from app.services import handover
from app.services.orchestrator import simple_issue,topic_domain
from tests.data_api_support import NativeAPI,mint_token

NATIVE=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')
SGT=timezone(timedelta(hours=8))
MARCUS=UUID('00000000-0000-4000-8000-100300000002')
RACHEL=UUID('00000000-0000-4000-8000-100300000001')


def section(anchor: str) -> dict[str,object]:
    return {'cite':f'kb:rm-change@v1#{anchor}','article_id':'rm-change','version':1,'anchor':anchor,'title':'Changes to your relationship manager',
        'heading':'Heading','body':'Approved body.','publisher':'DemoBank coursework project'}


def introduction(notice: list[str],previous: bool=True,current: bool=True) -> Introduction:
    return Introduction.model_validate({
        'current':{'rm_id':str(MARCUS),'name':'Marcus Chua (DEMO)','role':'Senior Relationship Manager','team':'Private Clients Team B',
            'contact':{'email':'marcus.chua@demobank.example','phone':'+65 6000 0000'},'started_on':'2026-10-15'} if current else None,
        'previous':{'name':'Rachel Goh (DEMO)','role':'Relationship Manager','team':'Private Clients Team A','started_on':'2024-03-01','ended_on':'2026-10-14'} if previous else None,
        'notice':[section(anchor) for anchor in notice]})


def request(**change: object) -> RelationshipRequest:
    row={'customer_id':1003,'request_id':str(uuid4()),'advisor_id':str(RACHEL),'who':'David Lee','what':'Update standing instruction for the quarterly USD transfer',
        'urgency':'normal','deadline':'2026-11-30','country':'SG','currency':'USD','status':'seen','customer_action':'Confirm the new quarterly transfer amount',
        'created_at':'2026-10-08T14:00:00+08:00','updated_at':'2026-10-09T09:30:00+08:00'}
    return RelationshipRequest.model_validate({**row,**change})


@pytest.mark.parametrize('message',['why did my old guy leave? will things stay the same?','can someone send me my annual portfolio statement',
    'whats happening with my requests?','can i meet her next week'])
def test_davids_messages_route_to_the_relationship_agent(message: str) -> None:
    assert topic_domain(message.split('?')[0])=='relationship'
    single=simple_issue(message,'English')
    assert single is None or single.domain=='relationship'


@pytest.mark.parametrize('message,domain',[('How do I find my statement?','retail'),('whats my balance','retail'),
    ('my transfer to my landlord still says pending','retail'),('theres a $2400 charge i dont recognise','fraud'),('what is the status of my compliance review','compliance')])
def test_existing_workflow_routing_is_unchanged(message: str,domain: str) -> None:
    assert topic_domain(message)==domain


def test_introduction_requires_the_matching_approved_wording() -> None:
    assert [item.anchor for item in introduction(['team-restructure','continuity']).notice]==['team-restructure','continuity']
    assert introduction(['unrecorded','continuity']).previous is not None
    assert introduction([],previous=False).notice==[]
    assert introduction(['no-advisor'],previous=False,current=False).current is None
    for notice,previous,current in [([],True,True),(['continuity','team-restructure'],True,True),(['team-restructure'],True,True),
        (['team-restructure','continuity'],False,True),([],False,False),(['no-advisor'],True,True)]:
        with pytest.raises(ValidationError):introduction(notice,previous,current)


def test_handover_note_uses_recorded_facts_and_never_claims_a_transfer() -> None:
    profile=CustomerProfile(name='David Lee (DEMO)',preferred_language='en',employment_status='employed')
    answered=request(status='answered',what='Old answered request',deadline=None)
    topics,fields=handover.compose(profile,[request(),answered],introduction(['team-restructure','continuity']))
    assert fields==['name','preferred_language']
    assert topics[0]=='Preference: please communicate with me in English'
    assert topics[1]=='Priority: Update standing instruction for the quarterly USD transfer (requested deadline 30 Nov 2026; recorded status seen; raised with Rachel Goh (DEMO); my recorded action: Confirm the new quarterly transfer amount)'
    assert topics[-1]=='Context: previously looked after by Rachel Goh (DEMO) until 14 Oct 2026'
    assert not any('Old answered' in topic or 'transferred' in topic or 'demobank.example' in topic for topic in topics)
    many=[request(what=f'Synthetic request {index} with card 4111 1111 1111 1111',deadline=None,customer_action=None,advisor_id=str(MARCUS)) for index in range(9)]
    topics,_=handover.compose(profile,many,introduction(['team-restructure','continuity']))
    assert len(topics)==6 and all(len(topic)<=300 and '4111 1111 1111 1111' not in topic for topic in topics)
    assert 'raised with Marcus Chua (DEMO)' in topics[1]
    with pytest.raises(handover.NoAdvisorError):handover.compose(profile,[],introduction(['no-advisor'],previous=False,current=False))
    with pytest.raises(ValidationError):HandoverDraft(advisor_id=MARCUS,recipient_name='Marcus',profile_fields=['name'],topics=[],sources=[])


def test_relationship_evidence_carries_approved_wording_without_the_reason_code() -> None:
    from app.schemas.appointments import AppointmentRecord
    from app.schemas.customer_data import RelationshipAssignment
    from app.schemas.relationship import AvailableSlot,SlotsQuery
    from app.schemas.relationship_records import RelationshipRecords
    from app.services.agents import relationship
    now=datetime(2026,10,26,9,tzinfo=SGT)
    def envelope(data: object) -> dict[str,object]:return {'customer_id':1003,'call_id':str(uuid4()),'as_of':now.isoformat(),'data':data}
    assignment={'current':{'rm_id':str(MARCUS),'name':'Marcus Chua (DEMO)','role':'Senior Relationship Manager','team':'B','contact':{'email':'marcus.chua@demobank.example','phone':'+65 6000 0000'},
        'timezone':'Asia/Singapore','started_on':'2026-10-15','ended_on':None,'transition_reason_code':'team_restructure'},'previous':None}
    records=RelationshipRecords.model_validate({'assignment':envelope(assignment),'requests':envelope([request().model_dump(mode='json')]),
        'appointments':envelope([]),'slots':envelope([]),'search':{'from_date':'2026-10-26','to_date':'2026-11-08','limit':20},
        'introduction':envelope(introduction(['team-restructure','continuity']).model_dump(mode='json'))})
    evidence=relationship.evidence_for(records)
    transition=evidence[-1]
    assert transition.id=='relationship-transition' and transition.source.url=='/knowledge/sections/rm-change/1/team-restructure'
    body=json.dumps([item.model_dump(mode='json',exclude={'source'}) for item in evidence])
    assert 'team_restructure' not in body and 'demobank.example' not in body and str(MARCUS) not in body
    assert json.loads(evidence[1].text)['records'][0]['customer_action']=='Confirm the new quarterly transfer amount'
    assert json.loads(evidence[1].text)['records'][0]['recorded_with_current_advisor'] is False
    assert relationship.transition_evidence(records.model_copy(update={'introduction':None}))==[]
    del AppointmentRecord,RelationshipAssignment,AvailableSlot,SlotsQuery


def auth(api: NativeAPI,cid: int) -> dict[str,str]:
    return {'Authorization':'Bearer '+mint_token(api,cid,'aal1',uuid4())}


def command(client: TestClient,headers: dict[str,str],kind: str,target: dict[str,object],case_id: str | None=None) -> dict[str,object]:
    if case_id is None:
        case_id=str(uuid4())
        assert client.post('/actions/cases',headers=headers,json={'session_id':str(uuid4()),'case_id':case_id}).status_code==200
    response=client.post('/commands',headers=headers,json={'command_id':str(uuid4()),'case_id':case_id,'task_id':'workflow3','type':kind,'target':target})
    assert response.status_code==200,response.text
    return response.json()


def confirm(client: TestClient,headers: dict[str,str],proposal: dict[str,object]) -> dict[str,object]:
    response=client.post(f"/commands/{proposal['command']['command_id']}/confirm",headers=headers)
    assert response.status_code==200,response.text
    result=response.json()
    assert result['receipt']['outcome']=='succeeded',result
    return result


@NATIVE
@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_introduction_and_meeting_options_are_owned_and_audited(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    headers=auth(local_data_api,cid)
    intro=native_api_client.get('/relationship/introduction',headers=headers)
    options=native_api_client.get('/relationship/meeting-options',headers=headers)
    assert intro.status_code==200 and options.status_code==200
    data=intro.json()['data']
    assert 'team_restructure' not in intro.text and 'transition_reason_code' not in intro.text
    if cid==1003:
        assert data['current']['name']=='Marcus Chua (DEMO)' and data['current']['contact']=={'email':'marcus.chua@demobank.example','phone':'+65 6000 0000'}
        assert data['previous']['name']=='Rachel Goh (DEMO)' and data['previous']['ended_on']=='2026-10-14'
        assert [item['anchor'] for item in data['notice']]==['team-restructure','continuity']
        assert data['notice'][0]['body'].startswith('Your relationship manager changed because DemoBank reorganised')
        assert {row['format'] for row in options.json()['data']}=={'video','phone','in_person'}
    else:  # 0063: one long-standing advisor, no change, so no change wording
        assert data['current']['name']==('Priya Nair (DEMO)' if cid==1001 else 'Daniel Tan (DEMO)') and data['previous'] is None and data['notice']==[]
        assert {row['format'] for row in options.json()['data']}=={'video','phone','in_person'}
        assert native_api_client.get('/relationship/handover-draft',headers=headers).status_code==200
    with psycopg.connect(local_data_api.database_uri) as db:
        tools={row[0] for row in db.execute("select details->>'tool' from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchall()}
        assert {'rm.get_introduction','appointments.meeting_options'}<=tools
        db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(uuid4()),'role':'authenticated'}),))
        db.execute('set local role authenticated')
        assert db.execute('select count(*) from rm.meeting_options').fetchone()==(0,)
        db.rollback()
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@NATIVE
def test_introduction_falls_back_to_unrecorded_wording_and_requires_approved_text(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    headers=auth(local_data_api,1003)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update rm.assignments set transition_reason_code='not_approved' where customer_id=1003 and ended_on is null")
    assert [item['anchor'] for item in native_api_client.get('/relationship/introduction',headers=headers).json()['data']['notice']]==['unrecorded','continuity']
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update kb.articles set status='retired' where article_id='rm-change'")
    failed=native_api_client.get('/relationship/introduction',headers=headers)
    assert failed.status_code==503 and failed.json()['error']['code']=='relationship_unavailable'
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update kb.articles set status='published' where article_id='rm-change'")
        db.execute("update rm.assignments set transition_reason_code='team_restructure' where customer_id=1003 and ended_on is null")


@NATIVE
def test_davids_workflow_records_request_note_sharing_and_booking(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    """The in-chat panel's HTTP sequence: every write is a reviewed action with its own receipt."""
    import psycopg
    client,headers=native_api_client,auth(local_data_api,1003)
    # RC4: status shows the old request's recorded customer action and recipient; nothing says transferred.
    old=client.get('/relationship/requests',headers=headers).json()['data']
    assert [(row['status'],row['customer_action'],row['advisor_id']) for row in old]==[('seen','Confirm the new quarterly transfer amount with your relationship manager',str(RACHEL))]
    # RC4: structured intake (what, urgency, deadline) lands in the current RM's queue only after confirmation.
    deadline=(datetime.now(SGT).date()+timedelta(days=10)).isoformat()
    proposal=command(client,headers,'requests.create',{'advisor_id':str(MARCUS),'what':'can someone send me my annual portfolio statement',
        'urgency':'normal','deadline':deadline,'country':None,'currency':None})
    assert proposal['command']['state']=='proposed' and proposal['command']['target']['recipient_name']=='Marcus Chua (DEMO)'
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select count(*) from rm.requests where customer_id=1003').fetchone()==(1,)
    sent=confirm(client,headers,proposal)['receipt']['result']
    assert sent['status']=='sent' and sent['recipient_name']=='Marcus Chua (DEMO)' and sent['deadline']==deadline
    queue=[row for row in client.get('/relationship/requests',headers=headers).json()['data'] if row['advisor_id']==str(MARCUS)]
    assert [(row['what'],row['status'],row['customer_action']) for row in queue]==[('can someone send me my annual portfolio statement','sent',None)]
    # RC2: draft from authorised records, add organised agenda topics, save privately, then consent and share.
    draft=client.get('/relationship/handover-draft',headers=headers).json()
    assert draft['recipient_name']=='Marcus Chua (DEMO)' and draft['profile_fields']==['name','preferred_language']
    assert {source['tool'] for source in draft['sources']}=={'customer.get_profile','requests.list','rm.get_introduction'}
    assert any('annual portfolio statement' in topic for topic in draft['topics'])
    topics=[*draft['topics'],'Agenda item 1: Estate planning','Agenda item 2: Review of fees','Agenda item 3: How often we meet']
    saved=command(client,headers,'agenda.save',{'agenda_id':str(uuid4()),'advisor_id':str(MARCUS),'expected_revision':0,'topics':topics,'profile_fields':draft['profile_fields']})
    note=saved['receipt']['result']
    assert saved['receipt']['outcome']=='succeeded' and note['categories']==['agenda','profile'] and 'Agenda item 2: Review of fees' in note['content']
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select count(*) from rm.shared_summaries').fetchone()==(0,)
    requested=command(client,headers,'consent.request',{'purpose':'rm_brief','recipient':str(MARCUS),'categories':note['categories'],'duration_days':30})
    grant=confirm(client,headers,command(client,headers,'consent.grant',{'request_id':requested['receipt']['result']['request_id']}))['receipt']['result']
    shared=confirm(client,headers,command(client,headers,'rm.share_summary',{'agenda_id':note['agenda_id'],'agenda_revision':note['revision'],
        'advisor_id':str(MARCUS),'consent_grant_id':grant['grant_id']}))['receipt']['result']
    assert shared['status']=='shared' and shared['recipient_name']=='Marcus Chua (DEMO)' and shared['content']==note['content']
    # RC3: suggested slots → ten-minute hold → booking confirmed only by the scheduling system, with recorded joining details.
    today=datetime.now(SGT).date();monday=today+timedelta(days=7-today.weekday())
    slots=client.post('/relationship/slots',headers=headers,json={'from_date':monday.isoformat(),'to_date':(monday+timedelta(days=4)).isoformat(),'limit':6}).json()['data']
    assert slots and all(row['advisor_id']==str(MARCUS) for row in slots)
    case_id=str(uuid4())  # the hold endpoint creates its own owned case
    held=client.post('/relationship/holds',headers=headers,json={'command_id':str(uuid4()),'session_id':str(uuid4()),'case_id':case_id,
        'target':{'advisor_id':slots[0]['advisor_id'],'starts_at':slots[0]['starts_at'],'ends_at':slots[0]['ends_at'],'format':'video'}})
    assert held.status_code==200,held.text
    hold=held.json()['receipt']['result']
    assert hold['status']=='held' and timedelta(minutes=9)<datetime.fromisoformat(hold['hold_expires_at'])-datetime.now(timezone.utc)<=timedelta(minutes=10)
    booked=confirm(client,headers,command(client,headers,'appointment.confirm',{'appointment_id':hold['appointment_id']},case_id))['receipt']['result']
    assert booked['status']=='confirmed' and booked['starts_at']==hold['starts_at'] and booked['format']=='video'
    joining={row['format']:row['joining_details'] for row in client.get('/relationship/meeting-options',headers=headers).json()['data']}
    assert 'DEMO-MC-ROOM-1' in joining['video']
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select status from rm.appointments where customer_id=1003").fetchall()==[('confirmed',)]
        assert db.execute('select count(*) from rm.shared_summaries where customer_id=1003').fetchone()==(1,)
        assert db.execute("select type,state from actions.commands order by proposed_at").fetchall()==[('requests.create','succeeded'),('agenda.save','succeeded'),
            ('consent.request','succeeded'),('consent.grant','succeeded'),('rm.share_summary','succeeded'),('appointment.hold','succeeded'),('appointment.confirm','succeeded')]
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@NATIVE
def test_customer_without_any_advisor_is_directed_to_support(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('delete from rm.assignments where customer_id=1001')
    headers=auth(local_data_api,1001)
    data=native_api_client.get('/relationship/introduction',headers=headers).json()['data']
    assert data['current'] is None and [item['anchor'] for item in data['notice']]==['no-advisor']
    assert native_api_client.get('/relationship/meeting-options',headers=headers).json()['data']==[]
    assert native_api_client.get('/relationship/handover-draft',headers=headers).json()['error']['code']=='advisor_missing'
