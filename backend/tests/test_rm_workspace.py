"""Persona 2 RM workspace (F12, F14, migrations 0089-0090) over real temporary PostgreSQL: book-based access, audited
refusals, consent-checked handover notes, restricted specialist detail, RM-approved writes and availability.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_rm_workspace.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL,uuid4,uuid5
import pytest
from tests.database_support import SOPHIE_LOGIN,SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SOPHIE='00000000-0000-4000-8000-100200000002'
RACHEL='00000000-0000-4000-8000-100300000001'


def as_rm(db: 'Connection[SqlRow]',login=SOPHIE_LOGIN,rm_id: str=SOPHIE,role: str='rm') -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(login),'role':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'app_metadata':{'demobank_role':role,'rm_id':rm_id}}),))
    db.execute('set local role authenticated')


def call(db,sql: str,*args):return db.execute(f'select {sql}',args).fetchone()[0]


def test_only_a_linked_rm_sees_only_her_book(database: 'Connection[SqlRow]') -> None:
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
    as_rm(database)
    match=call(database,'rm_match_clients(%s)',"Show me Ong Wei Ming's transactions and David's notes")
    assert match['outside_book'] is True and match['in_book']==[{'customer_id':1003,'name':'David Lee'}]
    none=call(database,'rm_match_clients(%s)','What needs my attention today?')
    assert none['in_book']==[] and none['outside_book'] is False


def test_specialist_cases_are_read_only_and_restricted_detail_is_withheld(database: 'Connection[SqlRow]') -> None:
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
    rachel_login=uuid4()
    database.execute("insert into auth.users(id,email) values (%s,'demo.rachel@example.com')",(rachel_login,))
    database.execute("insert into rm.staff_logins(advisor_id,demo_email,auth_user_id) values (%s,'demo.rachel@example.com',%s)",(RACHEL,rachel_login))
    as_rm(database,rachel_login,RACHEL)
    assert call(database,'rm_book()')['data']==[] and call(database,'rm_meeting_notes(1007)')['denied'] is True


def test_rm_approved_writes_are_recorded_once_and_visible_to_the_client(database: 'Connection[SqlRow]') -> None:
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
