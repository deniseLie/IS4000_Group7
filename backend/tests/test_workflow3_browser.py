"""Workflow 3 (David) in the normal browser app against native PostgreSQL/PostgREST, JWT and RLS; no model is called."""
import os
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native Workflow 3 browser integration')


class Workflow3Fixture(NativeBrowserSession):
    deadline: date


def test_workflow3_in_chat_relationship_steps(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=Workflow3Fixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003),
        deadline=datetime.now(timezone(timedelta(hours=8))).date()+timedelta(days=14))
    run_browser(native_api_client,case,'workflow 3 introduces the new RM',tmp_path,timeout=240)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select what,status from rm.requests where customer_id=1003 and advisor_id=(select advisor_id from rm.assignments where customer_id=1003 and ended_on is null)").fetchall()==[
            ('can someone send me my annual portfolio statement','sent')]
        assert db.execute('select count(*) from rm.shared_summaries where customer_id=1003').fetchone()==(1,)
        assert db.execute("select status,format from rm.appointments where customer_id=1003").fetchall()==[('confirmed','video')]
        assert db.execute("select count(*) from actions.commands where state<>'succeeded'").fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
