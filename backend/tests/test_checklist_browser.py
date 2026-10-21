"""Normal frontend and real local database/API; no request interception or model simulation."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser checks')


def test_checklist_mark_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),user_id=persona_id(1002))
    run_browser(native_api_client,case,'checklist preparation saves',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from actions.receipts').fetchone()==(8,)
        assert db.execute("select client_mark,submission_status from compliance.requested_items where item_id='00000000-0000-4000-8000-100200000011'").fetchone()==('not_prepared','not_submitted')
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_closed_checklist_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("update compliance.cases set status='completed'")
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),user_id=persona_id(1002))
    run_browser(native_api_client,case,'closed checklist review blocks',tmp_path)
