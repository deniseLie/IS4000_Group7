"""Storyline alignment over real temporary PostgreSQL (migrations 0115-0117 and 0118): private-client financing articles,
Michelle's advised portfolio and its RLS, the Lombard collateral alert on Today, the financing specialist request type,
the demo wording, and the Brief Writer's saved, consent-re-checked briefs.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_storyline_db.py -q
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
POINT='00000000-0000-4000-8000-100200000091'


def as_sophie(db: 'Connection[SqlRow]') -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'app_metadata':{'demobank_role':'rm','rm_id':SOPHIE}}),))
    db.execute('set local role authenticated')


def owner(db: 'Connection[SqlRow]') -> None:
    db.execute('reset role')


def meeting(db: 'Connection[SqlRow]') -> str:
    return str(db.execute("select appointment_id from rm.appointments where customer_id=1002 and purpose='Property financing options'").fetchone()[0])


def test_michelle_reads_only_her_own_portfolio(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    set_customer(database,1002)
    envelope=database.execute('select public.customer_read_portfolio(1002)').fetchone()[0]
    data=envelope['data']
    assert data['mandate']=='advised' and float(data['total_value'])==1180000.0 and data['holdings'][0]['description'].startswith('Time deposit ****4410, matures') and [h['asset_class'] for h in data['holdings']]==['cash','bonds','equities','funds']
    assert 'lending_value_pct' not in json.dumps(data)  # the bank's lending values stay with the bank and her RM
    set_customer(database,1001)
    assert database.execute('select public.customer_read_portfolio(1001)').fetchone()[0]['data'] is None
    assert database.execute('select count(*) from banking.portfolio_holdings').fetchone()[0]==0
    for query in ('select public.customer_read_portfolio(1002)','update banking.portfolio_holdings set market_value=0'):
        with pytest.raises(InsufficientPrivilege):  # another customer's scope (42501), and no write grant
            with database.transaction():database.execute(query)


def test_the_rm_sees_the_portfolio_and_a_collateral_alert_only_when_borrowing_exceeds_its_lending_value(database: 'Connection[SqlRow]') -> None:
    as_sophie(database)
    profile=database.execute('select public.rm_client_profile(1002)').fetchone()[0]['data']
    assert float(profile['portfolio']['total_value'])==1180000.0 and profile['financing'] is None
    owner(database)
    scenario=database.execute('select rm.demo_lombard_scenario()').fetchone()[0]
    assert scenario['collateral_status']=='below_limit' and float(scenario['portfolio']['shortfall'])==33000.0
    as_sophie(database)
    profile=database.execute('select public.rm_client_profile(1002)').fetchone()[0]['data']
    assert profile['financing']['collateral_status']=='below_limit' and float(profile['financing']['lending_value'])==687000.0
    titles=[f['title'] for f in database.execute('select public.rm_followups()').fetchone()[0]['data'] if f['customer_id']==1002 and f['kind']=='milestone']
    assert 'Lombard collateral below its lending value' in titles and 'Time deposit ****4410 matures' in titles
    owner(database)
    database.execute('select banking.reset_portfolio_demo()')
    assert database.execute("select count(*) from rm.client_milestones where customer_id=1002 and kind='collateral_alert'").fetchone()[0]==0
    assert [r[0] for r in database.execute("select action from audit.events where action like 'policy.collateral_alert%%' order by event_id")]==[
        'policy.collateral_alert','policy.collateral_alert_cleared']


def test_demo_helpers_are_owner_only(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    as_sophie(database)
    for query in ('select rm.demo_lombard_scenario()','select banking.reset_portfolio_demo()','select rm.check_collateral(1002)',
                  "select * from meetings.copilot_briefs"):
        with pytest.raises(InsufficientPrivilege):
            with database.transaction():database.execute(query)


def test_storyline_wording_meeting_and_specialist_request_type(database: 'Connection[SqlRow]') -> None:
    assert database.execute("select purpose,topics from rm.meeting_requests where request_id='00000000-0000-4000-8000-100200000071'").fetchone()==(
        'Property financing options',['home_loan','investments'])
    assert database.execute("select title from rm.tasks where customer_id=1002 and title like 'Send Michelle%%'").fetchone()[0]=='Send Michelle time deposit options'
    assert database.execute("select count(*) from rm.client_milestones where customer_id=1002 and title like 'Fixed deposit%%'").fetchone()[0]==0
    row=database.execute("select owner_team,customer_raisable,kb_article,jsonb_array_length(fields) from support.request_categories where category='financing_specialist'").fetchone()
    assert row==('Mortgage and Lombard specialists',True,'home-loans',3)
    result=database.execute('select rm.reset_meeting_demo()').fetchone()[0]
    assert result['portfolio']['holdings']==4
    assert database.execute("select purpose from rm.meeting_requests where request_id='00000000-0000-4000-8000-100200000071'").fetchone()[0]=='Property financing options'


def test_a_saved_brief_is_versioned_rechecked_on_open_and_approved_by_exact_version(database: 'Connection[SqlRow]') -> None:
    from psycopg import Error as DatabaseError  # P1001 (a replaced version) has no dedicated psycopg class
    mid=meeting(database)
    content=json.dumps({'summary':'Briefing','sections':[{'heading':'Who the client is','items':[]}],'sources':[]})
    as_sophie(database)
    upcoming={m['meeting_id']:m for m in database.execute('select public.rm_meeting_briefs()').fetchone()[0]['data']}
    assert upcoming[mid]['brief_version'] is None
    command=str(uuid4())
    first=database.execute('select public.rm_meeting_brief_save(%s,%s::jsonb,%s::uuid[],%s)',(mid,content,[POINT],command)).fetchone()[0]
    again=database.execute('select public.rm_meeting_brief_save(%s,%s::jsonb,null,%s)',(mid,content,command)).fetchone()[0]
    assert first['version']==1 and again=={**first,'replayed':True}
    brief=database.execute('select public.rm_meeting_brief(%s)',(mid,)).fetchone()[0]['data']['brief']
    assert set(brief['grants_used'])=={'chat_topics','transaction_insights'} and brief['withdrawn']==[] and brief['removed_points']==[] and not brief['stale']
    owner(database)
    database.execute("update consent.grants set revoked_at=now() where customer_id=1002 and 'chat_topics'=any(categories)")
    as_sophie(database)
    brief=database.execute('select public.rm_meeting_brief(%s)',(mid,)).fetchone()[0]['data']['brief']
    assert [w['category'] for w in brief['withdrawn']]==['chat_topics'] and [p['point_id'] for p in brief['removed_points']]==[POINT]
    with pytest.raises(DatabaseError,match='replaced brief'):
        with database.transaction():database.execute('select public.rm_meeting_brief_approve(%s,%s,2,%s)',(mid,brief['brief_id'],str(uuid4())))
    approved=database.execute('select public.rm_meeting_brief_approve(%s,%s,1,%s)',(mid,brief['brief_id'],str(uuid4()))).fetchone()[0]
    assert approved['status']=='approved'
    second=database.execute('select public.rm_meeting_brief_save(%s,%s::jsonb,null,%s)',(mid,content,str(uuid4()))).fetchone()[0]
    assert second['version']==2 and database.execute('select public.rm_meeting_brief(%s)',(mid,)).fetchone()[0]['data']['brief']['status']=='draft'


def test_briefs_are_refused_outside_her_meetings(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    mid=meeting(database)
    set_customer(database,1002)
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():database.execute('select public.rm_meeting_brief(%s)',(mid,))
    as_sophie(database)
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():database.execute('select public.rm_meeting_brief(%s)',(str(uuid4()),))
