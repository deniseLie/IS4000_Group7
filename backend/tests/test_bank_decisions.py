"""Michelle's demo activity (0075) and the bank's decision on submitted documents (0076); real temporary PostgreSQL."""
import pytest
from tests.database_support import SqlRow, set_customer

pytestmark=pytest.mark.skipif(__import__('os').getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native PostgreSQL with TEST_DATABASE_LOCAL=1')
ITEM='00000000-0000-4000-8000-100200000011'  # Proof of residential address


def test_michelle_has_her_own_suspicious_charges_and_pending_transfer(database) -> None:
    rows=database.execute("select transaction_id,merchant_country,status from banking.transactions where customer_id=1002 and transaction_id between 100214 and 100217 order by 1").fetchall()
    assert rows==[(100214,'SG','posted'),(100215,'GB','posted'),(100216,'GB','posted'),(100217,None,'pending')]
    assert database.execute('select outstanding_balance from banking.credit_cards where card_id=10021').fetchone()[0]==__import__('decimal').Decimal('5169.60')


def submit(database) -> None:
    database.execute("update compliance.requested_items set submission_status='submitted_awaiting_review' where item_id=%s",(ITEM,))


def test_bank_can_accept_a_submitted_document(database) -> None:
    submit(database)
    assert database.execute('select compliance.record_bank_decision(%s,%s)',(ITEM,'accepted')).fetchone()[0]=='Proof of residential address accepted'
    assert database.execute('select submission_status from compliance.requested_items where item_id=%s',(ITEM,)).fetchone()==('accepted',)
    assert database.execute("select count(*) from audit.events where action='document.bank_decided' and customer_id=1002").fetchone()==(1,)


def test_correction_opens_a_request_that_closes_when_she_resubmits(database) -> None:
    submit(database)
    database.execute('select compliance.record_bank_decision(%s,%s,%s)',(ITEM,'rejected','The bill is older than 3 months.'))
    status,=database.execute('select submission_status from compliance.requested_items where item_id=%s',(ITEM,)).fetchone()
    question,state=database.execute('select question,status from compliance.info_requests where item_id=%s',(ITEM,)).fetchone()
    assert status=='rejected' and state=='open' and question=='Please send a new Proof of residential address. The bill is older than 3 months.'
    submit(database)
    assert database.execute('select status from compliance.info_requests where item_id=%s',(ITEM,)).fetchone()==('responded',)


@pytest.mark.parametrize('args,message',[(('maybe',None),'accepted or rejected'),(('rejected',' '),'what the client needs'),(('accepted',None),'only a submitted')])
def test_bad_decisions_are_refused(database,args,message) -> None:
    import psycopg
    database.execute("update compliance.requested_items set submission_status='not_submitted' where item_id=%s",(ITEM,))
    with pytest.raises(psycopg.Error,match=message):database.execute('select compliance.record_bank_decision(%s,%s,%s)',(ITEM,*args))


def test_clients_cannot_decide_their_own_documents(database) -> None:
    import psycopg
    submit(database);set_customer(database,1002)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):database.execute('select compliance.record_bank_decision(%s,%s)',(ITEM,'accepted'))


def test_a_correction_due_this_week_gets_a_reminder(database) -> None:
    submit(database)
    database.execute("select compliance.record_bank_decision(%s,'rejected','Please send a clearer scan.',((now() at time zone 'Asia/Singapore')::date+3))",(ITEM,))
    database.execute('select events.enqueue_due_reminders()')
    assert database.execute("""select count(*) from events.outbox o join compliance.info_requests r on r.request_id::text=o.payload->>'request_id'
      where o.event_type='compliance.information_reminder' and r.item_id=%s""",(ITEM,)).fetchone()==(1,)
