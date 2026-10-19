"""Consent scope, grant lifetime and RLS boundaries on native PostgreSQL."""
import os
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from tests.database_support import SqlRow,set_customer

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')


def grant(db: 'Connection[SqlRow]', customer_id: int,recipient: UUID,categories: list[str]) -> UUID:
    row=db.execute("""insert into consent.grants(customer_id,purpose,recipient,categories)
        values (%s,'rm_brief',%s,%s) returning grant_id""",(customer_id,str(recipient),categories)).fetchone()
    assert row is not None and isinstance(row[0],UUID)
    return row[0]


def check(db: 'Connection[SqlRow]',customer_id: int,purpose: str,recipient: UUID,categories: list[str]) -> SqlRow:
    row=db.execute('select * from public.consent_check(%s,%s,%s,%s)',(customer_id,purpose,recipient,categories)).fetchone()
    assert row is not None
    return row


def test_exact_purpose_recipient_and_category_subset(database: 'Connection[SqlRow]') -> None:
    recipient=uuid4()
    grant_id=grant(database,1001,recipient,['profile','accounts'])
    set_customer(database,1001)
    result=check(database,1001,'rm_brief',recipient,['profile'])
    assert result[0] is True and result[1]==grant_id and result[2] is None and result[3] is not None
    for purpose,target,categories in [('marketing',recipient,['profile']),('rm_brief',uuid4(),['profile']),
        ('rm_brief',recipient,['transactions'])]:
        assert check(database,1001,purpose,target,categories)[:3]==(False,None,None)


def test_separate_partial_grants_are_not_combined(database: 'Connection[SqlRow]') -> None:
    recipient=uuid4()
    grant(database,1001,recipient,['profile'])
    grant(database,1001,recipient,['accounts'])
    set_customer(database,1001)
    assert check(database,1001,'rm_brief',recipient,['profile'])[0] is True
    assert check(database,1001,'rm_brief',recipient,['profile','accounts'])[:3]==(False,None,None)


@pytest.mark.parametrize('change',["expires_at=clock_timestamp()-interval '1 second'",
    'expires_at=clock_timestamp()',"revoked_at=clock_timestamp()", "granted_at=clock_timestamp()+interval '1 day'"])
def test_inactive_grants_are_not_accepted(database: 'Connection[SqlRow]',change: str) -> None:
    recipient=uuid4()
    grant_id=grant(database,1001,recipient,['profile'])
    database.execute(f'update consent.grants set {change} where grant_id=%s',(grant_id,))
    set_customer(database,1001)
    assert check(database,1001,'rm_brief',recipient,['profile'])[:3]==(False,None,None)


def test_expiry_uses_wall_clock_during_an_open_transaction(database: 'Connection[SqlRow]') -> None:
    recipient=uuid4()
    grant_id=grant(database,1001,recipient,['profile'])
    database.execute("update consent.grants set expires_at=clock_timestamp()+interval '20 milliseconds' where grant_id=%s",(grant_id,))
    database.execute('select pg_sleep(0.05)')
    set_customer(database,1001)
    assert check(database,1001,'rm_brief',recipient,['profile'])[0] is False


def test_customer_scope_cannot_be_overridden(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    recipient=uuid4()
    grant(database,1003,recipient,['profile'])
    set_customer(database,1001)
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():check(database,1003,'rm_brief',recipient,['profile'])
    assert check(database,1001,'rm_brief',recipient,['profile'])[0] is False


def test_guest_and_unlinked_customer_are_denied(database: 'Connection[SqlRow]') -> None:
    from psycopg.errors import InsufficientPrivilege
    recipient=uuid4()
    database.execute('set local role anon')
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():check(database,1001,'rm_brief',recipient,['profile'])
    database.execute('reset role')
    set_customer(database,9999)
    with pytest.raises(InsufficientPrivilege):
        with database.transaction():check(database,9999,'rm_brief',recipient,['profile'])


@pytest.mark.parametrize('purpose,categories',[(None,['profile']),('',['profile']),('private free text',['profile']),
    ('rm_brief',[]),('rm_brief',None),('rm_brief',['profile',None]),('rm_brief',['profile','profile']),
    ('rm_brief',['private free text']),('rm_brief',['x'+str(i) for i in range(21)])])
def test_invalid_scope_fails_instead_of_returning_a_denial(database: 'Connection[SqlRow]',purpose: str | None,categories: list[str | None] | None) -> None:
    from psycopg.errors import InvalidParameterValue
    set_customer(database,1001)
    with pytest.raises(InvalidParameterValue):
        with database.transaction():
            database.execute('select * from public.consent_check(%s,%s,%s,%s)',(1001,purpose,uuid4(),categories))
