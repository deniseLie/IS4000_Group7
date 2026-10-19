"""Normal agenda UI against real local SQL/HTTP services, without model or hosted login simulation."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native agenda browser integration')


def test_agenda_edit_and_receipt(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'private agenda drafts save and edit',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*),min(revision),max(revision) from rm.agendas').fetchone()==(4,2,2)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(8,)
        assert db.execute('select count(*) from rm.shared_summaries').fetchone()==(0,)
        assert db.execute('select count(*) from consent.grants').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_agenda_read_failure_is_explicit(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.customer_read_agendas(bigint)')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'failed agenda reads block editing',tmp_path)
