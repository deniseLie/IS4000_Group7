"""Native temporary PostgreSQL fixtures for opt-in SQL integration suites."""
import json
import os
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
import pytest

if TYPE_CHECKING:
    from psycopg import Connection

SqlRow: TypeAlias = tuple[str | int | float | bool | UUID | date | datetime | Decimal | None, ...]


def persona_id(customer_id: int) -> UUID:
    return uuid5(NAMESPACE_URL,f'demobank-test-persona:{customer_id}')


SOPHIE_LOGIN=uuid5(NAMESPACE_URL,'demobank-test-persona:sophie')  # RM login, linked by rm.staff_logins (0088)


@pytest.fixture(scope='session')
def local_postgres(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if os.getenv('TEST_DATABASE_LOCAL')!='1':pytest.skip('Opt in to native PostgreSQL with TEST_DATABASE_LOCAL=1')
    import pgserver
    import psycopg
    server=pgserver.get_server(tmp_path_factory.mktemp('demobank-postgres')/'data',cleanup_mode='delete')
    try:
        uri=server.get_uri()
        with psycopg.connect(uri,autocommit=True) as db:
            db.execute(Path(__file__).with_name('local_bootstrap.sql').read_text())
            for customer_id,name in [(1001,'alex'),(1002,'michelle'),(1003,'david')]:
                db.execute('insert into auth.users(id,email) values (%s,%s)',(persona_id(customer_id),f'demo.{name}@example.com'))
            db.execute('insert into auth.users(id,email) values (%s,%s)',(SOPHIE_LOGIN,'demo.sophie@example.com'))
            for migration in sorted((Path(__file__).parents[1]/'supabase'/'migrations').glob('*.sql')):
                db.execute(migration.read_text())
        yield uri
    finally:server.cleanup()


@pytest.fixture
def database(local_postgres: str) -> Iterator['Connection[SqlRow]']:
    import psycopg
    with psycopg.connect(local_postgres) as db:
        try:yield db
        finally:db.rollback()


def set_customer(db: 'Connection[SqlRow]', customer_id: int) -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",
        (json.dumps({'sub':str(persona_id(customer_id)),'role':'authenticated','aal':'aal1','session_id':str(uuid4())}),))
    db.execute('set local role authenticated')
