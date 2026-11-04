"""Card disputes over real temporary PostgreSQL: bank-only since 0081; the travel seed and the staff dispute handoff reason remain.

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


def test_the_client_cannot_propose_a_dispute_draft(database: 'Connection[SqlRow]') -> None:
    """Disputes are bank-only since 0081: since build plan 2b the charge goes to her RM as one urgent request instead (below)."""
    for customer in (1001,1002):
        case_id=owned_case(database,customer)
        assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'card.dispute',json.dumps(TARGET),
            f'{case_id}:t1_dispute:card.dispute'))=='42501'


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


def test_a_confirmed_urgent_card_request_lands_first_in_the_rms_ticket_inbox(database: 'Connection[SqlRow]') -> None:
    """Build plan 2b: the code-built urgent target (rm_handover.urgent_target), offered and confirmed as in a chat, opens an
    urgent rm_handover ticket (0082 trigger) that Sophie sees at the top of her own inbox (0089 rm_tickets)."""
    from app.services import rm_handover
    from tests.test_rm_workspace import SOPHIE,as_rm
    case_id,group=owned_case(database,1002),uuid4()
    target=rm_handover.urgent_target('charge','eh there is a charge in London I did not make','card ending 7310 (active)')
    key=f'{case_id}:t1:handoff.create'
    database.execute('select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',(uuid4(),case_id,'handoff.create',json.dumps(target),key,30,group,1,None))
    database.execute('reset role')
    assert database.execute('select count(*) from support.tickets where case_id=%s',(case_id,)).fetchone()==(0,)  # nothing before she confirms
    set_customer(database,1002)
    assert [o['command']['state'] for o in database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]]==['succeeded']
    database.execute('reset role')
    category,urgency,summary,reference=database.execute('select category,urgency,summary,reference from support.tickets where case_id=%s',(case_id,)).fetchone()
    assert (category,urgency)==('rm_handover','urgent') and summary==target['summary'] and summary.startswith('[Urgent card] Charge')
    assert rm_handover.urgent(summary) and reference.startswith('TCK-')
    as_rm(database,rm_id=SOPHIE)
    owned=call_rm_tickets(database)['rm_owned']
    mine=next(i for i,t in enumerate(owned) if t['reference']==reference)
    assert owned[mine]['customer_id']==1002 and owned[mine]['summary']==summary and all(t['urgency']=='urgent' for t in owned[:mine+1])


def test_after_an_rm_request_in_the_same_chat_the_urgent_yes_returns_that_handoff(database: 'Connection[SqlRow]') -> None:
    """Why orchestrator.outcome_text checks `created` (rm_handover.urgent_elsewhere): one open general_support handoff per case."""
    from app.services import rm_handover
    case_id,group=owned_case(database,1002),uuid4()
    assert propose(database,case_id,'handoff.create',{'reason':'general_support','priority':'P3','summary':'[Fees or rates] Client question for you: waive my fee'})['receipt']['outcome']=='succeeded'
    target=rm_handover.urgent_target('lost','i lost my card')
    database.execute('select public.action_offer(%s,%s,%s,%s::jsonb,%s,%s,%s,%s::smallint,%s)',(uuid4(),case_id,'handoff.create',json.dumps(target),
        f'{case_id}:lost_card:handoff.create',30,group,1,None))
    confirmed=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0][0]
    assert confirmed['receipt']['outcome']=='succeeded' and confirmed['receipt']['result']['created'] is False
    database.execute('reset role')
    assert database.execute("select count(*) from support.tickets where case_id=%s and urgency='urgent'",(case_id,)).fetchone()==(0,)


def call_rm_tickets(db: 'Connection[SqlRow]') -> dict:
    return db.execute('select public.rm_tickets()').fetchone()[0]['data']
