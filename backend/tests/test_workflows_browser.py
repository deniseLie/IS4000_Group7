"""Normal React UI over real paused graphs and action receipts; no model client is replaced."""
import os
import secrets
from pathlib import Path
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context
from tests.test_workflows import paused

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser checks')


class WorkflowFixture(NativeBrowserSession):
    session_id: UUID
    command_id: UUID


@pytest.mark.parametrize('decision',['confirmation','rejection','cancellation','dialog confirmation'])
def test_durable_review_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path,decision: str) -> None:
    import psycopg
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.setattr(settings,'conversation_store','database')
    ctx=context(local_data_api,1001)
    owned,command_id=paused(local_data_api,ctx)
    case=WorkflowFixture(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id,
        session_id=owned.access.session_id,command_id=command_id)
    run_browser(native_api_client,case,'durable action workflow '+decision,tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select state from conversation.turns where turn_id=%s',(owned.turn.turn_id,)).fetchone()==('failed' if decision=='cancellation' else 'completed',)
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked' if decision.endswith('confirmation') else 'active',)
