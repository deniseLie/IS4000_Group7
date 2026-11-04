"""Current Michelle accounts and relationship agent journey, over native services and actual Codex."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1' or os.getenv('TEST_CLIENT_LIVE')!='1',reason='Opt in to the current client journey with real Codex')


def test_current_client_accounts_and_relationship(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    import psycopg
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),user_id=persona_id(1002))
    run_browser(native_api_client,case,'current client receives owned account and relationship evidence',tmp_path,timeout=600)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from conversation.turns where state='completed' and customer_id=1002").fetchone()==(3,)
        assert db.execute('select count(*) from actions.commands where customer_id=1002').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
