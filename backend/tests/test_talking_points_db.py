"""Talking points over real temporary PostgreSQL (migration 0108, RM copilot build plan phase 4): RLS and the client-only
evidence (turns only since 0142), recording (consent recheck, her exact words, idempotency, merge), the 24-hour hold, the care pause, the consent
gate and its audits, her controls with receipts, lane 2 sharing with the notice and its limits, withdrawal, reassignment
and one client per call. Synthetic data only.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_talking_points_db.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from app.schemas.meetings import TalkingPoint
from tests.database_support import SqlRow,set_customer
from tests.test_meeting_requests import MICHELLE_GRANT,act
from tests.test_meeting_seam import SOPHIE,as_rm
from tests.test_offer_groups import sqlstate

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
MICHELLE_POINT='00000000-0000-4000-8000-100200000091'
MEI_LING_POINT='00000000-0000-4000-8000-100700000091'
ENVELOPE={'denied','shared','category','grant_id','customer_id','call_id','as_of','data','pending'}


def saved_turn(db: 'Connection[SqlRow]',text: str,customer: int=1002) -> UUID:
    """A saved chat turn of hers holding this redacted message (conversation_start_turn, 0013)."""
    set_customer(db,customer)
    session,turn=uuid4(),uuid4()
    revision=db.execute("select (public.conversation_create(%s,'personal','English',null))->>'revision'",(session,)).fetchone()[0]
    db.execute("select public.conversation_start_turn(%s,%s,%s,%s,'P3',%s,null)",(session,turn,int(revision),text,uuid4()))
    return turn


def record(db: 'Connection[SqlRow]',turn: UUID,topic: str,stage: str,quote: str,timeframe: str='unknown',concerns: list[str]|None=None) -> dict:
    return db.execute('select public.customer_record_talking_point(%s,%s,%s,%s,%s,%s)',(turn,topic,stage,timeframe,concerns or [],quote)).fetchone()[0]


def propose(db: 'Connection[SqlRow]',kind: str,target: dict,customer: int=1002) -> dict:
    """An automatic action from Data sharing: it runs at once and returns its receipt."""
    set_customer(db,customer)
    session_id,case_id=uuid4(),uuid4()
    db.execute('select public.action_create_case(%s,%s)',(session_id,case_id))
    return db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(target),f'{case_id}:t1:{kind}')).fetchone()[0]


def refused(db: 'Connection[SqlRow]',kind: str,target: dict,customer: int=1002) -> str | None:
    set_customer(db,customer)
    case=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    return sqlstate(db,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case,kind,json.dumps(target),f'{case}:t1:{kind}'))


def rm_points(db: 'Connection[SqlRow]',customer: int=1002,purpose: str='meeting_prep') -> dict:
    as_rm(db)
    return db.execute('select public.rm_talking_points(%s,%s)',(customer,purpose)).fetchone()[0]


def owner(db: 'Connection[SqlRow]') -> 'Connection[SqlRow]':
    db.execute('reset role')
    return db


def test_her_points_are_hers_and_the_evidence_is_client_only(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1002)
    assert database.execute('select topic,origin from insights.talking_points').fetchall()==[('home_loan','inferred')]
    assert sqlstate(database,'select * from insights.talking_point_evidence',())=='42501'
    set_customer(database,1001)
    assert database.execute('select count(*) from insights.talking_points').fetchone()==(0,)
    as_rm(database)
    assert database.execute('select count(*) from insights.talking_points').fetchone()==(0,)  # an RM login has no customer rows
    assert sqlstate(database,'select * from insights.talking_point_evidence',())=='42501'
    assert sqlstate(database,'select * from rm.topic_shares',())=='42501'
    owner(database)
    roles=[r for (r,) in database.execute("select rolname from pg_roles where rolname in ('anon','authenticated','demobank_meetings','demobank_worker')").fetchall()]
    for role in roles:
        assert not database.execute("select has_table_privilege(%s,'insights.talking_point_evidence','select')",(role,)).fetchone()[0],role
        assert not database.execute("select has_table_privilege(%s,'insights.share_prompts','select')",(role,)).fetchone()[0],role
    # No RM-side function reads the evidence: every rm_* function and everything in schema rm.
    readers=database.execute("""select p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where (n.nspname='public' and p.proname like 'rm\\_%%') or n.nspname='rm'""").fetchall()
    assert len(readers)>20
    assert database.execute("""select array_agg(p.proname) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where ((n.nspname='public' and p.proname like 'rm\\_%%') or n.nspname='rm') and p.prosrc like '%%talking_point_evidence%%'""").fetchone()==(None,)
    assert database.execute("select count(*) from information_schema.schemata where schema_name='insights'").fetchone()==(1,)


def test_recording_rechecks_her_words_and_merges_into_one_point_per_topic(database: 'Connection[SqlRow]') -> None:
    turn=saved_turn(database,'I want to invest $50,000 in unit trusts next year and I worry about fees')
    first=record(database,turn,'investments','planning','invest $50,000 in unit trusts next year',timeframe='within_12m',concerns=['fees'])
    assert first['recorded'] is True and first['created'] is True
    assert record(database,turn,'investments','planning','invest $50,000 in unit trusts next year')=={'recorded':False,'reason':'duplicate',
        'point_id':first['point_id']}  # idempotent on (turn, topic)
    assert sqlstate(database,'select public.customer_record_talking_point(%s,%s,%s,%s,%s,%s)',
        (turn,'investments','planning','unknown',[],'invest in crypto'))=='22023'  # not her words
    assert sqlstate(database,'select public.customer_record_talking_point(%s,%s,%s,%s,%s,%s)',
        (turn,'pension','planning','unknown',[],'unit trusts'))=='22023'  # not a listed topic
    owner(database)
    assert database.execute('select turn_id from insights.talking_point_evidence where point_id=%s',(first['point_id'],)).fetchone()==(turn,)
    assert not database.execute("select exists(select 1 from information_schema.columns where table_schema='insights' "
        "and table_name='talking_point_evidence' and column_name='quote')").fetchone()[0]  # 0142: her words are never kept
    point=database.execute('select stage,timeframe,concerns,visible_from>now()+interval \'23 hours\' from insights.talking_points where point_id=%s',
        (first['point_id'],)).fetchone()
    assert point==('planning','within_12m',['fees'],True)  # pending for 24 hours
    # Merge: the stage only moves up, concerns add up, last_seen moves on, one evidence row per turn.
    for stage,quote in [('ready','ready to invest'),('curious','invest'),('exploring','unit trusts'),('exploring','invest more')]:
        record(database,saved_turn(database,f'I am {quote} now'),'investments',stage,quote,concerns=['rates'])
    owner(database)
    assert database.execute('select stage,concerns from insights.talking_points where point_id=%s',(first['point_id'],)).fetchone()==('ready',['fees','rates'])
    assert database.execute('select count(*) from insights.talking_point_evidence where point_id=%s',(first['point_id'],)).fetchone()==(5,)
    # Another client's turn, and a client without chat_topics.
    set_customer(database,1001)
    assert sqlstate(database,'select public.customer_record_talking_point(%s,%s,%s,%s,%s,%s)',(turn,'investments','planning','unknown',[],'invest'))=='42501'
    alex=saved_turn(database,'I am planning to buy a condo',customer=1001)
    assert record(database,alex,'home_loan','planning','buy a condo')['reason']=='no_consent'
    owner(database)
    assert database.execute("select details from audit.events where action='data.talking_point_skipped' and resource_id=%s",(str(alex),)).fetchone()==(
        {'topic':'home_loan','reason':'no_consent'},)
    assert database.execute('select count(*) from insights.talking_points where customer_id=1001').fetchone()==(0,)


def test_the_rm_sees_inferred_points_only_under_consent_after_the_hold_and_not_while_paused(database: 'Connection[SqlRow]') -> None:
    turn=saved_turn(database,'Thinking about a fixed deposit')
    held=record(database,turn,'fixed_deposit','exploring','fixed deposit')['point_id']
    shown=rm_points(database)
    assert set(shown)==ENVELOPE and shown['shared'] is True and shown['grant_id']==MICHELLE_GRANT and shown['pending'] is True
    assert [p['topic'] for p in shown['data']]==['home_loan']  # the new point is on its 24-hour hold
    for row in shown['data']:TalkingPoint.model_validate(row)  # exactly the contract: list values, dates and ids
    owner(database).execute("update insights.talking_points set visible_from=now()-interval '1 minute' where point_id=%s",(held,))
    after=rm_points(database)
    assert sorted(p['topic'] for p in after['data'])==['fixed_deposit','home_loan'] and after['pending'] is False
    # A care signal pauses product topics; a confirmed point still shows.
    set_customer(database,1002)
    database.execute('select public.customer_pause_suggestions(%s)',(saved_turn(database,'I just lost my job, should I still take a loan?'),))
    owner(database).execute("insert into insights.talking_points(customer_id,topic,stage,origin,visible_from,expires_at) "
        "values(1002,'insurance','planning','confirmed',now(),now()+interval '90 days')")
    paused=rm_points(database)
    assert [(p['topic'],p['origin']) for p in paused['data']]==[('insurance','confirmed')] and paused['pending'] is True
    owner(database)
    assert database.execute("select details from audit.events where action='data.suggestions_paused' order by event_id desc limit 1").fetchone()==({'days':60},)
    assert database.execute('select count(*) from insights.talking_points where customer_id=1002').fetchone()==(3,)  # nothing deleted, only hidden
    # A paused client: recording stops too.
    set_customer(database,1002)
    assert record(database,saved_turn(database,'Ready to invest'),'investments','ready','invest')['reason']=='paused'


def test_without_chat_topics_only_her_shared_points_reach_the_rm_audited_as_client_shared(database: 'Connection[SqlRow]') -> None:
    shared=act(database,'insight.share',{'topic':'insurance','stage':'planning','timeframe':'within_3m'})
    assert shared['receipt']['outcome']=='succeeded' and shared['receipt']['result']['origin']=='confirmed'
    command=shared['command']['command_id']
    owner(database).execute('update consent.grants set revoked_at=now() where grant_id=%s',(MICHELLE_GRANT,))
    gap=rm_points(database,purpose='client_view')
    assert gap['shared'] is False and gap['pending'] is False and [(p['topic'],p['origin']) for p in gap['data']]==[('insurance','confirmed')]
    owner(database)
    events=dict(database.execute('select action,details from audit.events where resource_id=%s',(gap['call_id'],)).fetchall())
    assert events['policy.rm_consent_gap']=={'tool':'rm.talking_points','category':'chat_topics','purpose':'client_view','reason':'no_active_consent'}
    assert events['data.read']=={'tool':'rm.talking_points','result_count':1,'basis':'client_shared','command_ids':[command],'purpose':'client_view'}


def test_withdrawing_chat_topics_removes_inferred_points_and_all_evidence_and_keeps_confirmed_ones(database: 'Connection[SqlRow]') -> None:
    act(database,'insight.share',{'topic':'insurance','stage':'planning','timeframe':'unknown'})
    record(database,saved_turn(database,'Planning to invest'),'investments','planning','invest')
    owner(database)
    assert database.execute('select count(*) from insights.talking_point_evidence where customer_id=1002').fetchone()==(2,)
    database.execute('update consent.grants set revoked_at=now() where grant_id=%s',(MICHELLE_GRANT,))
    assert database.execute('select topic,origin from insights.talking_points where customer_id=1002 and deleted_at is null').fetchall()==[('insurance','confirmed')]
    # 0110: removed softly (purged after 7 days), her words at once
    assert database.execute("select count(*) from insights.talking_points where customer_id=1002 and deleted_reason='chat_topics_revoked'").fetchone()==(2,)
    assert database.execute('select count(*) from insights.talking_point_evidence where customer_id=1002').fetchone()==(0,)
    assert database.execute("select details from audit.events where action='data.talking_points_forgotten' order by event_id desc limit 1").fetchone()==(
        {'points':2,'evidence':2},)


def test_her_controls_run_at_once_and_leave_receipts(database: 'Connection[SqlRow]') -> None:
    deleted=propose(database,'insight.delete',{'point_id':MICHELLE_POINT})
    result=deleted['receipt']['result']
    assert deleted['command']['state']=='succeeded' and {k:result[k] for k in ('point_id','topic','status')}=={'point_id':MICHELLE_POINT,'topic':'home_loan','status':'deleted'}
    owner(database)
    # 0110: removed softly with a 7-day undo; its evidence row stays until the purge (never shown to the RM)
    assert database.execute("select deleted_reason,purge_after>now()+interval '6 days' from insights.talking_points where point_id=%s",
        (MICHELLE_POINT,)).fetchone()==('client',True)
    assert database.execute('select count(*) from insights.talking_point_evidence where point_id=%s',(MICHELLE_POINT,)).fetchone()==(1,)
    assert refused(database,'insight.delete',{'point_id':MEI_LING_POINT},customer=1001)=='42501'  # not hers
    record(database,saved_turn(database,'Planning to invest'),'investments','planning','invest')
    muted=propose(database,'insight.mute',{'topic':'investments'})
    assert muted['receipt']['result']=={'topic':'investments','status':'muted','removed':1}
    assert record(database,saved_turn(database,'Ready to invest'),'investments','ready','invest')['reason']=='muted'
    assert propose(database,'insight.resume',{'topic':'investments'})['receipt']['result']=={'topic':'investments','status':'resumed'}
    set_customer(database,1002)
    database.execute('select public.customer_pause_suggestions(%s)',(saved_turn(database,'I was retrenched'),))
    assert propose(database,'insight.resume',{'topic':None})['receipt']['result']=={'topic':None,'status':'resumed'}
    owner(database)
    assert database.execute('select count(*) from insights.opportunity_pauses where customer_id=1002 and ended_at is null').fetchone()==(0,)
    assert database.execute("""select array_agg(c.type order by c.type) from actions.commands c join actions.receipts r using(command_id)
        where c.customer_id=1002 and c.type like 'insight.%%' and r.outcome='succeeded'""").fetchone()==(
        ['insight.delete','insight.mute','insight.resume','insight.resume'],)
    assert refused(database,'insight.resume',{'topic':None})=='22023'  # nothing is paused any more


def test_sharing_creates_a_confirmed_point_on_todays_list_with_a_notice_that_names_no_topic(database: 'Connection[SqlRow]') -> None:
    owner(database).execute('update consent.grants set revoked_at=now() where grant_id=%s',(MICHELLE_GRANT,))  # lane 2 needs no chat_topics
    shared=act(database,'insight.share',{'topic':'home_loan','stage':'planning','timeframe':'within_12m'})
    assert shared['receipt']['result']['notified'] is True
    as_rm(database)
    today=database.execute('select public.rm_shared_topics()').fetchone()[0]
    rows={r['customer_id']:r for r in today['data']}
    assert set(rows)=={1002,1007} and all(r['point']['origin']=='confirmed' for r in today['data'])
    michelle=rows[1002]
    assert michelle['notice']=='Michelle Ng shared a topic with you. Open your workspace.'
    assert TalkingPoint.model_validate(michelle['point']).line()=='Home loan · planning · within 12 months · shared by the client'
    owner(database)
    assert database.execute("select details from audit.events where action='data.read' and resource_id=%s",(today['call_id'],)).fetchone()[0]=={
        'tool':'rm.shared_topics','result_count':2,'basis':'client_shared','command_ids':[shared['command']['command_id']]}
    # One prompt per topic per 30 days; a muted topic or a pause refuses the offer.
    assert refused(database,'insight.share',{'topic':'home_loan','stage':'ready','timeframe':'now'})=='22023'
    propose(database,'insight.mute',{'topic':'insurance'})
    assert refused(database,'insight.share',{'topic':'insurance','stage':'planning','timeframe':'unknown'})=='22023'
    status=database.execute('select public.customer_topic_status(1002)').fetchone()[0]['data']
    assert status=={'chat_topics':False,'paused':False,'has_rm':True,'rm_first_name':'Sophie','muted':['insurance'],'prompted':['home_loan'],
        'meeting_topics':[]}


def test_sharing_upgrades_an_inferred_point_with_only_what_she_confirmed_and_notices_stop_at_20_a_day(database: 'Connection[SqlRow]') -> None:
    owner(database)
    first_seen=database.execute('select first_seen from insights.talking_points where point_id=%s',(MICHELLE_POINT,)).fetchone()[0]
    for _ in range(20):
        database.execute("insert into rm.topic_shares(advisor_id,customer_id,point_id,notice) values(%s,1007,%s,true)",(SOPHIE,MEI_LING_POINT))
    shared=act(database,'insight.share',{'topic':'home_loan','stage':'ready','timeframe':'now'})
    assert shared['receipt']['result']=={'point_id':MICHELLE_POINT,'topic':'home_loan','stage':'ready','timeframe':'now','origin':'confirmed','notified':False}
    owner(database)
    point=database.execute('select origin,stage,timeframe,concerns,first_seen>%s,share_command_id::text from insights.talking_points where point_id=%s',
        (first_seen,MICHELLE_POINT)).fetchone()
    assert point==('confirmed','ready','now',[],True,shared['command']['command_id'])


def test_reassignment_ends_access_and_each_read_is_one_client(database: 'Connection[SqlRow]') -> None:
    mei_ling=rm_points(database,1007)
    assert mei_ling['customer_id']==1007 and [p['point_id'] for p in mei_ling['data']]==[MEI_LING_POINT]
    assert database.execute("select public.rm_talking_points(null,'meeting_prep')").fetchone()[0]['denied'] is True
    assert rm_points(database,1001)['denied'] is True  # not in her book
    owner(database).execute('update rm.assignments set ended_on=rm.today() where customer_id=1002 and advisor_id=%s and ended_on is null',(SOPHIE,))
    assert rm_points(database)['denied'] is True
    today=database.execute('select public.rm_shared_topics()').fetchone()[0]
    assert {r['customer_id'] for r in today['data']}=={1007}
    set_customer(database,1002)
    assert record(database,saved_turn(database,'Planning to invest'),'investments','planning','invest')['reason']=='no_consent'


def test_her_own_view_shows_the_hold_mutes_and_the_pause_and_never_her_words(database: 'Connection[SqlRow]') -> None:
    record(database,saved_turn(database,'Planning to invest in ETFs'),'investments','planning','invest in ETFs')
    set_customer(database,1002)
    mine=database.execute('select public.customer_read_talking_points(1002)').fetchone()[0]
    data=mine['data']
    assert data['chat_topics'] is True and data['rm_first_name']=='Sophie' and data['muted']==[] and data['pause'] is None
    points={p['topic']:p for p in data['points']}
    assert points['investments']['pending'] is True and points['home_loan']['pending'] is False
    assert 'evidence' not in points['investments'] and 'invest in ETFs' not in json.dumps(mine)  # 0142: no words in her view
    assert sqlstate(database,'select public.customer_read_talking_points(1001)',())=='42501'
