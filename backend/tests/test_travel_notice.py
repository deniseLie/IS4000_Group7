"""Phase 7 SQL (migration 0101): the travel_notice request category with its trip check, and a chained offer that keeps
its after_receipt mark through the type's validator. Real temporary PostgreSQL, synthetic data.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_travel_notice.py -q
"""
import json
import os
from datetime import date,timedelta
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from tests.database_support import SqlRow,set_customer
from tests.test_offer_groups import HANDOFF,offer,owned_case,sqlstate

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')


def trip(start: int,end: int) -> dict:
    today=date.today()
    return {'card':'4821','from_date':(today+timedelta(days=start)).isoformat(),'to_date':(today+timedelta(days=end)).isoformat()}


def notice(details: dict) -> dict:
    return {'category':'travel_notice','urgency':'normal','summary':'Travel notice for my card.','details':details}


def test_a_confirmed_travel_notice_raises_one_ticket_for_an_owned_card(database: 'Connection[SqlRow]') -> None:
    assert database.execute("select customer_raisable,target_hours from support.request_categories where category='travel_notice'").fetchone()==(True,24)
    _,case_id=owned_case(database)
    group=uuid4()
    offered=offer(database,case_id,'ticket.create',notice({**trip(10,20),'destination':'Japan'}),group,1)
    assert offered['command']['state']=='proposed'
    [outcome]=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert outcome['command']['state']=='succeeded' and outcome['receipt']['result']['category']=='travel_notice'
    row=database.execute("select details->>'card',details->>'destination',source from support.tickets where case_id=%s",(case_id,)).fetchone()
    assert row==('4821','Japan','chat')


@pytest.mark.parametrize('details',[trip(20,10),trip(10,200),{**trip(10,20),'card':'0000'},{**trip(10,20),'seat':'aisle'}])
def test_a_travel_notice_needs_a_real_trip_on_an_owned_card(database: 'Connection[SqlRow]',details: dict) -> None:
    _,case_id=owned_case(database)
    group=uuid4()
    refused=sqlstate(database,'select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',
        (uuid4(),case_id,'ticket.create',json.dumps(notice(details)),f'{case_id}:t1:ticket.create',30,group,1,None))
    if refused is None:  # the trip check runs when the ticket is written
        [outcome]=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
        assert outcome['command']['state']=='failed'
    assert database.execute('select count(*) from support.tickets where case_id=%s',(case_id,)).fetchone()==(0,)


def test_the_trip_check_guards_the_table_itself(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1001)
    database.execute('reset role')
    assert sqlstate(database,"""insert into support.tickets(customer_id,category,summary,details,urgency,owner_team,decided_by,source,target_at)
        values(1001,'travel_notice','x',%s::jsonb,'normal','t','d','seed',now())""",(json.dumps(trip(20,10)),))=='22023'


def test_a_chained_offer_keeps_its_mark_and_receives_the_earlier_receipt(database: 'Connection[SqlRow]') -> None:
    _,case_id=owned_case(database)
    group=uuid4()
    offer(database,case_id,'handoff.create',HANDOFF,group,1,task='t1')
    second=offer(database,case_id,'handoff.create',{**HANDOFF,'after_receipt':None},group,2,task='t2')
    assert second['command']['target'].get('after_receipt','missing') is None
    plain=offer(database,case_id,'ticket.create',notice(trip(10,20)),group,3,task='t3')
    assert 'after_receipt' not in plain['command']['target']
    outcomes=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert [o['command']['state'] for o in outcomes]==['succeeded','succeeded','succeeded']
    assert outcomes[1]['command']['target']['after_receipt']==outcomes[0]['receipt']['receipt_id']
