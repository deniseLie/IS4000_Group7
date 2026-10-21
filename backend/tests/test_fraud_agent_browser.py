"""Real Fraud model through the normal UI, durable review, scoped writes and receipts."""
import os
import secrets
from pathlib import Path
from typing import Literal
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1' or os.getenv('TEST_FRAUD_LIVE')!='1',
    reason='Requires native browser services and the real configured Fraud model')


class FraudBrowserSession(NativeBrowserSession):
    fraud_live: Literal[True]


def test_real_fraud_proposal_review_and_saved_receipts(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    ctx=context(local_data_api,1001)
    case=FraudBrowserSession(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),
        user_id=ctx.principal.user_id,fraud_live=True)
    run_browser(native_api_client,case,'real fraud proposal uses reviewed confirmation',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked',)
        assert db.execute('select count(*) from support.handoffs').fetchone()==(1,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(2,)
        assert db.execute("select count(*) from conversation.turns where state='completed'").fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
