"""Normal React review/confirmation UI over native action transactions, JWT and RLS; no model substitute."""
import os
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.test_actions_api import card_command,context,owned_case,propose

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in with TEST_BROWSER_LOCAL=1 and both database/API flags')


class ActionsFixture(NativeBrowserSession):
    confirm_command_id: UUID
    reject_command_id: UUID
    expired_command_id: UUID
    failed_command_id: UUID
    handoff_command_id: UUID


def test_recorded_action_review_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    import psycopg
    monkeypatch.setattr(settings,'conversation_store','database')
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    records: list[ActionOutcome]=[propose(native_api_client,ctx,card_command(ctx,task)) for task in ['confirm','reject','expire','fail']]
    handoff=propose(native_api_client,ctx,ActionCommand(type='handoff.create',idempotency_key=f'{ctx.case_id}:handoff:handoff.create',
        target={'reason':'general_support','priority':'P3','summary':'Please review this synthetic support case.'}))
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update actions.commands set proposed_at=clock_timestamp()-interval '6 minutes',expires_at=clock_timestamp()-interval '1 second' where command_id=%s",(records[2].command.command_id,))
    case=ActionsFixture(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id,
        confirm_command_id=records[0].command.command_id,reject_command_id=records[1].command.command_id,
        expired_command_id=records[2].command.command_id,failed_command_id=records[3].command.command_id,handoff_command_id=handoff.command.command_id)
    run_browser(native_api_client,case,'recorded action review',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked',)
        assert db.execute("select count(*) from actions.receipts where outcome='succeeded'").fetchone()==(2,)
        assert db.execute("select count(*) from actions.receipts where outcome='failed'").fetchone()==(1,)
        assert db.execute('select count(*) from events.outbox where command_id is not null').fetchone()==(3,)


def test_empty_action_review_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    ctx=context(local_data_api,1003)
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id)
    run_browser(native_api_client,case,'recorded action empty',tmp_path)
