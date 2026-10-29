"""Reminders already in the Inbox are cleared once she has done what they asked (0078); real temporary PostgreSQL."""
import json
import pytest
from tests.database_support import SqlRow, set_customer

pytestmark=pytest.mark.skipif(__import__('os').getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native PostgreSQL with TEST_DATABASE_LOCAL=1')
CASE='00000000-0000-4000-8000-100200000001'
ITEMS=[f'00000000-0000-4000-8000-1002000000{n}' for n in (11,12,13,14)]


def deliver(database,event_type,payload,key) -> str:
    """A reminder as the worker would have delivered it: an outbox event and its Inbox row."""
    event,=database.execute("insert into events.outbox(event_type,customer_id,payload,dedup_key) values(%s,1002,%s,%s) returning event_id",
        (event_type,json.dumps(payload),key)).fetchone()
    return str(database.execute("""insert into notify.inbox(customer_id,event_id,template_key,title,body,priority)
      values(1002,%s,'compliance.action_reminder','Action needed','Send your documents by 30 Nov 2026.','P2') returning notification_id""",(event,)).fetchone()[0])


def visible(database) -> set[str]:
    set_customer(database,1002)
    items=database.execute('select public.notification_list_customer(1002)').fetchone()[0]
    database.execute('reset role')
    return {item['notification_id'] for item in items}


def submit(database,item) -> None:
    database.execute("update compliance.requested_items set submission_status='submitted_awaiting_review' where item_id=%s",(item,))


def test_deadline_reminder_stays_until_every_document_is_sent(database) -> None:
    database.execute("update compliance.requested_items set submission_status='not_submitted' where case_id=%s",(CASE,))
    reminder=deliver(database,'compliance.deadline_reminder',{'case_id':CASE,'due_date':'2026-11-30'},'test:deadline')
    for item in ITEMS[:-1]:submit(database,item)
    assert reminder in visible(database)
    submit(database,ITEMS[-1])
    assert reminder not in visible(database)
    cleared,read=database.execute('select cleared_at is not null,read_at is not null from notify.inbox where notification_id=%s',(reminder,)).fetchone()
    assert cleared and read


def test_correction_reminder_clears_when_she_sends_a_new_file(database) -> None:
    submit(database,ITEMS[0])
    database.execute("select compliance.record_bank_decision(%s,'rejected','Please send a clearer scan.')",(ITEMS[0],))
    request,=database.execute('select request_id from compliance.info_requests where item_id=%s',(ITEMS[0],)).fetchone()
    reminder=deliver(database,'compliance.information_reminder',{'case_id':CASE,'request_id':str(request),'due_date':'2026-11-30'},'test:info')
    assert reminder in visible(database)
    submit(database,ITEMS[0])
    assert reminder not in visible(database)


def test_other_notifications_are_never_cleared(database) -> None:
    other,=database.execute("""insert into notify.inbox(customer_id,template_key,title,body) values(1002,'document.submitted','Sent','Your file was sent.')
      returning notification_id""").fetchone()
    for item in ITEMS:submit(database,item)
    assert str(other) in visible(database)


def test_existing_reminders_with_an_old_deadline_are_cleared_and_current_ones_stay(database) -> None:
    """The case on the hosted project: a reminder from before 0073 moved the deadline to 30 Nov (0079)."""
    request,due=database.execute("""insert into compliance.info_requests(case_id,customer_id,question,instructions,due_date)
      values(%s,1002,'Please explain the source of your incoming transfer.','Reply to your RM.','2026-11-30') returning request_id,due_date""",(CASE,)).fetchone()
    old=deliver(database,'compliance.information_reminder',{'case_id':CASE,'request_id':str(request),'due_date':'2026-10-27'},'test:old')
    current=deliver(database,'compliance.information_reminder',{'case_id':CASE,'request_id':str(request),'due_date':str(due)},'test:current')
    database.execute('select notify.clear_answered_reminders(1002,%s)',(CASE,))
    shown=visible(database)
    assert old not in shown and current in shown
    database.execute("update compliance.info_requests set due_date='2026-12-05' where request_id=%s",(request,))
    assert current not in visible(database)  # a changed deadline clears it; a new reminder follows with the right date
