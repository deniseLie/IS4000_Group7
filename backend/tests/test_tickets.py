"""Ticket Service, request categories and the ticket.create action over real temporary PostgreSQL (migration 0071).

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_tickets.py -q
SQL claims are set directly (tests/database_support.py), as in the other native action suites.
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
LIMIT={'category':'card_limit_change','summary':'Higher limit for a trip to Japan.','urgency':'normal',
    'details':{'card':'7310','new_limit':'30000','duration':'temporary','until':'2099-12-31','reason':'Travel'}}


def owned_case(db: 'Connection[SqlRow]',customer: int=1002) -> UUID:
    set_customer(db,customer)
    case_id=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    return case_id


def propose(db: 'Connection[SqlRow]',case_id: UUID,target: dict,task: str='ticket') -> dict:
    return db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'ticket.create',json.dumps(target),f'{case_id}:{task}:ticket.create')).fetchone()[0]


def sqlstate(db: 'Connection[SqlRow]',target: dict,customer: int=1002) -> str | None:
    import psycopg
    case_id=owned_case(db,customer)
    try:
        with db.transaction():propose(db,case_id,target)
    except psycopg.Error as error:return error.sqlstate
    return None


def tickets(db: 'Connection[SqlRow]',customer: int=1002) -> list[dict]:
    from app.services.customer_data import TICKETS
    set_customer(db,customer)
    envelope=db.execute('select public.customer_read_tickets(%s)',(customer,)).fetchone()[0]
    assert TICKETS.validate_python(envelope).customer_id==customer   # same adapter the API uses
    return envelope['data']


def test_categories_are_public_configuration(database: 'Connection[SqlRow]') -> None:
    database.execute("select set_config('request.jwt.claims','{\"role\":\"anon\"}',true)")
    database.execute('set local role anon')
    from app.services.customer_data import CATEGORIES
    rows=database.execute('select public.read_request_categories()').fetchone()[0]
    CATEGORIES.validate_python(rows)
    categories={row['category']:row for row in rows}
    assert {'card_limit_change','statement_request','suspected_fraud','rm_request'}<=set(categories)
    limit=categories['card_limit_change']
    assert limit['customer_raisable'] and 'cannot approve' in limit['decided_by'] and [f['key'] for f in limit['fields']][:2]==['card','new_limit']
    assert not categories['suspected_fraud']['customer_raisable']


def test_card_limit_ticket_needs_confirmation_then_gets_a_reference_and_is_never_decided_by_the_bot(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database)
    proposed=propose(database,case_id,LIMIT)
    command=proposed['command']
    assert command['state']=='proposed' and proposed['receipt'] is None
    assert command['target']['owner_team']=='Cards and credit team' and command['target']['category_label']=='Card limit change'
    before=len(tickets(database))
    outcome=database.execute('select public.action_confirm(%s)',(command['command_id'],)).fetchone()[0]
    result=outcome['receipt']['result']
    from app.schemas.actions import ActionOutcome
    from app.schemas.tickets import StoredTicketTarget,TicketResult
    parsed=ActionOutcome.model_validate(outcome)   # the API's own validation accepts the SQL receipt
    assert isinstance(parsed.command.target,StoredTicketTarget) and isinstance(parsed.receipt.result,TicketResult)
    assert outcome['command']['state']=='succeeded' and result['created'] and result['status']=='submitted' and result['reference'].startswith('TCK-')
    rows=tickets(database)
    assert len(rows)==before+1
    ticket=next(row for row in rows if row['reference']==result['reference'])
    assert ticket['status']=='submitted' and ticket['details']['new_limit']=='30000' and ticket['last_update']['note'].startswith('Received.')
    database.execute('reset role')
    assert database.execute('select event_type from events.outbox where command_id=%s',(command['command_id'],)).fetchall()==[('ticket.recorded',)]
    delivered=database.execute('select notify.deliver(o) from events.outbox o where command_id=%s',(command['command_id'],)).fetchone()[0]
    assert database.execute('select title from notify.inbox where notification_id=%s',(delivered['notification_id'],)).fetchone()==(f"Request {result['reference']} recorded",)
    assert database.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    # Asking again while it is open shows the same ticket instead of a duplicate.
    again=propose(database,owned_case(database),{**LIMIT,'summary':'Please raise it again.'})
    reused=database.execute('select public.action_confirm(%s)',(again['command']['command_id'],)).fetchone()[0]['receipt']['result']
    assert reused['created'] is False and reused['reference']==result['reference']


@pytest.mark.parametrize('change,code',[
    ({'details':{**LIMIT['details'],'card':'4821'}},'42501'),            # Alex's card, not Michelle's
    ({'details':{k:v for k,v in LIMIT['details'].items() if k!='reason'}},'22023'),
    ({'details':{**LIMIT['details'],'approved':'yes'}},'22023'),        # unknown field
    ({'details':{**LIMIT['details'],'new_limit':'lots'}},'22023'),
    ({'details':{**LIMIT['details'],'duration':'forever'}},'22023'),
    ({'details':{**LIMIT['details'],'until':'2000-01-01'}},'22023'),
    ({'summary':'My card 4111 1111 1111 1111 has a problem'},'22023'),  # not redacted
    ({'category':'suspected_fraud','details':{}},'22023'),              # raised by the fraud workflow only
    ({'urgency':'urgent'},'22023')])
def test_invalid_tickets_are_refused_before_a_proposal(database: 'Connection[SqlRow]',change: dict,code: str) -> None:
    assert sqlstate(database,{**LIMIT,**change})==code


def test_customers_only_see_their_own_tickets_and_no_internal_notes(database: 'Connection[SqlRow]') -> None:
    michelle=tickets(database)
    assert {row['category'] for row in michelle}>={'statement_request','personal_details_update'}
    waiting=next(row for row in michelle if row['category']=='personal_details_update')
    assert waiting['status']=='waiting_on_client' and 'proof of your new address' in waiting['last_update']['note']
    database.execute('reset role')
    ticket_id=waiting['ticket_id']
    database.execute("insert into support.ticket_events(ticket_id,customer_id,status,public_note,internal) values(%s,1002,'waiting_on_client','Staff only: risk note',true)",(ticket_id,))
    again=next(row for row in tickets(database) if row['ticket_id']==ticket_id)
    assert again['last_update']['note']!='Staff only: risk note'
    assert all(row['reference'] not in {r['reference'] for r in michelle} for row in tickets(database,1001))
    set_customer(database,1001)
    assert database.execute('select count(*) from support.tickets where customer_id=1002').fetchone()==(0,)


def test_fraud_handoffs_and_rm_requests_become_tickets_that_follow_their_status(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database,1001)
    database.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'handoff.create',
        json.dumps({'reason':'card_verification','priority':'P0','summary':'Card ending 4821: verify the blocked card.'}),f'{case_id}:t1_handoff:handoff.create'))
    fraud=next(row for row in tickets(database,1001) if row['source']=='handoff')
    assert fraud['category']=='suspected_fraud' and fraud['urgency']=='urgent' and fraud['owner_team']=='Fraud operations'
    assert 'verify the blocked card' not in fraud['summary']   # staff summary stays internal
    database.execute('reset role')
    database.execute("update support.handoffs set status='claimed' where case_id=%s",(case_id,))
    assert next(row for row in tickets(database,1001) if row['ticket_id']==fraud['ticket_id'])['status']=='in_review'
    database.execute('reset role')
    advisor=database.execute('select advisor_id from rm.assignments where customer_id=1003 and ended_on is null').fetchone()[0]
    request_id=database.execute("insert into rm.requests(customer_id,advisor_id,who,what,urgency) values(1003,%s,'David','Annual portfolio statement','normal') returning request_id",(advisor,)).fetchone()[0]
    database.execute("update rm.requests set status='seen' where request_id=%s",(request_id,))
    rm=next(row for row in tickets(database,1003) if row['category']=='rm_request' and 'Annual portfolio statement' in row['summary'] and row['status']=='in_review')
    assert rm['owner_team']=='Your relationship manager'


def test_owner_can_move_a_ticket_with_a_customer_visible_note(database: 'Connection[SqlRow]') -> None:
    reference=next(row for row in tickets(database) if row['category']=='statement_request')['reference']
    database.execute('reset role')
    database.execute("select support.set_ticket_status(%s,'resolved','Your statement is in your inbox.')",(reference,))
    row=next(row for row in tickets(database) if row['reference']==reference)
    assert row['status']=='resolved' and row['last_update']['note']=='Your statement is in your inbox.'
    set_customer(database,1002)
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with database.transaction():database.execute("select support.set_ticket_status(%s,'closed',null)",(reference,))


def test_audit_walkthrough_traces_a_case_from_proposal_to_ticket(database: 'Connection[SqlRow]') -> None:
    case_id=owned_case(database)
    proposed=propose(database,case_id,LIMIT)
    database.execute('select public.action_confirm(%s)',(proposed['command']['command_id'],))
    timeline=database.execute('select public.my_case_timeline(%s)',(case_id,)).fetchone()[0]
    steps=[row['action'] for row in timeline]
    assert steps.index('action.proposed')<steps.index('action.executed') and 'ticket.submitted' in steps and 'handoff.ticket_created' in steps
    assert all(isinstance(row['details'],dict) for row in timeline)
    set_customer(database,1001)
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with database.transaction():database.execute('select public.my_case_timeline(%s)',(case_id,))
    database.execute('reset role')
    from scripts.audit_walkthrough import render
    rows=database.execute('select * from audit.case_timeline(%s)',(case_id,)).fetchall()
    assert rows and '| # | Time |' in render(rows,True) and 'ticket.submitted' in render(rows,False)
