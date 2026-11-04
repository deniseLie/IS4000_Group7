"""Customer-confirmed freeze and unfreeze through the normal React UI over native PostgREST/PostgreSQL.

Run from backend/: TEST_BROWSER_LOCAL=1 ../.venv/bin/python -m pytest tests/test_card_changes_browser.py -q
Needs the native Data API (see test_data_api_local.py) and frontend npm dependencies. No model is involved.
"""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser checks')


def test_card_self_service(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),user_id=persona_id(1001))
    run_browser(native_api_client,case,'card self-service',tmp_path,timeout=300)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
        assert db.execute("select type,state from actions.commands where customer_id=1001 order by proposed_at").fetchall()==[
            ('card.temporary_block','succeeded'),('card.unblock','succeeded'),('card.temporary_block','rejected')]
        assert db.execute("select count(*) from actions.receipts where customer_id=1001").fetchone()==(2,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
