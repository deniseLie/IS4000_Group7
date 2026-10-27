"""card.dispute over real temporary PostgreSQL: owned posted charges only, a required card answer, atomic receipt/audit/outbox.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_card_dispute.py -q
SQL claims are set directly (tests/database_support.py); the HTTP route and Data API boundary are covered by the action API
suites. The seed charges must fall inside the 120-day window, as for the other persona date checks.
"""
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import os
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from tests.database_support import SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SGT=ZoneInfo('Asia/Singapore')
TARGET={'card_id':'card_10011','transaction_ids':['txn_100116','txn_100111']}


def owned_case(db: 'Connection[SqlRow]',customer: int=1001) -> UUID:
    set_customer(db,customer)
    case_id=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    return case_id


def propose(db: 'Connection[SqlRow]',case_id: UUID,kind: str='card.dispute',target: object=TARGET) -> dict:
    return db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(target),
        f'{case_id}:t1_dispute:{kind}')).fetchone()[0]


def sqlstate(db: 'Connection[SqlRow]',query: str,params: tuple) -> str | None:
    import psycopg
    try:
        with db.transaction():db.execute(query,params)
    except psycopg.Error as error:return error.sqlstate
    return None


def test_impossible_travel_seed_reads_back_with_time_and_country(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1001)
    result=database.execute("select public.customer_read_transactions(1001,'2026-10-17','2026-10-17','{\"card_id\":\"card_10011\"}'::jsonb)").fetchone()[0]
    rows=[(row['txn_id'],datetime.fromisoformat(row['txn_at']).astimezone(SGT).strftime('%H:%M'),row['description'],row['amount'],row['merchant_country'],row['is_overseas']) for row in result['data']]
    assert rows==[('txn_100111','21:58','TECHVAULT ELECTRONICS',-2400.0,'US',True),
        ('txn_100116','21:42','SUNSET GRILL LA',-86.4,'US',True),
        ('txn_100115','19:15','Hawker Delight, Tanjong Pagar',-12.5,'SG',False)]
    assert database.execute('select outstanding_balance from banking.credit_cards where card_id=10011').fetchone()[0]==Decimal('2563.10')
    database.execute('reset role')
    from psycopg.errors import CheckViolation
    with pytest.raises(CheckViolation):
        with database.transaction():database.execute("update banking.transactions set merchant_country='usa' where transaction_id=100111")


def test_proposal_derives_the_charges_from_owned_records_in_date_order(database: 'Connection[SqlRow]') -> None:
    command=propose(database,owned_case(database))['command']
    target=command['target']
    assert command['state']=='proposed' and command['confirmation_input'] is None
    assert target['transaction_ids']==['txn_100111','txn_100116'] and target['last4']=='4821'
    assert [(item['transaction_id'],item['date'],item['merchant'],item['amount'],item['currency']) for item in target['items']]==[
        ('txn_100116','2026-10-17','SUNSET GRILL LA',-86.4,'SGD'),('txn_100111','2026-10-17','TECHVAULT ELECTRONICS',-2400.0,'SGD')]


def test_confirmation_requires_the_card_answer_and_records_it_atomically(database: 'Connection[SqlRow]') -> None:
    command=propose(database,owned_case(database))['command']
    command_id=command['command_id']
    assert sqlstate(database,'select public.action_confirm(%s)',(command_id,))=='22023'
    for answers in [None,{},{'card_in_possession':'maybe'},{'card_in_possession':'yes','note':'x'},['yes']]:
        assert sqlstate(database,'select public.action_confirm_with_input(%s,%s::jsonb)',(command_id,json.dumps(answers)))=='22023'
    assert database.execute('select state,confirmation_input from actions.commands where command_id=%s',(command_id,)).fetchone()==('proposed',None)
    outcome=database.execute('select public.action_confirm_with_input(%s,%s::jsonb)',(command_id,json.dumps({'card_in_possession':'yes'}))).fetchone()[0]
    receipt=outcome['receipt']
    assert outcome['command']['state']=='succeeded' and outcome['command']['confirmation_input']=={'card_in_possession':'yes'}
    assert receipt['outcome']=='succeeded' and receipt['result']['status']=='submitted_for_review' and receipt['result']['card_in_possession']=='yes'
    assert receipt['result']['items']==command['target']['items']
    database.execute('reset role')
    assert database.execute('select event_type from events.outbox where command_id=%s',(command_id,)).fetchall()==[('card.dispute_recorded',)]
    assert database.execute("select details from audit.events where action='action.card_dispute_recorded' and resource_id=%s",(command_id,)).fetchall()==[({'items':2},)]
    assert database.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    delivered=database.execute('select notify.deliver(o) from events.outbox o where command_id=%s',(command_id,)).fetchone()[0]
    assert delivered['disposition']=='delivered'
    assert database.execute('select title,body from notify.inbox where notification_id=%s',(delivered['notification_id'],)).fetchone()==(
        'Dispute draft recorded for review','Your dispute draft for 2 charges on the synthetic card ending 4821 is recorded for DemoBank staff review. '
        'Staff decide any refund or cancellation; nothing has been refunded. Open Actions for the receipt.')
    assert database.execute('select count(*) from support.handoffs').fetchone()==(0,)


@pytest.mark.parametrize('target,state',[({'card_id':'card_10011','transaction_ids':['txn_100112']},'42501'),
    ({'card_id':'card_10011','transaction_ids':['txn_100211']},'42501'),({'card_id':'card_10012','transaction_ids':['txn_100111']},'42501'),
    ({'card_id':'card_10011','transaction_ids':['txn_100111','txn_100111']},'22023'),
    ({'card_id':'card_10011','transaction_ids':[f'txn_{100100+i}' for i in range(11)]},'22023'),({'card_id':'card_10011','transaction_ids':[]},'22023'),
    ({'card_id':'card_10011'},'22023')])
def test_only_owned_posted_debits_on_the_named_card_can_be_disputed(database: 'Connection[SqlRow]',target: dict,state: str) -> None:
    case_id=owned_case(database)
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'card.dispute',json.dumps(target),
        f'{case_id}:t1_dispute:card.dispute'))==state


def test_another_customer_cannot_dispute_alexs_card(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database,1002)
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'card.dispute',json.dumps(TARGET),
        f'{case_id}:t1_dispute:card.dispute'))=='42501'


def test_a_changed_charge_fails_with_a_receipt_and_other_types_take_no_answers(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database)
    command_id=propose(database,case_id)['command']['command_id']
    block=propose(database,case_id,'card.temporary_block',{'card_id':'card_10011'})['command']['command_id']
    assert sqlstate(database,'select public.action_confirm_with_input(%s,%s::jsonb)',(block,json.dumps({'card_in_possession':'yes'})))=='22023'
    database.execute('reset role')
    database.execute("update banking.transactions set status='pending' where transaction_id=100116")
    set_customer(database,1001)
    outcome=database.execute('select public.action_confirm_with_input(%s,%s::jsonb)',(command_id,json.dumps({'card_in_possession':'no'}))).fetchone()[0]
    assert outcome['command']['state']=='failed' and outcome['receipt']['result']=={'error_code':'target_changed'}


def test_the_dispute_handoff_reason_is_registered_for_staff(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database)
    summary='Card ending 4821: the customer disputes 2 US charges, SUNSET GRILL LA and TECHVAULT ELECTRONICS. Staff decide refunds and cancellation.'
    outcome=propose(database,case_id,'handoff.create',{'reason':'card_dispute','priority':'P0','summary':summary})
    assert outcome['receipt']['outcome']=='succeeded'
    database.execute('reset role')
    assert database.execute('select reason,priority,summary from support.handoffs where case_id=%s',(case_id,)).fetchall()==[('card_dispute','P0',summary)]
    set_customer(database,1001)
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'handoff.create',
        json.dumps({'reason':'refund','priority':'P0','summary':summary}),f'{case_id}:t9:handoff.create'))=='22023'
