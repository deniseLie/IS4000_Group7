"""Persona 2 seed (F4, migration 0088): Sophie's login, book, David's inherited history and tickets, over real temporary PostgreSQL.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_rm_seed.py -q
SQL claims are set directly (tests/database_support.py). RM access rules are F12; until then Sophie reads no client rows.
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from tests.database_support import SOPHIE_LOGIN,SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SOPHIE=UUID('00000000-0000-4000-8000-100200000002')
PRIYA=UUID('00000000-0000-4000-8000-100100000002')
RACHEL=UUID('00000000-0000-4000-8000-100300000001')
OPEN=('submitted','in_review','waiting_on_client')


def set_sophie(db: 'Connection[SqlRow]') -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'app_metadata':{'demobank_role':'rm','rm_id':str(SOPHIE)}}),))
    db.execute('set local role authenticated')


def test_sophie_has_a_linked_login_and_a_book_of_four_clients(database: 'Connection[SqlRow]') -> None:
    assert database.execute('select advisor_id,demo_email,auth_user_id from rm.staff_logins').fetchall()==[(SOPHIE,'demo.sophie@example.com',SOPHIE_LOGIN)]
    assert database.execute("select full_name,role,team_name,email from rm.advisors where advisor_id=%s",(SOPHIE,)).fetchone()==(
        'Sophie Lim (DEMO)','Senior Relationship Manager','Private Clients Team A','sophie.lim@demobank.example')
    book=database.execute('select customer_id from rm.assignments where advisor_id=%s and ended_on is null order by 1',(SOPHIE,)).fetchall()
    assert [row[0] for row in book]==[1002,1003,1004,1005]
    other=database.execute('select customer_id from rm.assignments where advisor_id=%s and ended_on is null order by 1',(PRIYA,)).fetchall()
    assert [row[0] for row in other]==[1001,1006]
    # Michelle keeps one long-standing assignment (no change notice); her joining details name Sophie.
    assert database.execute('select count(*),bool_and(ended_on is null) from rm.assignments where customer_id=1002').fetchone()==(1,True)
    details=[row[0] for row in database.execute('select joining_details from rm.meeting_options where advisor_id=%s',(SOPHIE,))]
    assert len(details)==3 and all('Daniel' not in text and 'DEMO-DT' not in text for text in details)


def test_david_is_inherited_from_rachel_with_notes_a_request_and_a_consented_handover(database: 'Connection[SqlRow]') -> None:
    assert database.execute('select advisor_id,ended_on is not null from rm.assignments where customer_id=1003 order by started_on').fetchall()==[(RACHEL,True),(SOPHIE,False)]
    notes=database.execute('select advisor_id,format,cardinality(commitments) from rm.meeting_notes where customer_id=1003 order by met_on').fetchall()
    assert len(notes)==3 and {row[0] for row in notes}=={RACHEL} and all(row[2]>=1 for row in notes)
    assert database.execute("select count(*) from rm.requests where customer_id=1003 and status<>'closed'").fetchone()==(1,)
    shared=database.execute("""select s.advisor_id,g.purpose,g.recipient,g.revoked_at is null and g.expires_at>now() from rm.shared_summaries s
        join consent.grants g on g.grant_id=s.consent_grant_id where s.customer_id=1003""").fetchall()
    assert shared==[(SOPHIE,'rm_brief',str(SOPHIE),True)]


def test_open_tickets_across_the_book_include_urgent_fraud_and_one_past_its_target(database: 'Connection[SqlRow]') -> None:
    rows=database.execute("""select category,urgency,target_at<now() from support.tickets
        where status=any(%s) and customer_id in (select customer_id from rm.assignments where advisor_id=%s and ended_on is null)""",(list(OPEN),SOPHIE)).fetchall()
    assert 8<=len(rows)<=10
    assert len({row[0] for row in rows})>=6 and {row[1] for row in rows}>={'urgent','high','normal','low'}
    assert ('suspected_fraud','urgent',False) in rows and sum(row[2] for row in rows)==1
    events=database.execute("select count(*) from support.ticket_events e join support.tickets t using(ticket_id) where t.source='seed' and e.status='submitted'").fetchone()[0]
    assert events==database.execute("select count(*) from support.tickets where source='seed'").fetchone()[0]


def test_seed_holds_synthetic_masked_data_only(database: 'Connection[SqlRow]') -> None:
    names=[row[0] for row in database.execute('select full_name from banking.customer_profiles where customer_id in (1004,1005,1006)')]
    assert len(names)==3 and all(name.endswith('(DEMO)') for name in names)
    assert all(row[0].startswith('****') for row in database.execute('select masked_account_number from banking.bank_accounts where customer_id in (1004,1005,1006)'))
    assert database.execute("select count(*) from banking.credit_cards where customer_id in (1004,1005) and last4 ~ '^[0-9]{4}$'").fetchone()==(2,)


@pytest.mark.parametrize('customer,own_tickets',[(1002,2),(1003,3)])
def test_clients_see_only_their_own_rows_and_never_meeting_notes(database: 'Connection[SqlRow]',customer: int,own_tickets: int) -> None:
    set_customer(database,customer)
    assert database.execute('select distinct customer_id from support.tickets').fetchall()==[(customer,)]
    assert database.execute('select count(*) from support.tickets').fetchone()==(own_tickets,)
    assert database.execute('select count(*) from banking.customer_profiles where customer_id<>%s',(customer,)).fetchone()==(0,)
    advisors=[row[0] for row in database.execute('select full_name from rm.advisors order by 1')]
    assert 'Sophie Lim (DEMO)' in advisors and 'Priya Nair (DEMO)' not in advisors
    import psycopg
    for table in ('rm.meeting_notes','rm.staff_logins'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with database.transaction():database.execute(f'select count(*) from {table}')


def test_sophie_reads_no_client_rows_until_rm_access_rules_exist(database: 'Connection[SqlRow]') -> None:
    set_sophie(database)
    for table in ('banking.customer_profiles','banking.bank_accounts','support.tickets','rm.assignments','rm.shared_summaries','consent.grants'):
        assert database.execute(f'select count(*) from {table}').fetchone()==(0,),table
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with database.transaction():database.execute('select count(*) from rm.meeting_notes')
