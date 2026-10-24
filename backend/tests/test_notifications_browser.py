"""Normal action-to-inbox UI with an actual restricted worker running in the API lifespan.

Native PostgreSQL does not provide Supabase Realtime. This suite explicitly reloads the inbox;
it proves worker/API/UI integration, not hosted push delivery or password login.
"""
import os
from pathlib import Path
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.test_actions_api import card_command,context,owned_case,propose
from tests.test_notifications import worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser checks')


class NotificationFixture(NativeBrowserSession):
    command_id: UUID


def test_card_action_to_recorded_inbox(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    proposal=propose(native_api_client,ctx,card_command(ctx,'inbox-ui'))
    monkeypatch.setattr(settings,'run_worker_in_api',True)
    monkeypatch.setattr(settings,'worker_database_url',worker_uri)
    monkeypatch.setattr(settings,'worker_poll_seconds',0.2)
    case=NotificationFixture(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id,
        command_id=proposal.command.command_id)
    run_browser(native_api_client,case,'recorded notification delivery',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select read_at is not null from notify.inbox where customer_id=1001').fetchone()==(True,)
        assert db.execute('select status from events.outbox where command_id=%s',(proposal.command.command_id,)).fetchone()==('done',)


def test_empty_inbox_and_guest_scope(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    monkeypatch.setattr(settings,'run_worker_in_api',True)
    monkeypatch.setattr(settings,'worker_database_url',worker_uri)
    # Michelle's open recorded information deadline now produces a service reminder.
    ctx=context(local_data_api,1001)
    run_browser(native_api_client,NativeBrowserSession(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id),
        'recorded notification empty',tmp_path)
