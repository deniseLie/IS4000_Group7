"""Apply the reviewable meeting deployment batch twice to a real fresh PostgreSQL baseline; never hosted access."""
import os
from pathlib import Path
import psycopg,pytest
from tests.database_support import SOPHIE_LOGIN,persona_id
from scripts.prepare_hosted_migrations import load_migrations,render_bundle

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Native PostgreSQL deployment opt-in required')


def test_meeting_candidate_batch_is_atomic_replayable_and_preserves_runtime_permissions(tmp_path):
    import pgserver
    server=pgserver.get_server(tmp_path/'meeting-deploy',cleanup_mode='delete')
    folder=Path(__file__).parents[1]/'supabase/migrations'
    try:
        with psycopg.connect(server.get_uri(),autocommit=True) as db:
            db.execute(Path(__file__).with_name('local_bootstrap.sql').read_text())
            for customer,name in ((1001,'alex'),(1002,'michelle'),(1003,'david')):
                db.execute('insert into auth.users(id,email) values(%s,%s)',(persona_id(customer),'demo.'+name+'@example.com'))
            db.execute('insert into auth.users(id,email) values(%s,%s)',(SOPHIE_LOGIN,'demo.sophie@example.com'))
            for path in sorted(folder.glob('*.sql')):
                if int(path.name[:4])<=124:db.execute(path.read_text())
            db.execute('create schema supabase_migrations;create table supabase_migrations.schema_migrations(version text primary key,name text,statements text[])')
            earlier=load_migrations(folder,125,133);db.execute(render_bundle(earlier))
            migrations=load_migrations(folder,134,139);batch=render_bundle(migrations);db.execute(batch)
            assert db.execute('select count(*) from supabase_migrations.schema_migrations').fetchone()==(len(earlier)+len(migrations),)
            before=db.execute('select count(*) from audit.events').fetchone();db.execute(batch)
            assert db.execute('select count(*) from audit.events').fetchone()==before
            for table in ('meetings.briefs','meetings.copilot_briefs','meetings.packs','meetings.threads','meetings.minutes_reviews','meetings.source_chunks','meetings.timers','meetings.graph_runs','meetings.graph_checkpoints','meetings.graph_writes','meetings.graph_pending'):
                assert db.execute('select relrowsecurity from pg_class where oid=%s::regclass',(table,)).fetchone()==(True,)
                for role in ('authenticated','demobank_meetings','demobank_meeting_ingress'):
                    assert db.execute("select has_table_privilege(%s,%s,'select,insert,update,delete')",(role,table)).fetchone()==(False,)
            assert db.execute("select has_function_privilege('demobank_meetings','meetings.checkpoint_claim(bigint,uuid)','execute')").fetchone()==(True,)
            assert db.execute("select has_function_privilege('demobank_meeting_ingress','meetings.checkpoint_claim(bigint,uuid)','execute')").fetchone()==(False,)
            for function in ('public.rm_meeting_brief_before_lifecycle(uuid)','public.rm_meeting_brief_approve_before_lifecycle(uuid,uuid,integer,uuid)'):
                for role in ('authenticated','demobank_meetings','demobank_meeting_ingress'):
                    assert not db.execute('select has_function_privilege(%s,%s,%s)',(role,function,'execute')).fetchone()[0]
            db.execute("update supabase_migrations.schema_migrations set statements=array['tampered'] where version='0135'")
            with pytest.raises(psycopg.errors.RaiseException,match='recorded with different SQL'):db.execute(batch)
            db.execute('rollback')
    finally:server.cleanup()
