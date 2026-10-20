"""Normal request intake UI using real native JWT, action SQL, receipts and customer reads."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser checks')


def test_confirmed_request_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'request intake reviews confirms',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(4,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_rejected_request_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'request rejection stores',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(0,)


def test_unassigned_request_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),user_id=persona_id(1001))
    run_browser(native_api_client,case,'unassigned customers cannot prepare',tmp_path)
