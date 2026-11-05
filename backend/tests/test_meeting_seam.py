"""Meeting seam (migration 0105) over real temporary PostgreSQL: consent by data category for the RM, and the private
meeting event queue (dedupe, payload rules, runner lease, retries, role boundaries).

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_meeting_seam.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from tests.database_support import SOPHIE_LOGIN,SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SOPHIE='00000000-0000-4000-8000-100200000002'
PRIYA='00000000-0000-4000-8000-100100000002'
CATEGORIES=('transaction_insights','chat_topics','meeting_recording')


def as_rm(db: 'Connection[SqlRow]') -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'app_metadata':{'demobank_role':'rm','rm_id':SOPHIE}}),))
    db.execute('set local role authenticated')


def grant(db: 'Connection[SqlRow]',customer: int,advisor: str,categories: list[str],expires: str='30 days',revoked: bool=False) -> None:
    db.execute("insert into consent.grants(customer_id,purpose,recipient,categories,granted_at,expires_at,revoked_at) "
        "values(%s,'rm_brief',%s,%s,now()-interval '1 day',now()+%s::interval,case when %s then now() end)",(customer,advisor,categories,expires,revoked))


def active(db: 'Connection[SqlRow]',customer: int,category: str,advisor: str=SOPHIE) -> bool:
    return db.execute('select rm.consent_active(%s,%s,%s)',(customer,category,advisor)).fetchone()[0]


def meeting(db: 'Connection[SqlRow]') -> str:
    db.execute("update meetings.events set status='done'")  # 0106 queues the demo meeting's event; start from an empty queue
    row=db.execute('select appointment_id from rm.appointments where customer_id=1002 and advisor_id=%s order by starts_at limit 1',(SOPHIE,)).fetchone()
    assert row,'0090 seeds Michelle\'s meetings with Sophie'
    return str(row[0])


def enqueue(db: 'Connection[SqlRow]',meeting_id: str,kind: str='meeting.scheduled',payload: dict|None=None,key: str|None=None) -> int:
    return db.execute('select meetings.enqueue(%s,%s,%s::jsonb,%s)',(kind,meeting_id,json.dumps(payload or {}),key)).fetchone()[0]


def claim(db: 'Connection[SqlRow]') -> tuple:
    return db.execute('select event_id,status,attempts from meetings.claim_next()').fetchone()


def test_consent_active_needs_an_active_grant_for_that_category_to_that_rm(database: 'Connection[SqlRow]') -> None:
    # Mei Ling (1007): Sophie's client with no category grant (0106 seeds one for Michelle).
    assert not any(active(database,1007,c) for c in CATEGORIES)
    grant(database,1007,SOPHIE,['transaction_insights'])
    assert active(database,1007,'transaction_insights') and not active(database,1007,'chat_topics')
    grant(database,1007,PRIYA,['chat_topics'])  # Priya is not Mei Ling's RM: a grant to her opens nothing
    assert not active(database,1007,'chat_topics',PRIYA) and not active(database,1007,'chat_topics')
    database.execute("update consent.grants set revoked_at=now() where customer_id=1007 and recipient=%s and 'transaction_insights'=any(categories)",(SOPHIE,))
    assert not active(database,1007,'transaction_insights')
    grant(database,1007,SOPHIE,['chat_topics'],expires='-1 minute')
    assert not active(database,1007,'chat_topics')
    database.execute('savepoint s')
    with pytest.raises(Exception,match='unknown consent category'):active(database,1007,'transactions')
    database.execute('rollback to savepoint s')


def test_rm_sees_consent_per_category_only_for_her_book(database: 'Connection[SqlRow]') -> None:
    grant(database,1007,SOPHIE,['chat_topics','meeting_recording'])
    as_rm(database)
    status=database.execute('select rm_consent_status(1007)').fetchone()[0]
    assert status['denied'] is False and status['data']=={'transaction_insights':False,'chat_topics':True,'meeting_recording':True}
    refused=database.execute('select rm_consent_status(1006)').fetchone()[0]
    assert refused['denied'] is True and refused['data'] is None
    database.execute('reset role')
    tools=[r[0] for r in database.execute("select details->>'tool' from audit.events where actor_type='staff' and details->>'tool'='rm.consent_status'")]
    assert len(tools)>=2
    set_customer(database,1002)
    with pytest.raises(Exception,match='relationship manager'):database.execute('select rm_consent_status(1002)')


def test_enqueue_dedupes_and_accepts_only_ids_codes_and_dates(database: 'Connection[SqlRow]') -> None:
    meeting_id=meeting(database)
    first=enqueue(database,meeting_id,payload={'starts_at':'2026-11-11T06:30:00+00:00','format':'video'},key=f'command:{meeting_id}')
    assert enqueue(database,meeting_id,key=f'command:{meeting_id}')==first
    row=database.execute('select customer_id,advisor_id::text,status,attempts from meetings.events where meeting_id=%s',(meeting_id,)).fetchall()
    assert row==[(1002,SOPHIE,'pending',0)]
    assert database.execute("select count(*) from audit.events where action='action.meeting_event_queued' and resource_id=%s",(str(first),)).fetchone()==(1,)
    for kind,payload,target,error in [('meeting.scheduled',{'note':'Michelle wants a home loan'},meeting_id,'ids, codes and dates'),
            ('meeting.unknown',{},meeting_id,'check constraint'),('meeting.scheduled',{},str(uuid4()),'meeting not found')]:
        database.execute('savepoint s')
        with pytest.raises(Exception,match=error):enqueue(database,target,kind,payload)
        database.execute('rollback to savepoint s')


def test_runner_claims_with_a_lease_and_retries_with_backoff(database: 'Connection[SqlRow]') -> None:
    meeting_id=meeting(database)
    first,second=enqueue(database,meeting_id),enqueue(database,meeting_id,'brief.requested')
    assert claim(database)==(first,'processing',1)
    database.execute('select meetings.finish(%s,true)',(first,))
    assert claim(database)==(second,'processing',1)
    database.execute("select meetings.finish(%s,false,'provider_unavailable')",(second,))
    assert database.execute('select status,last_error_code,available_at>clock_timestamp() from meetings.events where event_id=%s',(second,)).fetchone()==('pending','provider_unavailable',True)
    assert claim(database)==(None,None,None)  # backing off
    database.execute('update meetings.events set available_at=clock_timestamp() where event_id=%s',(second,))
    assert claim(database)==(second,'processing',2)
    database.execute("update meetings.events set available_at=clock_timestamp()-interval '1 second' where event_id=%s",(second,))  # lease ended
    assert claim(database)==(second,'processing',3)
    database.execute('update meetings.events set attempts=5 where event_id=%s',(second,))
    assert database.execute("select status from meetings.finish(%s,false,'provider_unavailable')",(second,)).fetchone()==('failed',)
    assert database.execute("select count(*) from audit.events where action='action.meeting_event_failed' and resource_id=%s",(str(second),)).fetchone()==(1,)
    for args,error in [((first,True,None),'not being processed'),((second,False,None),'needs an error code')]:
        database.execute('savepoint s')
        with pytest.raises(Exception,match=error):database.execute('select meetings.finish(%s,%s,%s)',args)
        database.execute('rollback to savepoint s')


def test_only_the_meeting_runner_role_reaches_the_queue(database: 'Connection[SqlRow]') -> None:
    meeting_id=meeting(database)
    for as_who in (lambda:set_customer(database,1002),lambda:as_rm(database)):
        for statement in ('select count(*) from meetings.events','select meetings.claim_next()'):
            database.execute('savepoint s')
            as_who()
            with pytest.raises(Exception,match='permission denied'):database.execute(statement)
            database.execute('rollback to savepoint s')
    database.execute('savepoint s')
    queued=enqueue(database,meeting_id,'brief.requested',key='provider:evt-1')
    database.execute('set local role demobank_meetings')
    assert claim(database)[0]==queued
    database.execute('savepoint no_enqueue')
    with pytest.raises(Exception,match='permission denied'):enqueue(database,meeting_id)
    database.execute('rollback to savepoint no_enqueue')
    with pytest.raises(Exception,match='permission denied'):database.execute('select count(*) from meetings.events')
    database.execute('rollback to savepoint s')
