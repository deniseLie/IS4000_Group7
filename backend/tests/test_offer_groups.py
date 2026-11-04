"""v5 offers on the Action Service (0100; build plan phase 4): ownership, TTL, ordered group confirmation, one record per
double confirm, a failure stops the chain, and v4 paused turns are expired. Real temporary PostgreSQL, synthetic data.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_offer_groups.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from tests.database_support import SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
CARD={'card_id':'card_10011'}
HANDOFF={'reason':'general_support','priority':'P3','summary':'Question about a card fee.'}


def owned_case(db: 'Connection[SqlRow]',customer: int=1001) -> tuple[UUID,UUID]:
    set_customer(db,customer)
    session_id,case_id=uuid4(),uuid4()
    db.execute('select public.action_create_case(%s,%s)',(session_id,case_id))
    return session_id,case_id


def offer(db: 'Connection[SqlRow]',case_id: UUID,kind: str,target: dict,group: UUID,order: int,task: str='t1',ttl: int=30,turn: UUID | None=None) -> dict:
    return db.execute('select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',
        (uuid4(),case_id,kind,json.dumps(target),f'{case_id}:{task}:{kind}',ttl,group,order,turn)).fetchone()[0]


def sqlstate(db: 'Connection[SqlRow]',query: str,params: tuple) -> str | None:
    import psycopg
    try:
        with db.transaction():db.execute(query,params)
    except psycopg.Error as error:return error.sqlstate
    return None


def states(db: 'Connection[SqlRow]',group: UUID) -> list[str]:
    return [row[0] for row in db.execute('select state from actions.commands where offer_group=%s order by group_order',(group,)).fetchall()]


def test_an_offer_never_executes_even_for_an_automatic_type(database: 'Connection[SqlRow]') -> None:
    session_id,case_id=owned_case(database)
    group=uuid4()
    result=offer(database,case_id,'handoff.create',HANDOFF,group,1)
    assert result['command']['state']=='proposed' and result['receipt'] is None
    assert result['command']['offer_group']==str(group) and result['command']['group_order']==1
    ttl=database.execute('select expires_at-proposed_at from actions.commands where offer_group=%s',(group,)).fetchone()[0]
    assert 29*60<=ttl.total_seconds()<=30*60+1
    assert database.execute('select count(*) from support.handoffs where case_id=%s',(case_id,)).fetchone()==(0,)
    assert offer(database,case_id,'handoff.create',HANDOFF,group,1)['command']['command_id']==result['command']['command_id']  # idempotent


@pytest.mark.parametrize('ttl,order',[(0,1),(61,1),(30,0),(30,11)])
def test_ttl_and_group_position_are_bounded(database: 'Connection[SqlRow]',ttl: int,order: int) -> None:
    _,case_id=owned_case(database)
    assert sqlstate(database,'select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',
        (uuid4(),case_id,'handoff.create',json.dumps(HANDOFF),f'{case_id}:t1:handoff.create',ttl,uuid4(),order,None))=='22023'


def test_offers_are_owned_and_a_group_stays_in_one_case(database: 'Connection[SqlRow]') -> None:
    session_id,case_id=owned_case(database)
    group=uuid4()
    offer(database,case_id,'handoff.create',HANDOFF,group,1)
    _,other_case=owned_case(database)
    assert sqlstate(database,'select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',
        (uuid4(),other_case,'card.temporary_block',json.dumps(CARD),f'{other_case}:t2:card.temporary_block',30,group,2,None))=='42501'
    assert sqlstate(database,'select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',
        (uuid4(),case_id,'card.temporary_block',json.dumps(CARD),f'{case_id}:t2:card.temporary_block',30,uuid4(),1,uuid4()))=='42501'  # not her turn
    set_customer(database,1002)
    assert database.execute('select public.customer_open_offers(%s)',(session_id,)).fetchone()[0]==[]
    assert sqlstate(database,'select public.offer_group_confirm(%s)',(group,))=='P0002'
    assert sqlstate(database,'select public.offer_group_cancel(%s)',(group,))=='P0002'
    set_customer(database,1001)
    opened=database.execute('select public.customer_open_offers(%s)',(session_id,)).fetchone()[0]
    assert [(o['offer_group'],o['group_order'],o['type']) for o in opened]==[(str(group),1,'handoff.create')]


def test_a_group_confirms_in_order_and_a_double_confirm_records_once(database: 'Connection[SqlRow]') -> None:
    session_id,case_id=owned_case(database)
    group=uuid4()
    offer(database,case_id,'handoff.create',HANDOFF,group,2,task='t2')
    offer(database,case_id,'card.temporary_block',CARD,group,1,task='t1')
    first=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert [o['command']['type'] for o in first]==['card.temporary_block','handoff.create']
    assert [o['command']['state'] for o in first]==['succeeded','succeeded'] and states(database,group)==['succeeded','succeeded']
    again=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert [o['receipt']['receipt_id'] for o in again]==[o['receipt']['receipt_id'] for o in first]
    assert database.execute('select count(*) from actions.receipts r join actions.commands c using(command_id) where c.offer_group=%s',(group,)).fetchone()==(2,)
    assert database.execute('select count(*) from support.handoffs where case_id=%s',(case_id,)).fetchone()==(1,)
    assert database.execute("select count(*) from audit.events where action='action.executed' and resource_id in "
        "(select command_id::text from actions.commands where offer_group=%s)",(group,)).fetchone()==(2,)
    assert database.execute('select public.customer_open_offers(%s)',(session_id,)).fetchone()[0]==[]


def test_a_failure_stops_the_chain_and_the_rest_fail_with_a_receipt(database: 'Connection[SqlRow]') -> None:
    _,case_id=owned_case(database)
    group=uuid4()
    offer(database,case_id,'card.temporary_block',CARD,group,1,task='t1')
    offer(database,case_id,'handoff.create',HANDOFF,group,2,task='t2')
    database.execute('reset role')
    database.execute("update banking.credit_cards set status='cancelled' where card_id=10011")  # the card changed after the offer
    set_customer(database,1001)
    outcomes=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert [o['command']['state'] for o in outcomes]==['failed','failed']
    assert outcomes[1]['receipt']['result']=={'error_code':'earlier_offer_failed'}
    assert database.execute('select count(*) from support.handoffs where case_id=%s',(case_id,)).fetchone()==(0,)


def test_expired_offers_expire_the_whole_group_and_cancel_rejects_open_ones(database: 'Connection[SqlRow]') -> None:
    session_id,case_id=owned_case(database)
    group,other=uuid4(),uuid4()
    offer(database,case_id,'handoff.create',HANDOFF,group,1,task='t1')
    offer(database,case_id,'card.temporary_block',CARD,group,2,task='t2')
    database.execute('reset role')
    database.execute("update actions.commands set expires_at=clock_timestamp()-interval '1 second' where offer_group=%s",(group,))
    set_customer(database,1001)
    assert database.execute('select public.customer_open_offers(%s)',(session_id,)).fetchone()[0]==[]
    assert [o['command']['state'] for o in database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]]==['expired','expired']
    offer(database,case_id,'handoff.create',HANDOFF,other,1,task='t3')
    assert [o['command']['state'] for o in database.execute('select public.offer_group_cancel(%s)',(other,)).fetchone()[0]]==['rejected']
    assert database.execute('select count(*) from support.handoffs where case_id=%s',(case_id,)).fetchone()==(0,)


def test_a_later_step_receives_the_earlier_receipt(database: 'Connection[SqlRow]') -> None:
    """A target that carries "after_receipt": null gets the previous step's receipt (phase 7 chains use it)."""
    _,case_id=owned_case(database)
    group=uuid4()
    offer(database,case_id,'handoff.create',HANDOFF,group,1,task='t1')
    second=offer(database,case_id,'card.temporary_block',CARD,group,2,task='t2')
    database.execute('reset role')
    database.execute("""update actions.commands set target=target||'{"after_receipt":null}' where command_id=%s""",(second['command']['command_id'],))
    set_customer(database,1001)
    outcomes=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert outcomes[1]['command']['target']['after_receipt']==outcomes[0]['receipt']['receipt_id']


def test_the_migration_expired_paused_v4_decisions(database: 'Connection[SqlRow]') -> None:
    assert database.execute("select count(*) from actions.commands c join actions.workflow_commands using(command_id) where c.state='proposed'").fetchone()==(0,)
    assert database.execute("select count(*) from conversation.agent_workflows where state='awaiting_confirmation'").fetchone()==(0,)
