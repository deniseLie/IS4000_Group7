"""Consent withdrawal by request (0146) over real temporary PostgreSQL: the consent_withdrawal category and its 10
business days, the data protection team's SQL editor steps (withdraw_by_request, add_internal_note), the standing
recording opt-out, and what the client and her RM can and cannot do. Synthetic data only.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_consent_withdrawal.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from tests.database_support import SqlRow,set_customer
from tests.test_meeting_requests import MICHELLE_GRANT,MICHELLE_INSIGHTS_GRANT,act,free_times
from tests.test_meeting_seam import SOPHIE,as_rm
from tests.test_offer_groups import sqlstate

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
RECORDING_GRANT='00000000-0000-4000-8000-100200000043'  # Michelle's notice-based recording grant (0109)
STOP_RECORDING={'category':'consent_withdrawal','summary':'Please stop recording my meetings.','urgency':'normal','details':{'what':'Meeting recordings'}}


def raise_ticket(db: 'Connection[SqlRow]',customer: int=1002,what: str='Meeting recordings') -> str:
    outcome=act(db,'ticket.create',{**STOP_RECORDING,'details':{'what':what}},customer)
    assert outcome['command']['state']=='succeeded' and outcome['receipt']['result']['created'] is True
    db.execute('reset role')
    return outcome['receipt']['result']['reference']


def withdraw(db: 'Connection[SqlRow]',reference: str,category: str) -> dict:
    db.execute('reset role')  # the team works from the SQL editor as the owner
    return db.execute('select consent.withdraw_by_request(%s,%s)',(reference,category)).fetchone()[0]


def grant(db: 'Connection[SqlRow]',grant_id: str) -> tuple:
    db.execute('reset role')
    return db.execute('select end_reason,revoked_at is null from consent.grants where grant_id=%s',(grant_id,)).fetchone()


def active(db: 'Connection[SqlRow]',customer: int,category: str) -> bool:
    db.execute('reset role')
    return db.execute('select rm.consent_active(%s,%s,%s)',(customer,category,SOPHIE)).fetchone()[0]


def recording_grants(db: 'Connection[SqlRow]',customer: int) -> int:
    db.execute('reset role')
    return db.execute("""select count(*) from consent.grants where customer_id=%s and 'meeting_recording'=any(categories) and revoked_at is null
        and (expires_at is null or expires_at>now())""",(customer,)).fetchone()[0]


def confirm_meeting(db: 'Connection[SqlRow]',customer: int) -> None:
    [at]=free_times(db,customer,n=1)
    db.execute('reset role')
    db.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status)
        values(%s,%s,%s,%s::timestamptz+interval '30 minutes','video','confirmed')""",(customer,SOPHIE,at,at))


def test_the_category_is_raisable_by_her_and_due_in_ten_business_days(database: 'Connection[SqlRow]') -> None:
    from app.services.customer_data import CATEGORIES
    database.execute("select set_config('request.jwt.claims','{\"role\":\"anon\"}',true)")
    database.execute('set local role anon')
    rows=CATEGORIES.validate_python(database.execute('select public.read_request_categories()').fetchone()[0])
    [cat]=[c for c in rows if c.category=='consent_withdrawal']
    assert len(rows)<=50 and len(cat.description)<=400  # frontend/src/lib/ticketData.js parseCategories
    assert (cat.label,cat.owner_team,cat.customer_raisable)==('Stop using my data','Data protection team',True) and len(cat.decided_by)<=120
    assert [(f.key,f.kind,f.required) for f in cat.fields]==[('what','choice',True),('details','text',False)]
    assert all(len(c)<=40 for c in cat.fields[0].choices)  # the form shows the choices as they are (RequestField.jsx)
    database.execute('reset role')
    reference=raise_ticket(database)
    owner,status,source,due,days=database.execute("""select owner_team,status,source,(target_at at time zone 'Asia/Singapore'),(select count(*) from generate_series(
        (created_at at time zone 'Asia/Singapore')::date+1,(target_at at time zone 'Asia/Singapore')::date,interval '1 day') d where extract(isodow from d)<6)
        from support.tickets where reference=%s""",(reference,)).fetchone()
    assert (owner,status,days)==('Data protection team','submitted',10) and due.isoweekday()<6 and (due.hour,due.minute)==(18,0)
    assert source in ('form','chat')
    assert act(database,'ticket.create',STOP_RECORDING)['receipt']['result']['created'] is False  # asking twice shows the open one
    for details in ({},{'what':'meeting_recording'},{'what':'Meeting recordings','reason':'x'}):  # required, a listed choice, known keys only
        set_customer(database,1003)
        case=uuid4()
        database.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
        assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',
            (uuid4(),case,'ticket.create',json.dumps({**STOP_RECORDING,'details':details}),f'{case}:t1:ticket.create'))=='22023',details


def test_withdrawing_recording_ends_the_notice_grant_and_no_later_meeting_brings_it_back(database: 'Connection[SqlRow]') -> None:
    reference=raise_ticket(database)
    assert active(database,1002,'meeting_recording') is True and recording_grants(database,1007)==0
    result=withdraw(database,reference,'meeting_recording')
    assert (result['grants_ended'],result['recording_opted_out'],result['customer_id'])==(1,True,1002)
    assert grant(database,RECORDING_GRANT)==('withdrawn',False) and active(database,1002,'meeting_recording') is False
    assert grant(database,MICHELLE_INSIGHTS_GRANT)==(None,True) and grant(database,MICHELLE_GRANT)==(None,True)  # only that category
    rows=database.execute("select action,resource_id,details from audit.events where action in ('consent.withdrawn','consent.recording_opted_out') order by event_id").fetchall()
    assert [(a,d) for a,_,d in rows]==[('consent.withdrawn',{'category':'meeting_recording','reference':reference}),
        ('consent.recording_opted_out',{'category':'meeting_recording','reference':reference})] and rows[0][1]==RECORDING_GRANT
    confirm_meeting(database,1002)  # 0109 would have put recording back on under the notice
    assert recording_grants(database,1002)==0
    confirm_meeting(database,1007)  # a client who did not opt out still gets it
    assert recording_grants(database,1007)==1
    again=withdraw(database,reference,'meeting_recording')  # running the step twice changes nothing
    assert (again['grants_ended'],again['recording_opted_out'])==(0,True)
    assert database.execute("select count(*) from audit.events where action='consent.recording_opted_out'").fetchone()==(1,)
    assert database.execute('select consent.recording_opted_out(1002),consent.recording_opted_out(1007)').fetchone()==(True,False)


def test_withdrawing_chat_topics_forgets_her_inferred_topics_and_keeps_her_recording(database: 'Connection[SqlRow]') -> None:
    reference=raise_ticket(database,what='Topics from my chats')
    database.execute('reset role')
    assert database.execute("select count(*) from insights.talking_points where customer_id=1002 and origin='inferred' and deleted_at is null").fetchone()[0]>0
    result=withdraw(database,reference,'chat_topics')
    assert (result['grants_ended'],result['recording_opted_out'])==(1,False)
    assert grant(database,MICHELLE_GRANT)==('withdrawn',False) and active(database,1002,'chat_topics') is False
    assert database.execute("select count(*) from insights.talking_points where customer_id=1002 and origin='inferred' and deleted_at is null").fetchone()==(0,)
    assert database.execute('select count(*) from insights.talking_point_evidence where customer_id=1002').fetchone()==(0,)  # 0110's trigger still fires
    assert active(database,1002,'meeting_recording') is True and grant(database,MICHELLE_INSIGHTS_GRANT)==(None,True)


def test_only_the_owner_runs_the_steps_and_only_on_an_open_withdrawal_request(database: 'Connection[SqlRow]') -> None:
    reference=raise_ticket(database)
    for fn in ('consent.withdraw_by_request(text,text)','support.add_internal_note(text,text)','consent.recording_opted_out(bigint)'):
        assert database.execute("select has_function_privilege('authenticated',%s,'execute'),has_function_privilege('anon',%s,'execute')",
            (fn,fn)).fetchone()==(False,False),fn
    set_customer(database,1002)  # not even her own ticket from the client side
    assert sqlstate(database,'select consent.withdraw_by_request(%s,%s)',(reference,'meeting_recording'))=='42501'
    as_rm(database)
    assert sqlstate(database,'select consent.withdraw_by_request(%s,%s)',(reference,'meeting_recording'))=='42501'
    database.execute('reset role')
    deletion=act(database,'ticket.create',{'category':'data_deletion','summary':'Delete all my data','urgency':'normal','details':{}})['receipt']['result']['reference']
    database.execute('reset role')
    assert sqlstate(database,'select consent.withdraw_by_request(%s,%s)',(deletion,'meeting_recording'))=='22023'  # not a withdrawal request
    assert sqlstate(database,'select consent.withdraw_by_request(%s,%s)',('TCK-999999','meeting_recording'))=='P0002'
    assert sqlstate(database,'select consent.withdraw_by_request(%s,%s)',(reference,'profile_basics'))=='22023'  # always shared, not by consent
    database.execute("select support.set_ticket_status(%s,'resolved',null)",(reference,))
    assert sqlstate(database,'select consent.withdraw_by_request(%s,%s)',(reference,'meeting_recording'))=='P1001'
    assert grant(database,RECORDING_GRANT)==(None,True)
    assert database.execute("select count(*) from consent.grants where end_reason='withdrawn'").fetchone()==(0,)


def test_an_internal_note_never_reaches_the_client_or_the_rm(database: 'Connection[SqlRow]') -> None:
    reference=raise_ticket(database)
    note='Identity confirmed on the call by Sophie Lim.'
    database.execute('select support.add_internal_note(%s,%s)',(reference,note))
    assert database.execute("select e.status,e.internal from support.ticket_events e join support.tickets t using (ticket_id) where t.reference=%s and e.public_note=%s",
        (reference,note)).fetchone()==('submitted',True)  # the ticket keeps its status
    assert database.execute("select details from audit.events where action='handoff.ticket_note_added'").fetchone()==({'category':'consent_withdrawal','internal':True},)
    set_customer(database,1002)
    assert database.execute('select count(*) from support.ticket_events where public_note=%s',(note,)).fetchone()==(0,)
    mine=next(t for t in database.execute('select public.customer_read_tickets(1002)').fetchone()[0]['data'] if t['reference']==reference)
    assert note not in json.dumps(mine)
    as_rm(database)
    seen=database.execute('select public.rm_ticket(%s)',(reference,)).fetchone()[0]
    assert note not in json.dumps(seen) and seen['data']['internal_notes']==[]
    database.execute('reset role')
    for bad in ('',' '*3,'x'*501,'Card 4111 1111 1111 1111 was read out'):
        assert sqlstate(database,'select support.add_internal_note(%s,%s)',(reference,bad)) is not None,bad
    assert sqlstate(database,'select support.add_internal_note(%s,%s)',('TCK-999999','note'))=='P0002'


def test_her_rm_raises_and_follows_it_but_cannot_change_its_status(database: 'Connection[SqlRow]') -> None:
    as_rm(database)
    raised=database.execute('select public.rm_create_service_request(%s,1002,%s,%s,%s::jsonb,%s,true)',(str(uuid4()),'consent_withdrawal',
        'Michelle asked on the call to stop recording her meetings.',json.dumps({'what':'Meeting recordings'}),'normal')).fetchone()[0]
    assert raised['created'] is True and raised['owner_team']=='Data protection team'
    reference=raised['reference']
    database.execute('reset role')
    assert database.execute('select source,status from support.tickets where reference=%s',(reference,)).fetchone()==('rm','submitted')
    assert database.execute("select count(*) from notify.inbox where customer_id=1002 and template_key='rm.request_raised' and title like %s",
        (f'%{reference}%',)).fetchone()==(1,)
    as_rm(database)
    [listed]=[t for t in database.execute('select public.rm_tickets()').fetchone()[0]['data']['specialist'] if t['reference']==reference]
    assert listed['rm_owned'] is False
    assert sqlstate(database,'select public.rm_send_client_update(%s,1002,%s,%s,%s,1,false)',
        (uuid4(),reference,'Hi Michelle, I have passed this to the data protection team.','resolved'))=='42501'
    database.execute('select public.rm_send_client_update(%s,1002,%s,%s,null,1,false)',(uuid4(),reference,'Hi Michelle, I have passed this to the data protection team.'))
    database.execute('reset role')
    assert database.execute('select status from support.tickets where reference=%s',(reference,)).fetchone()==('submitted',)
    set_customer(database,1002)
    mine=next(t for t in database.execute('select public.customer_read_tickets(1002)').fetchone()[0]['data'] if t['reference']==reference)
    assert mine['last_update']['note'].startswith('Hi Michelle')
    assert withdraw(database,reference,'meeting_recording')['grants_ended']==1  # the team works it like her own request


def test_switching_off_on_data_sharing_stays_instant_with_a_plain_notice(database: 'Connection[SqlRow]') -> None:
    outcome=act(database,'consent.revoke',{'grant_id':MICHELLE_INSIGHTS_GRANT})
    assert outcome['command']['state']=='succeeded' and grant(database,MICHELLE_INSIGHTS_GRANT)==('revoked',False)
    assert database.execute("select count(*) from support.tickets where customer_id=1002 and category='consent_withdrawal'").fetchone()==(0,)  # no ticket
    delivered=database.execute("select notify.deliver(o) from events.outbox o where event_type='consent.revoked' and customer_id=1002").fetchone()[0]
    assert delivered['disposition']=='delivered'
    title,body,version=database.execute("select title,body,template_version from notify.inbox where customer_id=1002 and template_key='consent.revoked'").fetchone()
    assert (title,version)==('Sharing stopped',2) and 'stopped at once' in body and 'Records the bank must keep by law are kept' in body
    assert 'Synthetic' not in body and 'Data sharing' in body
