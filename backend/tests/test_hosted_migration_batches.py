"""Apply the actual deployment batches to a real legacy-shaped database; never use hosted credentials."""
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4
import pytest
from scripts.prepare_hosted_migrations import Migration,load_migrations,render_bundle

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native migration deployment checks')
MIGRATIONS=Path(__file__).parents[1]/'supabase'/'migrations'


@pytest.fixture
def legacy_database(local_postgres: str) -> Iterator[str]:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    name='deploy_'+uuid4().hex
    uri=make_conninfo(local_postgres,dbname=name)
    with psycopg.connect(local_postgres,autocommit=True) as admin:
        admin.execute(sql.SQL('create database {} template template0').format(sql.Identifier(name)))
    try:
        with psycopg.connect(uri,autocommit=True) as db:
            bootstrap=Path(__file__).with_name('local_bootstrap.sql').read_text()
            db.execute('create schema auth;'+bootstrap.split('create schema auth;',1)[1])
            for path in sorted(MIGRATIONS.glob('*.sql')):
                if int(path.name[:4])>7:break
                db.execute(path.read_text())
            db.execute("create schema kb; create table kb.documents(source_id text primary key); create table kb.support_contacts(contact_id int primary key); insert into kb.documents values('legacy-preserved');")
            db.execute('create schema supabase_migrations; create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[]);')
            db.execute("insert into supabase_migrations.schema_migrations(version,name) values('20261012185253','trace_tables')")
        yield uri
    finally:
        with psycopg.connect(local_postgres,autocommit=True) as admin:
            admin.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))


def test_all_hosted_batches_preserve_legacy_tables_and_are_idempotent(legacy_database: str) -> None:
    import psycopg
    with psycopg.connect(legacy_database,autocommit=True) as db:
        for first,last in [(8,12),(13,19),(20,27),(28,33),(34,42),(43,44),(45,45),(46,47),(48,49),(50,50)]:
            bundle=render_bundle(load_migrations(MIGRATIONS,first,last))
            db.execute(bundle)
            before=db.execute('select count(*) from audit.events').fetchone()
            db.execute(bundle)
            assert db.execute('select count(*) from audit.events').fetchone()==before
        assert db.execute('select count(*) from supabase_migrations.schema_migrations').fetchone()==(44,)
        assert db.execute('select source_id from kb.documents').fetchone()==('legacy-preserved',)
        assert db.execute("select count(*) from kb.articles where status='published'").fetchone()[0]>0
        assert db.execute("select count(*) from information_schema.columns where table_schema='rm' and table_name='agendas' and column_name='revision'").fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_changed_history_and_failed_sql_roll_back_the_batch(legacy_database: str) -> None:
    import psycopg
    with psycopg.connect(legacy_database,autocommit=True) as db:
        bundle=render_bundle(load_migrations(MIGRATIONS,8,12))
        db.execute(bundle)
        db.execute("update supabase_migrations.schema_migrations set statements=array['changed'] where version='0008'")
        with pytest.raises(psycopg.errors.RaiseException,match='recorded with different SQL'):db.execute(bundle)
        db.execute('rollback')
        failing=render_bundle([Migration(version='0043',name='intentional_failure',sql='create table public.deploy_rollback(id int); select 1/0;')])
        with pytest.raises(psycopg.errors.DivisionByZero):db.execute(failing)
        db.execute('rollback')
        assert db.execute("select to_regclass('public.deploy_rollback')").fetchone()==(None,)
        assert db.execute("select count(*) from supabase_migrations.schema_migrations where version='0043'").fetchone()==(0,)
