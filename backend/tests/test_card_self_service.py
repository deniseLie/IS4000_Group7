"""Customer card unblock and permanent cancellation over real temporary PostgreSQL (migrations 0068-0069).

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


def card_status(db: 'Connection[SqlRow]') -> str:
    db.execute('reset role')
    status=db.execute('select status from banking.credit_cards where card_id=10011').fetchone()[0]
    set_customer(db,1001)
    return status


def blocked(db: 'Connection[SqlRow]') -> UUID:
    """A confirmed fraud block in its own case, with the card_verification handoff the workflow records."""
    case_id=owned_case(db)
    confirm(db,propose(db,case_id,'card.temporary_block',task='t1')['command']['command_id'])
    propose(db,case_id,'handoff.create',{'reason':'card_verification','priority':'P0','summary':'Card ending 4821: verify the blocked card.'},'t1_handoff')
    return case_id


def test_customer_unblocks_their_own_blocked_card_with_confirmation_and_staff_see_it(database: 'Connection[SqlRow]') -> None:
    block_case=blocked(database)
    assert card_status(database)=='temporarily_blocked'
    proposed=propose(database,owned_case(database),'card.unblock')
    command=proposed['command']
    assert command['state']=='proposed' and proposed['receipt'] is None and command['target']=={'card_id':'card_10011','last4':'4821','from_status':'temporarily_blocked'}
    assert card_status(database)=='temporarily_blocked'
    outcome=confirm(database,command['command_id'])
    result=outcome['receipt']['result']
    assert outcome['command']['state']=='succeeded' and result['status']=='active' and result['from_status']=='temporarily_blocked'
    assert card_status(database)=='active'
    database.execute('reset role')
    handoff=database.execute("select handoff_id,status from support.handoffs where case_id=%s",(block_case,)).fetchone()
    assert handoff[1]=='open' and result['handoffs_noted']==[str(handoff[0])]
    assert database.execute('select kind,card_last4 from support.handoff_notes where handoff_id=%s',(handoff[0],)).fetchall()==[('card_unblocked_by_customer','4821')]
    assert database.execute('select event_type from events.outbox where command_id=%s',(command['command_id'],)).fetchall()==[('card.unblocked_by_customer',)]
    assert database.execute("select details from audit.events where action='action.card_unblocked_by_customer'").fetchall()==[({'handoffs_noted':1},)]
    delivered=database.execute('select notify.deliver(o) from events.outbox o where command_id=%s',(command['command_id'],)).fetchone()[0]
    assert database.execute('select title from notify.inbox where notification_id=%s',(delivered['notification_id'],)).fetchone()==('You unblocked your card',)
    staff=uuid4()
    database.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(staff),'role':'authenticated','aal':'aal2',
        'app_metadata':{'demobank_role':'support'}}),))
    database.execute('set local role authenticated')
    queue=database.execute('select public.staff_handoff_queue(10)').fetchone()[0]
    assert [item['kind'] for row in queue if row['handoff_id']==str(handoff[0]) for item in row['customer_updates']]==['card_unblocked_by_customer']
    database.execute('reset role')
    assert database.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_permanent_cancellation_is_terminal_and_other_moves_are_refused(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database)
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'card.unblock',json.dumps(CARD),f'{case_id}:u1:card.unblock'))=='22023'
    command=propose(database,case_id,'card.cancel_permanently')['command']
    assert command['target']['from_status']=='active'
    assert confirm(database,command['command_id'])['receipt']['result']['status']=='cancelled' and card_status(database)=='cancelled'
    for kind,task in [('card.unblock','u2'),('card.cancel_permanently','c2')]:
        assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(CARD),f'{case_id}:{task}:{kind}'))=='22023',kind
    # A block is checked when it runs (0019): on a cancelled card it records a failed receipt and changes nothing.
    block=propose(database,case_id,'card.temporary_block',task='b2')['command']
    assert confirm(database,block['command_id'])['receipt']['result']=={'error_code':'target_changed'}
    assert card_status(database)=='cancelled'


def test_another_customer_cannot_change_alexs_card_or_use_the_staff_path(database: 'Connection[SqlRow]') -> None:
    blocked(database)
    case_id=owned_case(database,1002)
    for kind in ['card.unblock','card.cancel_permanently']:
        assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(CARD),f'{case_id}:x:{kind}'))=='42501'
    case_id=owned_case(database)
    staff_target={'card_id':'card_10011','handoff_id':str(uuid4())}
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'card.unblock',json.dumps(staff_target),f'{case_id}:s:card.unblock'))=='42501'
    assert card_status(database)=='temporarily_blocked'


def test_idempotency_and_an_optimistic_status_check(database: 'Connection[SqlRow]') -> None:
    blocked(database)
    case_id=owned_case(database)
    first=propose(database,case_id,'card.unblock')['command']
    assert propose(database,case_id,'card.unblock')['command']['command_id']==first['command_id']
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'card.unblock',json.dumps({'card_id':'card_10012'}),
        f'{case_id}:manage_card:card.unblock'))=='23505'
    cancel=propose(database,owned_case(database),'card.cancel_permanently')['command']
    assert cancel['target']['from_status']=='temporarily_blocked'
    confirm(database,first['command_id'])
    outcome=confirm(database,cancel['command_id'])
    assert outcome['command']['state']=='failed' and outcome['receipt']['result']=={'error_code':'target_changed'} and card_status(database)=='active'
    assert confirm(database,first['command_id'])['receipt']['outcome']=='succeeded'


@pytest.mark.parametrize('table',['events.outbox','audit.events'])
def test_card_change_receipt_audit_and_outbox_roll_back_together(database: 'Connection[SqlRow]',table: str) -> None:
    blocked(database)
    command=propose(database,owned_case(database),'card.unblock')['command']
    database.execute('reset role')
    database.execute('create function pg_temp.refuse() returns trigger language plpgsql as $$ begin raise exception \'refused\' using errcode=\'P0001\'; end $$')
    database.execute(f'create trigger refuse_write before insert on {table} for each row execute function pg_temp.refuse()')
    set_customer(database,1001)
    assert sqlstate(database,'select public.action_confirm(%s)',(command['command_id'],))=='P0001'
    database.execute('reset role')
    database.execute(f'drop trigger refuse_write on {table}')
    assert database.execute('select state from actions.commands where command_id=%s',(command['command_id'],)).fetchone()==('proposed',)
    assert database.execute('select count(*) from actions.receipts where command_id=%s',(command['command_id'],)).fetchone()==(0,)
    assert database.execute('select count(*) from events.outbox where command_id=%s',(command['command_id'],)).fetchone()==(0,)
    assert database.execute('select count(*) from support.handoff_notes').fetchone()==(0,)
    assert database.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked',)


def test_staff_unblock_still_runs_through_the_shared_type(database: 'Connection[SqlRow]') -> None:
    block_case=blocked(database)
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
