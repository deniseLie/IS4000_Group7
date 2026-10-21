"""Real temporary PostgreSQL checks; no hosted access, Auth server or Data API.

Install from the repo root: .venv/bin/python -m pip install -e './backend[dev,dbtest]'
Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_database_local.py -q
All repository migrations run unchanged after test-only platform prerequisites. SQL claims
are set directly to test RLS; gateway JWT tests and hosted persona checks cover other boundaries.
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from tests.database_support import SqlRow, set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')


@pytest.mark.parametrize('customer_id',[1001,1002,1003])
def test_native_rls_isolates_personas_without_code_filters(database: 'Connection[SqlRow]',customer_id: int) -> None:
    set_customer(database,customer_id)
    assert database.execute('select customer_id from banking.customer_profiles').fetchall()==[(customer_id,)]
    assert database.execute('select customer_id from banking.bank_accounts').fetchall()==[(customer_id,)]
    for table in ['banking.credit_cards','banking.transactions','banking.loans','compliance.cases','rm.assignments','notify.preferences']:
        rows=database.execute(f'select customer_id from {table}').fetchall()
        assert all(row[0]==customer_id for row in rows)
    assert database.execute('select * from consent.grants').fetchall()==[]


def test_unlinked_user_sees_no_customer_rows(database: 'Connection[SqlRow]') -> None:
    set_customer(database,9999)
    assert database.execute('select * from banking.bank_accounts').fetchall()==[]
    assert database.execute('select * from compliance.cases').fetchall()==[]
    assert database.execute('select * from rm.advisors').fetchall()==[]


def test_customer_cannot_write_or_read_internal_data(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    set_customer(database,1001)
    for query in ["update banking.credit_cards set status='temporarily_blocked' where card_id=10011",
        'select * from compliance.case_internal','select * from events.outbox','select * from notify.templates',
        "insert into consent.grants(customer_id,purpose,recipient) values (1001,'rm_brief','advisor')"]:
        with pytest.raises(InsufficientPrivilege):
            with database.transaction():database.execute(query)


def test_guest_has_no_personal_table_access(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    database.execute("select set_config('request.jwt.claims','{\"role\":\"anon\"}',true)")
    database.execute('set local role anon')
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():database.execute('select * from banking.bank_accounts')


def test_customer_session_event_is_idempotent_and_chain_is_valid(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1001)
    assert database.execute('select public.audit_session_started(%s)',(uuid4(),)).fetchone()==(True,)
    assert database.execute('select public.audit_session_started(%s)',(uuid4(),)).fetchone()==(False,)
    checked,bad,head,_=database.execute('select * from public.audit_verify_chain(null)').fetchone()
    assert checked==1 and bad is None and len(head)==64


def test_audit_batch_rolls_back_every_event_on_invalid_details(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import RaiseException
    set_customer(database,1001)
    events=[{'category':'chat','action':'chat.message_received','details':{'count':1}},
        {'category':'chat','action':'chat.reply_sent','details':{'text':'private free text'}}]
    with pytest.raises(RaiseException):
        with database.transaction():database.execute('select public.audit_record_batch(%s::jsonb)',(json.dumps(events),))
    assert database.execute('select checked from public.audit_verify_chain(null)').fetchone()==(0,)


def test_uuid_audit_details_preserve_ids_and_reject_other_long_digit_tokens(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import RaiseException
    from app.services.audit import is_safe
    identifier='12345678-1234-4234-8234-123456789012'
    valid={'object_id':identifier}
    assert is_safe(valid)
    set_customer(database,1001)
    event={'category':'data','action':'data.read','details':valid}
    database.execute('select public.audit_record_batch(%s::jsonb)',(json.dumps([event]),))
    assert database.execute('select details from audit.events').fetchone()==(valid,)
    for value in ['4111111111111111','token_123456789012','12345678-1234-4234-8234-1234567890123',
        '12345678-1234-4234-8234-123456789012z','private free text']:
        details={'object_id':value}
        assert not is_safe(details)
        with pytest.raises(RaiseException,match='audit details'):
            with database.transaction():
                database.execute('select public.audit_record_batch(%s::jsonb)',(json.dumps([{**event,'details':details}]),))
    assert database.execute('select checked,first_bad_event_id from public.audit_verify_chain(null)').fetchone()==(1,None)


def test_audit_actor_and_customer_are_derived_from_claims(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1002)
    events=[{'category':'policy','action':'policy.decided','outcome':'denied',
        'actor_type':'agent','actor_id':'document','details':{'tool':'customer.get_accounts','reasons':['agent_tool_forbidden'],'version':'v1'}}]
    database.execute('select public.audit_record_batch(%s::jsonb)',(json.dumps(events),))
    database.execute('reset role')
    assert database.execute('select customer_id,actor_type,actor_id from audit.events').fetchall()==[(1002,'agent','document')]


def test_native_row_change_and_audit_commit_together(database: 'Connection[SqlRow]') -> None:
    database.execute("update banking.credit_cards set status='temporarily_blocked' where card_id=10011")
    assert database.execute("select customer_id,category,resource_type from audit.events").fetchall()==[(1001,'row','banking.credit_cards')]
    checked,bad,_,_=database.execute('select * from audit.verify_chain(null)').fetchone()
    assert checked==1 and bad is None
    database.rollback()
    assert database.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
    assert database.execute('select count(*) from audit.events').fetchone()==(0,)


def test_audit_events_are_immutable_even_for_table_owner(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import FeatureNotSupported,RaiseException
    database.execute("select audit.record('chat','chat.message_received')")
    for query in ['update audit.events set outcome=\'failed\'','delete from audit.events','truncate audit.events']:
        with pytest.raises((RaiseException,FeatureNotSupported)):
            with database.transaction():database.execute(query)


def test_all_customer_tables_have_native_rls(database: 'Connection[SqlRow]') -> None:
    rows=database.execute("""select n.nspname,c.relname,c.relrowsecurity from pg_class c
        join pg_namespace n on n.oid=c.relnamespace
        where c.relkind='r' and n.nspname in
        ('banking','compliance','rm','consent','conversation','actions','support','notify','events','audit','ops')""").fetchall()
    assert rows and all(enabled for _,_,enabled in rows)
