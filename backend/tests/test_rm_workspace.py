"""Persona 2 RM workspace (F12, F14, migrations 0089-0090) over real temporary PostgreSQL: book-based access, audited
refusals, consent-checked handover notes, restricted specialist detail, RM-approved writes and availability. The meeting
request routes (build plan 2a, SQL 0106) run through the real gateway and JWT check with the Data API answered by the
same database (NativeDataApi).

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_rm_workspace.py -q
"""
import json
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL,uuid4,uuid5
import jwt
import pytest
from postgrest.exceptions import APIError
from app.core import data_api
from tests.database_support import SOPHIE_LOGIN,SqlRow,set_customer
from tests.test_gateway import bearer,client,code,env  # noqa: F401  (env is the autouse JWKS/database fixture)
from tests.test_meeting_requests import MEI_LING_REQUEST,MICHELLE_REQUEST,free_times

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SOPHIE='00000000-0000-4000-8000-100200000002'
RACHEL='00000000-0000-4000-8000-100300000001'
PRIYA='00000000-0000-4000-8000-100100000002'


def as_rm(db: 'Connection[SqlRow]',login=SOPHIE_LOGIN,rm_id: str=SOPHIE,role: str='rm') -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(login),'role':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'app_metadata':{'demobank_role':role,'rm_id':rm_id}}),))
    db.execute('set local role authenticated')


def full_book(db: 'Connection[SqlRow]') -> None:
    """Undo 0093 inside the test transaction: these tests exercise the 0089 rules with Sophie's original five-client book."""
    db.execute("delete from rm.assignments where advisor_id=%s and customer_id in (1003,1004,1005) and ended_on is null",(PRIYA,))
    db.execute("update rm.assignments set ended_on=null where advisor_id=%s and customer_id in (1003,1004,1005) "
        "and ended_on=(select max(ended_on) from rm.assignments a where a.advisor_id=%s and a.customer_id=rm.assignments.customer_id)",(SOPHIE,SOPHIE))
    db.execute("update rm.appointments set status=case when customer_id=1004 and format='video' then 'held' else 'confirmed' end "
        "where advisor_id=%s and customer_id in (1003,1004,1005) and status='cancelled' and starts_at>now()",(SOPHIE,))
    db.execute("update rm.tasks set status='open' where advisor_id=%s and customer_id in (1003,1004,1005) and status='cancelled'",(SOPHIE,))


def call(db,sql: str,*args):return db.execute(f'select {sql}',args).fetchone()[0]


def test_only_a_linked_rm_sees_only_her_book(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    names=[row['name'] for row in call(database,'rm_book()')['data']]
    assert names==['David Lee','Grace Koh','Hafiz Rahman','Michelle Ng','Tan Mei Ling']
    denied=call(database,'rm_client_profile(%s)',1006)
    assert denied['denied'] is True and denied['data'] is None
    assert call(database,"rm_book('Wei Ming')")['data']==[]
    database.execute('reset role')
    refused=database.execute("select count(*) from audit.events where action='policy.rm_client_denied' and customer_id=1006 and actor_type='staff'").fetchone()[0]
    assert refused>=2
    # A wrong advisor id, a customer token, a support role or user_metadata never pass.
    for login,rm_id,role in [(SOPHIE_LOGIN,RACHEL,'rm'),(uuid5(NAMESPACE_URL,'demobank-test-persona:1002'),SOPHIE,'rm'),(SOPHIE_LOGIN,SOPHIE,'support')]:
        database.execute('savepoint s')
        as_rm(database,login,rm_id,role)
        with pytest.raises(Exception,match='relationship manager'):database.execute('select rm_book()')
        database.execute('rollback to savepoint s')
    set_customer(database,1002)
    with pytest.raises(Exception,match='permission denied'):database.execute('select count(*) from rm.meeting_notes')


def test_names_outside_the_book_are_refused_before_any_read(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    match=call(database,'rm_match_clients(%s)',"Show me Ong Wei Ming's transactions and David's notes")
    assert match['outside_book'] is True and match['in_book']==[{'customer_id':1003,'name':'David Lee'}]
    none=call(database,'rm_match_clients(%s)','What needs my attention today?')
    assert none['in_book']==[] and none['outside_book'] is False


def test_specialist_cases_are_read_only_and_restricted_detail_is_withheld(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    tickets=call(database,'rm_tickets()')['data']
    mine={t['reference'] for t in tickets['rm_owned']}
    fraud=next(t for t in tickets['specialist'] if t['category']=='suspected_fraud')
    assert fraud['summary'] is None and fraud['restricted'] and fraud['urgency']=='urgent' and tickets['specialist'][0]['category']=='suspected_fraud'
    assert all(t['customer_id'] in (1002,1003,1004,1005,1007) for t in tickets['rm_owned']+tickets['specialist']) and mine
    detail=call(database,'rm_ticket(%s)',fraud['reference'])['data']
    assert detail['summary'] is None and detail['details'] is None and detail['internal_notes']==[] and detail['handover_context'] is None
    with pytest.raises(Exception,match='restricted'):
        call(database,'rm_send_client_update(%s,%s,%s,%s,null,1,false)',uuid4(),1004,fraud['reference'],'Hello Grace')


def test_consent_controls_the_handover_note_and_rm16_is_audited(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    shared=call(database,'rm_shared_summary(1007)')['data']
    assert shared['consent_active'] and len(shared['shared'])==1 and shared['excluded_revoked']==1 and shared['excluded_expired']==1
    assert 'Withdrawn' not in json.dumps(shared) and 'Older note' not in json.dumps(shared)
    brief=call(database,'rm_handover_sources(1007)')['data']
    assert brief['eligible'] and brief['withheld_private_notes']==1 and len(brief['notes'])==2
    assert {n['author'] for n in brief['notes']}=={'Rachel Goh'} and 'Private working note' not in json.dumps(brief)
    assert any(t['by']=='assistant' for t in brief['told']) and brief['free_slots']
    assert call(database,'rm_handover_sources(1004)')['data']=={'eligible':False}
    database.execute('reset role')
    row=database.execute("select actor_type,details from audit.events where details->>'tool'='rm.handover_brief' and customer_id=1007 order by event_id desc limit 1").fetchone()
    assert row[0]=='staff' and row[1]['notes']==2 and row[1]['excluded_consent']==2 and len(row[1]['source_ids'])>=3
    database.execute("update consent.grants set revoked_at=now() where grant_id='00000000-0000-4000-8000-100700000041'")
    as_rm(database)
    assert call(database,'rm_shared_summary(1007)')['data']['shared']==[]


def test_previous_rm_loses_access_after_reassignment(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    rachel_login=uuid4()
    database.execute("insert into auth.users(id,email) values (%s,'demo.rachel@example.com')",(rachel_login,))
    database.execute("insert into rm.staff_logins(advisor_id,demo_email,auth_user_id) values (%s,'demo.rachel@example.com',%s)",(RACHEL,rachel_login))
    as_rm(database,rachel_login,RACHEL)
    assert call(database,'rm_book()')['data']==[] and call(database,'rm_meeting_notes(1007)')['denied'] is True


def test_rm_approved_writes_are_recorded_once_and_visible_to_the_client(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    request,message,note=uuid4(),uuid4(),uuid4()
    with pytest.raises(Exception,match='identity check'):
        database.execute('savepoint s');call(database,"rm_create_service_request(%s,1003,'statement_request','Copy of September 2026 statement',%s,'normal',false)",
            request,json.dumps({'account':'Current account','period':'September 2026','format':'e-statement'}))
    database.execute('rollback to savepoint s')
    created=call(database,"rm_create_service_request(%s,1003,'statement_request','Copy of September 2026 statement',%s,'normal',true)",
        request,json.dumps({'account':'Current account','period':'September 2026','format':'e-statement'}))
    assert created['created'] and created['owner_team']=='Operations team'
    assert call(database,"rm_create_service_request(%s,1003,'statement_request','x',%s,'normal',true)",request,json.dumps({}))['reference']==created['reference']
    dup=call(database,"rm_create_service_request(%s,1003,'personal_details_update','Mobile',%s,'normal',true)",uuid4(),json.dumps({'change':'phone','details':'New number'}))
    assert dup['created'] is False and dup['duplicate']['reference']
    check=call(database,"rm_check_service_request(1003,'statement_request','Copy',%s,'normal')",json.dumps({'period':'August 2026'}))
    assert [m['key'] for m in check['missing']]==['account','format'] and check['valid'] is False
    owned=next(t['reference'] for t in call(database,'rm_tickets()')['data']['rm_owned'] if t['customer_id']==1005)
    sent=call(database,'rm_send_client_update(%s,1005,%s,%s,%s,2,true)',message,owned,'Hi Hafiz, I have asked the product team to review your fees.','in_review')
    assert sent['sent'] and sent['status']=='in_review'
    saved=call(database,"rm_save_interaction(%s,1003,current_date,'in_person','Intro','David will send the new USD amount.',array['Chase the USD amount'],'{}',"
        "array['Statement copy'],'raw notes','{}','[]')",note)
    tasks=call(database,'rm_create_tasks(1003,%s,%s)',saved['note_id'],json.dumps([{'request_key':str(uuid4()),'title':'Chase the USD amount','owner':'Sophie Lim'}]))
    assert len(tasks['created'])==1
    again=call(database,'rm_create_tasks(1003,%s,%s)',saved['note_id'],json.dumps([{'request_key':str(uuid4()),'title':'chase the USD amount '}]))
    assert again['created']==[] and again['duplicates']
    database.execute('reset role')
    assert database.execute("select count(*) from audit.events where action in ('action.rm_request_raised','action.rm_client_update_sent','action.rm_interaction_approved')").fetchone()[0]==3
    assert database.execute("select count(*) from audit.events where action='action.rm_client_update_sent' and details::text like '%%product team%%'").fetchone()[0]==0
    assert database.execute("select status from support.tickets where reference=%s",(owned,)).fetchone()[0]=='in_review'
    # A message on a specialist-managed (not restricted) request: no status change, and the client sees it in My requests.
    as_rm(database)
    statement=next(t for t in call(database,'rm_tickets()')['data']['specialist'] if t['customer_id']==1002 and t['category']=='statement_request')
    with pytest.raises(Exception,match='owner team'):
        database.execute('savepoint s');call(database,'rm_send_client_update(%s,1002,%s,%s,%s,1,false)',uuid4(),statement['reference'],'Hi Michelle','resolved')
    database.execute('rollback to savepoint s')
    call(database,'rm_send_client_update(%s,1002,%s,%s,null,1,false)',uuid4(),statement['reference'],'Hi Michelle, the Operations team is preparing your copy.')
    set_customer(database,1002)
    mine=call(database,'customer_read_tickets(1002)')['data']
    assert next(t for t in mine if t['reference']==statement['reference'])['last_update']['note'].startswith('Hi Michelle')
    database.execute('reset role')
    assert database.execute("select count(*) from notify.inbox where template_key='rm.client_update'").fetchone()[0]==2


def test_availability_changes_what_clients_can_book(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    before=call(database,'rm_availability()')['days']
    day=next(d for d in before if len(d['free'])>2)
    slot=day['free'][1]
    block=call(database,"rm_add_block((%s::date+%s::time) at time zone 'Asia/Singapore',(%s::date+%s::time+interval '30 minutes') at time zone 'Asia/Singapore','Lunch')",
        day['date'],slot,day['date'],slot)
    after=next(d for d in call(database,'rm_availability()')['days'] if d['date']==day['date'])
    assert slot not in after['free'] and after['blocks'][0]['block_id']==block['block_id']
    booked=next(d for d in before if d['bookings'] and d['bookings'][0]['status']=='confirmed')
    with pytest.raises(Exception,match='booked or is holding'):
        database.execute('savepoint s')
        call(database,"rm_add_block((%s::date+%s::time) at time zone 'Asia/Singapore',(%s::date+%s::time+interval '30 minutes') at time zone 'Asia/Singapore',null)",
            booked['date'],booked['bookings'][0]['starts'],booked['date'],booked['bookings'][0]['starts'])
    database.execute('rollback to savepoint s')
    call(database,'rm_remove_block(%s)',block['block_id'])
    assert slot in next(d for d in call(database,'rm_availability()')['days'] if d['date']==day['date'])['free']


def test_followups_cover_deadlines_tasks_meetings_and_commitments(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    items=call(database,'rm_followups()')['data']
    kinds={i['kind'] for i in items}
    assert {'rm_ticket','specialist_ticket','review_deadline','milestone','task','meeting','commitment'}<=kinds
    assert all(i['customer_id']!=1006 for i in items)
    overdue=next(i for i in items if i['kind']=='task' and i['overdue'])
    assert overdue['client']=='Hafiz Rahman' and overdue['confirmed_meeting']
    call(database,'rm_dismiss_followup(%s)',overdue['item_key'])
    assert overdue['item_key'] not in {i['item_key'] for i in call(database,'rm_followups()')['data']}


def test_conversations_are_saved_per_rm_and_hide_clients_who_left_the_book(database: 'Connection[SqlRow]') -> None:
    full_book(database)
    as_rm(database)
    cid=uuid4()
    turns=[{'role':'user','content':'About Mei Ling','client_id':1007},{'role':'assistant','reply':{'summary':'x'},'client_id':1007},
        {'role':'user','content':'Ticket triage','client_id':None}]
    assert call(database,'rm_save_conversation(%s,%s,%s,%s)',cid,'About Mei Ling',json.dumps(turns),json.dumps({'client_id':1007}))['revision']==1
    assert call(database,'rm_save_conversation(%s,%s,%s,%s)',cid,'About Mei Ling',json.dumps(turns),json.dumps({'client_id':1007}))['revision']==2
    listed=call(database,'rm_conversations()')['data']
    assert listed[0]['title']=='About Mei Ling' and listed[0]['clients']==['Tan Mei Ling']
    with pytest.raises(Exception,match='clients in your book'):
        database.execute('savepoint s');call(database,'rm_save_conversation(%s,%s,%s,%s)',uuid4(),'x',json.dumps([{'role':'user','content':'x','client_id':1006}]),'{}')
    database.execute('rollback to savepoint s')
    database.execute('reset role')
    database.execute("update rm.assignments set ended_on=current_date where customer_id=1007 and ended_on is null")
    as_rm(database)
    reopened=call(database,'rm_conversation(%s)',cid)
    assert reopened['withheld_turns']==2 and reopened['messages']==[turns[2]] and reopened['context']=={} and reopened['title']=='Earlier conversation'
    assert call(database,'rm_conversations()')['data'][0]['title']=='Earlier conversation'
    set_customer(database,1002)
    with pytest.raises(Exception):database.execute('select rm_conversations()')


def test_clients_can_book_at_most_two_meetings_a_week(database: 'Connection[SqlRow]') -> None:
    """0092: a third meeting in one week is neither offered nor accepted; other weeks stay open."""
    meeting=database.execute("select starts_at,advisor_id from rm.appointments where customer_id=1002 and status='confirmed' "
        "and starts_at>now() order by starts_at limit 1").fetchone()
    week=lambda t:database.execute("select date_trunc('week',%s::timestamptz at time zone 'Asia/Singapore')",(t,)).fetchone()[0]
    def offered():
        set_customer(database,1002)
        rows=database.execute("select starts_at from appointments_find_slots(1002,greatest((now() at time zone 'Asia/Singapore')::date,"
                "date_trunc('week',%s::timestamptz at time zone 'Asia/Singapore')::date),"
                "date_trunc('week',%s::timestamptz at time zone 'Asia/Singapore')::date+6,20)",(meeting[0],meeting[0])).fetchall()
        database.execute('reset role')
        return [r[0] for r in rows]
    same_week=[s for s in offered() if week(s)==week(meeting[0])]
    assert same_week, 'one meeting that week: still bookable'
    database.execute("insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status) values (1002,%s,%s,%s::timestamptz+interval '30 minutes','video','confirmed')",
        (meeting[1],same_week[0],same_week[0]))
    assert not [s for s in offered() if week(s)==week(meeting[0])]
    target=json.dumps({'advisor_id':str(meeting[1]),'starts_at':str(same_week[-1]),'ends_at':None})
    database.execute('savepoint limit_check')
    with pytest.raises(Exception,match='2 meetings with your relationship manager that week'):
        database.execute("select actions.require_available_slot(jsonb_set(%s::jsonb,'{ends_at}',to_jsonb((%s::timestamptz+interval '30 minutes')::text)),1002)",(target,same_week[-1]))
    database.execute('rollback to savepoint limit_check')
    as_rm(database)
    assert call(database,'rm_availability()')['rules']['weekly_meetings_per_client']==2


def test_demo_book_is_michelle_and_mei_ling(database: 'Connection[SqlRow]') -> None:
    """0093: Sophie keeps two clients; Grace and Hafiz moved to Priya, so they are refused like anyone outside her book."""
    as_rm(database)
    assert [row['name'] for row in call(database,'rm_book()')['data']]==['Michelle Ng','Tan Mei Ling']
    assert call(database,'rm_client_profile(%s)',1004)['denied'] is True
    assert {a['customer_id'] for a in call(database,'rm_appointments()')['data']}<={1002,1007}
    assert all(b['client'] in ('Michelle Ng','Tan Mei Ling') for d in call(database,'rm_availability()')['days'] for b in d['bookings'])


def test_clients_still_read_their_own_advisor(database: 'Connection[SqlRow]') -> None:
    """0094: the RLS helper rm.is_my_advisor stays executable for customers after 0089's schema-wide revoke."""
    set_customer(database,1002)
    assert call(database,'customer_read_assignment(%s)',1002)['data']['current']['name']=='Sophie Lim (DEMO)'
    assert call(database,'customer_read_introduction(%s)',1002)['data']['current']['name']=='Sophie Lim (DEMO)'


def test_rm_insights_link_meetings_and_stay_in_the_book(database: 'Connection[SqlRow]') -> None:
    """0095: a documented meeting is linked to its appointment; book facts and money movements are book-only."""
    as_rm(database)
    past=[a for a in call(database,'rm_appointments(current_date-30,current_date+13)')['data'] if a['past']]
    assert past and past[0]['client']=='Michelle Ng' and past[0]['note_id'] is None
    meeting=past[0]
    saved=call(database,"rm_save_interaction(%s,1002,%s,'phone','Check-in','Talked about the sale agreement.','{}','{}','{}','raw notes',%s,'[]')",
        uuid4(),meeting['date'],json.dumps({'appointment_id':meeting['appointment_id']}))
    again=next(a for a in call(database,'rm_appointments(current_date-30,current_date+13)')['data'] if a['appointment_id']==meeting['appointment_id'])
    assert str(again['note_id'])==str(saved['note_id'])
    notes=call(database,'rm_meeting_notes(1002)')['data']['notes']
    assert str(next(n for n in notes if str(n['note_id'])==str(saved['note_id']))['appointment_id'])==str(meeting['appointment_id'])
    # Another client's appointment id is never linked.
    other=call(database,"rm_save_interaction(%s,1007,current_date,'phone','x','y','{}','{}','{}','raw',%s,'[]')",uuid4(),json.dumps({'appointment_id':meeting['appointment_id']}))
    database.execute('reset role')
    assert database.execute('select appointment_id from rm.meeting_notes where note_id=%s',(other['note_id'],)).fetchone()[0] is None
    as_rm(database)
    facts=call(database,'rm_book_facts()')['data']
    assert [f['client'] for f in facts]==['Michelle Ng','Tan Mei Ling'] and facts[0]['maturities_and_reviews_next_90_days']
    cash=call(database,'rm_cash_events(1002)')['data']
    assert any(c['amount']==120000 and c['direction']=='incoming' and not c['recurring'] for c in cash)
    assert call(database,'rm_cash_events(1004)')['denied'] is True


# Meeting request routes (build plan 2a) ------------------------------------------------------------------------------
class NativeDataApi:
    """The Data API over the test database: an RPC runs in a savepoint under the caller's own JWT claims as authenticated
    (as PostgREST does), and a database error comes back as the SDK's APIError with its SQLSTATE."""
    def __init__(self,db: 'Connection[SqlRow]',ctx) -> None:
        self.db,self.claims=db,jwt.decode(ctx.principal.jwt.get_secret_value(),options={'verify_signature':False})
    def __enter__(self):return self
    def __exit__(self,*_) -> None:return None
    def rpc(self,name: str,params: dict):
        self.sql,self.args=f"select public.{name}({','.join(f'{k}=>%s' for k in params)})",tuple(params.values())
        return self
    def execute(self) -> SimpleNamespace:
        import psycopg
        try:
            with self.db.transaction():
                self.db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps(self.claims),))
                self.db.execute('set local role authenticated')
                return SimpleNamespace(data=self.db.execute(self.sql,self.args).fetchone()[0])
        except psycopg.Error as error:
            raise APIError({'code':error.sqlstate,'message':error.diag.message_primary,'details':None,'hint':None}) from error
        finally:self.db.execute('reset role')


@pytest.fixture
def sophie(database: 'Connection[SqlRow]',monkeypatch: pytest.MonkeyPatch) -> dict:
    """Sophie's bearer header; RM routes run through the gateway, rm_caller and records with RPCs answered by the database."""
    monkeypatch.setattr(data_api,'connect',lambda ctx:NativeDataApi(database,ctx))
    return bearer(sub=str(SOPHIE_LOGIN),app_metadata={'demobank_role':'rm','rm_id':SOPHIE})


def decide(request: str,headers: dict,**body):
    return client.post(f'/rm/meeting-requests/{request}/decide',json={'decision_id':str(uuid4()),**body},headers=headers)


def test_a_double_clicked_accept_records_once_and_requests_outside_the_book_are_refused(database: 'Connection[SqlRow]',sophie: dict) -> None:
    listed=client.get('/rm/meeting-requests',headers=sophie)
    assert listed.status_code==200
    mei=next(r for r in listed.json()['data'] if r['request_id']==MEI_LING_REQUEST)
    assert mei['purpose'].startswith('Renewing my fixed deposit') and mei['topics']==['fixed_deposit'] and mei['status']=='requested'
    assert client.get('/rm/meeting-requests?status=all',headers=sophie).status_code==422
    body={'decision_id':str(uuid4()),'decision':'accept','starts_at':mei['preferred_times'][0]['starts_at']}
    first=client.post(f'/rm/meeting-requests/{MEI_LING_REQUEST}/decide',json=body,headers=sophie)
    again=client.post(f'/rm/meeting-requests/{MEI_LING_REQUEST}/decide',json=body,headers=sophie)
    assert first.status_code==again.status_code==200 and (first.json()['replayed'],again.json()['replayed'])==(False,True)
    meeting=first.json()['request']['appointment_id']
    assert first.json()['request']['status']=='scheduled' and again.json()['request']['appointment_id']==meeting
    assert database.execute('select count(*) from meetings.events where meeting_id=%s',(meeting,)).fetchone()==(1,)
    assert MEI_LING_REQUEST in {r['request_id'] for r in client.get('/rm/meeting-requests?status=scheduled',headers=sophie).json()['data']}
    # Alex is Priya's client: refused and audited, never decided. An unknown request is 404.
    [at]=free_times(database,1001,n=1)
    alex=database.execute("""insert into rm.meeting_requests(customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by)
        values(1001,%s,'Investments','{investments}','video',%s,now()+interval '1 day') returning request_id::text""",(PRIYA,[at])).fetchone()[0]
    refused=decide(alex,sophie,decision='decline',reason='not_needed')
    assert refused.status_code==403 and code(refused)=='rm_not_in_book'
    assert database.execute("select count(*) from audit.events where action='policy.rm_client_denied' and details->>'tool'='rm.meeting_request_decide'").fetchone()==(1,)
    assert database.execute('select status from rm.meeting_requests where request_id=%s',(alex,)).fetchone()==('requested',)
    assert code(decide(str(uuid4()),sophie,decision='decline',reason='not_needed'))=='rm_refused'
    # A body that does not fit its decision never reaches the database.
    for bad in [{'decision':'decline'},{'decision':'accept'},{'decision':'decline','reason':'not_needed','starts_at':at.isoformat()},
            {'decision':'counter','starts_at':'2026-12-01T10:00:00'},{'decision':'decline','reason':'too_busy'}]:
        assert decide(alex,sophie,**bad).status_code==422


def test_a_refused_time_has_a_stable_code_and_the_same_decision_id_can_retry(database: 'Connection[SqlRow]',sophie: dict) -> None:
    preferred=database.execute('select preferred_times from rm.meeting_requests where request_id=%s',(MEI_LING_REQUEST,)).fetchone()[0]
    free=next(t for t in free_times(database,1007,n=8) if t not in preferred)
    night=database.execute("select ((%s::timestamptz at time zone 'Asia/Singapore')::date+time '03:00') at time zone 'Asia/Singapore'",(free,)).fetchone()[0]
    decision=str(uuid4())
    def counter(at):return decide(MEI_LING_REQUEST,sophie,decision_id=decision,decision='counter',starts_at=at.isoformat())
    refused=counter(night)
    assert refused.status_code==409 and code(refused)=='rm_slot_unavailable'
    assert refused.json()['error']['message']=="That time cannot be booked: it is outside the relationship manager's working hours"
    database.execute("insert into rm.advisor_blocks(advisor_id,starts_at,ends_at) values(%s,%s,%s::timestamptz+interval '30 minutes')",(SOPHIE,free,free))
    assert code(counter(free))=='rm_slot_unavailable'
    database.execute('delete from rm.advisor_blocks where advisor_id=%s and starts_at=%s',(SOPHIE,free))
    proposed=counter(free)  # nothing was recorded for the refused attempts, so the card's id still works
    assert proposed.status_code==200 and proposed.json()['request']['status']=='countered' and proposed.json()['request']['meeting']['status']=='held'
    late=decide(MEI_LING_REQUEST,sophie,decision='decline',reason='not_needed')
    assert late.status_code==409 and code(late)=='rm_refused'  # already answered: not a slot problem


def test_moving_a_meeting_records_once_and_refuses_taken_times_and_other_books(database: 'Connection[SqlRow]',sophie: dict) -> None:
    meeting=str(database.execute('select appointment_id from rm.meeting_requests where request_id=%s',(MICHELLE_REQUEST,)).fetchone()[0])
    later,taken=free_times(database,1002,n=2,exclude=meeting,skip=2)
    def move(at,change: str,appointment: str=meeting,**extra):
        return client.post(f'/rm/appointments/{appointment}/reschedule',json={'change_id':change,'starts_at':at.isoformat(),'reason':'client_asked',**extra},headers=sophie)
    change=str(uuid4())
    first,again=move(later,change),move(later,change)
    assert first.status_code==again.status_code==200 and (first.json()['replayed'],again.json()['replayed'])==(False,True)
    assert database.execute("select dedupe_key from meetings.events where meeting_id=%s and event_type='meeting.rescheduled'",(meeting,)).fetchall()==[(f'change:{change}',)]
    database.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status)
        values(1007,%s,%s,%s::timestamptz+interval '30 minutes','video','confirmed')""",(SOPHIE,taken,taken))
    clash=move(taken,str(uuid4()))
    assert clash.status_code==409 and code(clash)=='rm_slot_unavailable' and clash.json()['error']['message'].endswith('that time is already booked')
    other=database.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status)
        values(1001,%s,%s,%s::timestamptz+interval '30 minutes','video','cancelled') returning appointment_id::text""",(PRIYA,later,later)).fetchone()[0]
    refused=move(later,str(uuid4()),other)
    assert refused.status_code==403 and code(refused)=='rm_not_in_book'
    assert move(later,str(uuid4()),reason='because').status_code==422


def test_the_today_page_gets_one_ranked_list_with_its_reasons_from_her_book(database: 'Connection[SqlRow]',sophie: dict) -> None:
    """Build plan 3b: GET /rm/priorities ranks her own book-checked reads with services/rm/priority.py (the assistant's
    score), so the page never ranks; a meeting request past its answer-by time scores, and what a client wrote stays out."""
    from app.services.rm import priority
    database.execute("update rm.meeting_requests set respond_by=now()-interval '1 hour',created_at=now()-interval '3 days 1 minute' where request_id=%s",
        (MEI_LING_REQUEST,))
    got=client.get('/rm/priorities',headers=sophie)
    assert got.status_code==200
    body,items=got.json(),got.json()['items']
    assert body['points']==priority.POINTS and items and [x['rank'] for x in items]==list(range(1,len(items)+1))
    assert items==sorted(items,key=priority.order) and {x['customer_id'] for x in items}<={1002,1007}  # her book only
    assert all(x['score']==sum(q['points'] for q in x['parts']) and all(q['reason'] for q in x['parts']) for x in items)
    mei=next(x for x in items if x['kind']=='meeting_request' and x['ref']['request_id']==MEI_LING_REQUEST)
    assert [(q['code'],q['points']) for q in mei['parts']]==[('respond_by_passed',30),('waiting',3)] and mei['do_first']
    assert 'Renewing my fixed deposit' not in got.text and 'purpose' not in got.text
    customer=client.get('/rm/priorities',headers=bearer(sub=str(uuid5(NAMESPACE_URL,'demobank-test-persona:1002'))))
    assert customer.status_code==403 and code(customer)=='rm_only'
