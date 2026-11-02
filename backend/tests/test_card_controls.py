"""Card controls for the client (migration 0096, CR19) over real temporary PostgreSQL.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_card_controls.py -q
Freeze/unfreeze through the action ledger; replacement, limit and dispute requests through ticket.create.
"""
import json
import os
from datetime import date,timedelta
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from tests.database_support import SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SIGNATURE={'card_id':'card_10021'}   # Michelle's Signature Card ending 7310, limit 20,000
TODAY=date.today()


def case(db: 'Connection[SqlRow]',customer: int=1002) -> UUID:
    set_customer(db,customer)
    case_id=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    return case_id


def propose(db: 'Connection[SqlRow]',kind: str,target: dict,customer: int=1002) -> dict:
    case_id=case(db,customer)
    return db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(target),f'{case_id}:card:{kind}')).fetchone()[0]


def confirm(db: 'Connection[SqlRow]',proposed: dict) -> dict:
    return db.execute('select public.action_confirm(%s)',(proposed['command']['command_id'],)).fetchone()[0]


def refused(db: 'Connection[SqlRow]',kind: str,target: dict,customer: int=1002) -> tuple[str,str] | None:
    import psycopg
    try:
        with db.transaction():propose(db,kind,target,customer)
    except psycopg.Error as error:return error.sqlstate,error.diag.message_primary or ''
    return None


def status(db: 'Connection[SqlRow]',card: int=10021) -> str:
    db.execute('reset role')
    return db.execute('select status from banking.credit_cards where card_id=%s',(card,)).fetchone()[0]


def ticket(category: str,details: dict,summary: str='Card request.') -> dict:
    return {'category':category,'summary':summary,'urgency':'normal','details':details}


def limit(**change: str) -> dict:
    return ticket('card_limit_change',{'card':'7310','new_limit':'25000','duration':'permanent','reason':'Change in income','declaration':'yes',**change})


def test_the_client_freezes_and_unfreezes_her_own_card(database: 'Connection[SqlRow]') -> None:
    frozen=confirm(database,propose(database,'card.temporary_block',SIGNATURE))
    assert frozen['command']['state']=='succeeded' and frozen['command']['target']=={'card_id':'card_10021','last4':'7310'}
    assert status(database)=='temporarily_blocked'
    assert refused(database,'card.temporary_block',SIGNATURE)[0]=='P1002'           # already frozen
    set_customer(database,1002)
    card=next(c for c in database.execute('select public.customer_read_cards(1002)').fetchone()[0]['data'] if c['card_id']=='card_10021')
    assert card['unfreeze_blocked_reason'] is None
    thawed=confirm(database,propose(database,'card.unblock',SIGNATURE))
    assert thawed['command']['state']=='succeeded' and status(database)=='active'
    assert database.execute("select event_type from events.outbox where command_id=%s",(thawed['command']['command_id'],)).fetchone()==('card.unblocked_by_customer',)


def test_a_card_the_bank_froze_or_a_fraud_case_stays_with_the_bank(database: 'Connection[SqlRow]') -> None:
    database.execute("update banking.credit_cards set status='temporarily_blocked' where card_id=10021")
    code,message=refused(database,'card.unblock',SIGNATURE)
    assert code=='P1002' and 'The bank froze this card' in message
    set_customer(database,1002)
    card=next(c for c in database.execute('select public.customer_read_cards(1002)').fetchone()[0]['data'] if c['card_id']=='card_10021')
    assert 'The bank froze this card' in card['unfreeze_blocked_reason']
    # Her own freeze, but an open fraud case: still the bank's.
    database.execute('reset role')
    database.execute("update banking.credit_cards set status='active' where card_id=10021")
    confirm(database,propose(database,'card.temporary_block',SIGNATURE))
    database.execute('reset role')
    database.execute("select support.open_ticket(1002,null,'suspected_fraud','Charges not recognised.','{}','urgent','handoff')")
    code,message=refused(database,'card.unblock',SIGNATURE)
    assert code=='P1002' and 'fraud team' in message


def test_only_the_bank_cancels_and_the_old_dispute_draft_stays_bank_only(database: 'Connection[SqlRow]') -> None:
    assert refused(database,'card.cancel_permanently',SIGNATURE)[0]=='42501'
    assert refused(database,'card.dispute',{'card_id':'card_10021','transaction_ids':['txn_100214']})[0]=='42501'


def test_another_clients_card_cannot_be_frozen(database: 'Connection[SqlRow]') -> None:
    assert refused(database,'card.temporary_block',{'card_id':'card_10011'})[0]=='42501'


@pytest.mark.parametrize('change,words',[
    ({'new_limit':'25500'},'steps of SGD 1,000'),
    ({'new_limit':'60000'},'steps of SGD 1,000'),
    ({'new_limit':'20000'},'already has a limit'),
    ({'new_limit':'15000','duration':'temporary','until':(TODAY+timedelta(days=30)).isoformat()},'can only raise'),
    ({'duration':'temporary'},'Choose the date'),
    ({'duration':'temporary','until':(TODAY+timedelta(days=120)).isoformat()},'up to 90 days'),
    ({'until':(TODAY+timedelta(days=30)).isoformat()},'no end date'),
])
def test_limit_rules_are_explained_to_the_client(database: 'Connection[SqlRow]',change: dict,words: str) -> None:
    code,message=refused(database,'ticket.create',limit(**change))
    assert code=='P1002' and words in message


def test_a_higher_limit_needs_the_declaration_but_a_lower_one_does_not(database: 'Connection[SqlRow]') -> None:
    without={k:v for k,v in limit()['details'].items() if k!='declaration'}
    code,message=refused(database,'ticket.create',ticket('card_limit_change',without))
    assert code=='P1002' and 'income and employment' in message
    assert refused(database,'ticket.create',ticket('card_limit_change',{**without,'new_limit':'15000','reason':'Lower my spending'})) is None
    assert refused(database,'ticket.create',ticket('card_limit_change',{**without,'declaration':'no'}))[0]=='22023'


def test_one_decided_limit_change_per_card_every_30_days(database: 'Connection[SqlRow]') -> None:
    done=confirm(database,propose(database,'ticket.create',limit()))
    reference=done['receipt']['result']['reference']
    database.execute('reset role')
    database.execute("select support.set_ticket_status(%s,'resolved','Approved by the cards and credit team.')",(reference,))
    code,message=refused(database,'ticket.create',limit(new_limit='30000'))
    assert code=='P1002' and 'You can ask again from' in message
    # The other card is not affected.
    assert refused(database,'ticket.create',limit(card='5592',new_limit='12000')) is None


def test_a_lost_card_is_frozen_on_report_and_each_card_has_its_own_replacement(database: 'Connection[SqlRow]') -> None:
    lost=ticket('card_replacement',{'card':'7310','what_happened':'Lost','last_had':TODAY.isoformat(),'declaration':'yes'},'Lost my card at the mall.')
    result=confirm(database,propose(database,'ticket.create',lost))['receipt']['result']
    assert result['created'] and status(database)=='temporarily_blocked'
    row=database.execute('select urgency,details from support.tickets where reference=%s',(result['reference'],)).fetchone()
    assert row[0]=='high' and row[1]['card_frozen']=='Frozen when reported'
    code,message=refused(database,'card.unblock',SIGNATURE)
    assert code=='P1002' and 'replace' in message
    again=confirm(database,propose(database,'ticket.create',lost))['receipt']['result']
    assert again['created'] is False and again['reference']==result['reference']
    damaged=ticket('card_replacement',{'card':'5592','what_happened':'Damaged','declaration':'yes'},'Chip stopped working.')
    other=confirm(database,propose(database,'ticket.create',damaged))['receipt']['result']
    assert other['created'] and other['reference']!=result['reference'] and status(database,10022)=='active'
    assert refused(database,'ticket.create',ticket('card_replacement',{'card':'5592','what_happened':'Damaged','last_had':TODAY.isoformat(),'declaration':'yes'}))[0]=='P1002'


def dispute(transaction: str='txn_100214',card: str='7310',**change: str) -> dict:
    return ticket('card_dispute',{'card':card,'transaction':transaction,'reason':'Charged twice','contacted_merchant':'Yes',
        'what_happened':'The bistro charged me twice for one dinner.','declaration':'yes',**change},'Charged twice at Orchard Bistro.')


def test_a_recognised_charge_can_be_disputed_once_per_charge(database: 'Connection[SqlRow]') -> None:
    first=confirm(database,propose(database,'ticket.create',dispute()))['receipt']['result']
    assert first['created'] and first['category']=='card_dispute'
    database.execute('reset role')
    details=database.execute('select details from support.tickets where reference=%s',(first['reference'],)).fetchone()[0]
    assert 'Orchard Bistro' in details['charge'] and 'SGD 68.20' in details['charge']
    again=confirm(database,propose(database,'ticket.create',dispute(reason='Wrong amount')))['receipt']['result']
    assert again['created'] is False and again['reference']==first['reference']
    second=confirm(database,propose(database,'ticket.create',dispute('txn_100241','5592')))['receipt']['result']
    assert second['created'] and second['reference']!=first['reference']


@pytest.mark.parametrize('target,words',[
    (dispute('txn_100241'),'Choose a charge made on your card'),     # a Travel Card charge under the Signature Card
    (dispute('txn_100236'),'Only a posted charge'),                  # a payment received, not a charge
    (dispute('txn_100224'),'more than 60 days ago'),
])
def test_dispute_rules_are_explained(database: 'Connection[SqlRow]',target: dict,words: str) -> None:
    code,message=refused(database,'ticket.create',target)
    assert code=='P1002' and words in message


def test_the_categories_carry_the_new_field_kinds(database: 'Connection[SqlRow]') -> None:
    from app.services.customer_data import CATEGORIES
    rows=database.execute('select public.read_request_categories()').fetchone()[0]
    CATEGORIES.validate_python(rows)
    kinds={row['category']:{f['key']:f['kind'] for f in row['fields']} for row in rows}
    assert kinds['card_dispute']['transaction']=='transaction' and kinds['card_replacement']['declaration']=='confirm'
    assert kinds['card_limit_change']['reason']=='choice'
    # The client parses labels of at most 120 characters and decided_by of at most 120 (frontend/src/lib/ticketData.js).
    assert all(len(f['label'])<=120 for row in rows for f in row['fields']) and all(len(row['decided_by'])<=120 for row in rows)


def test_an_unfreeze_is_checked_again_when_she_confirms(database: 'Connection[SqlRow]') -> None:
    confirm(database,propose(database,'card.temporary_block',SIGNATURE))
    proposed=propose(database,'card.unblock',SIGNATURE)
    database.execute('reset role')
    database.execute("select support.open_ticket(1002,null,'suspected_fraud','Charges not recognised.','{}','urgent','handoff')")
    set_customer(database,1002)
    outcome=confirm(database,proposed)
    assert outcome['command']['state']=='failed' and outcome['receipt']['result']=={'error_code':'target_changed'}
    assert status(database)=='temporarily_blocked'
