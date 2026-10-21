"""Actual-provider compliance answer through the normal UI with native JWT/RLS case records."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1' or os.getenv('TEST_COMPLIANCE_LIVE')!='1',
    reason='Opt in to native browser integration and actual synthetic compliance model calls')


def test_real_personal_compliance_saved_chat(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-compliance-browser-key-with-at-least-32-characters')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),user_id=persona_id(1002))
    run_browser(native_api_client,case,'real personal compliance answer persists',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute("select count(*) from conversation.turns where state='completed'").fetchone()==(1,)
        assert db.execute("select status from compliance.cases where customer_id=1002").fetchone()==('awaiting_client_documents',)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
