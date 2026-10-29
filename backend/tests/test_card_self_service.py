"""Card unblock and permanent cancellation are the bank's (0081; client self-service from 0068 is refused). Real temporary PostgreSQL.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_card_self_service.py -q
SQL claims are set directly (tests/database_support.py). Staff unblock/cancel keep their own path; the last test
checks it still runs through the same type rows.
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


def owned_case(db: 'Connection[SqlRow]',customer: int=1001) -> UUID:
    set_customer(db,customer)
    case_id=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    return case_id


def propose(db: 'Connection[SqlRow]',case_id: UUID,kind: str,target: object=CARD,task: str='manage_card') -> dict:
    return db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(target),f'{case_id}:{task}:{kind}')).fetchone()[0]


def confirm(db: 'Connection[SqlRow]',command_id: str) -> dict:
    return db.execute('select public.action_confirm(%s)',(command_id,)).fetchone()[0]


def sqlstate(db: 'Connection[SqlRow]',query: str,params: tuple) -> str | None:
    import psycopg
    try:
        with db.transaction():db.execute(query,params)
    except psycopg.Error as error:return error.sqlstate
    return None


def blocked_by_the_bank(db: 'Connection[SqlRow]') -> UUID:
    """A blocked card with the urgent fraud-team handoff the chat records: the bank (here the database owner) blocked it."""
    case_id=owned_case(db)
    propose(db,case_id,'handoff.create',{'reason':'card_verification','priority':'P0','summary':'Card ending 4821: charges not recognised.'},'t1_fraud_team')
    db.execute('reset role')
    db.execute("update banking.credit_cards set status='temporarily_blocked' where card_id=10011")
    set_customer(db,1001)
    return case_id


@pytest.mark.parametrize('kind',['card.unblock','card.cancel_permanently','card.temporary_block','card.dispute'])
def test_a_customer_cannot_change_or_dispute_a_card(database: 'Connection[SqlRow]',kind: str) -> None:
    blocked_by_the_bank(database)
    case_id=owned_case(database)
    target={'card_id':'card_10011','transaction_ids':['txn_100111']} if kind=='card.dispute' else CARD
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(target),f'{case_id}:t:{kind}'))=='42501'
    database.execute('reset role')
    assert database.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked',)
    assert database.execute("select count(*) from actions.commands where type=%s",(kind,)).fetchone()==(0,)


def test_staff_unblock_still_runs_through_the_shared_type(database: 'Connection[SqlRow]') -> None:
    block_case=blocked_by_the_bank(database)
    database.execute('reset role')
    staff=uuid4()
    database.execute('insert into auth.users(id,email) values (%s,%s)',(staff,'staff@example.invalid'))
    handoff=database.execute('select handoff_id from support.handoffs where case_id=%s',(block_case,)).fetchone()[0]
    database.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(staff),'role':'authenticated','aal':'aal2',
        'app_metadata':{'demobank_role':'support'}}),))
    database.execute('set local role authenticated')
    database.execute('select public.staff_claim_handoff(%s,%s)',(uuid4(),handoff))
    outcome=database.execute('select public.staff_unblock_card(%s,%s,%s)',(uuid4(),handoff,'card_10011')).fetchone()[0]
    assert outcome['command']['target']['handoff_id']==str(handoff) and outcome['receipt']['result']=={'card_id':'card_10011','status':'active'}
    database.execute('reset role')
    assert database.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
    assert database.execute("select event_type from events.outbox where command_id=%s",(outcome['command']['command_id'],)).fetchall()==[('card.unblocked',)]
