"""Workflow 1 end to end: normal UI, personal v2 with the real planner and agents, native JWT/RLS data.

Run from backend/: TEST_BROWSER_LOCAL=1 TEST_WORKFLOW1_LIVE=1 ../.venv/bin/python -m pytest tests/test_workflow1_browser.py -q
Needs the native Data API (see test_data_api_local.py), frontend npm dependencies and a configured provider.
A signed local session replaces the password step; the hosted config covers real login (decline only).
"""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1' or os.getenv('TEST_WORKFLOW1_LIVE')!='1',
    reason='Opt in to native browser integration and actual Workflow 1 model calls')


@pytest.mark.parametrize('decision',['confirm','decline'])
def test_workflow_one(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path,decision: str) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'knowledge_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-workflow-one-browser-key-with-at-least-32-characters')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),user_id=persona_id(1001))
    run_browser(native_api_client,case,f'workflow one {decision}',tmp_path,timeout=900)
    with psycopg.connect(local_data_api.database_uri) as db:
        blocked=decision=='confirm'
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==(('temporarily_blocked' if blocked else 'active'),)
        assert db.execute("select state from actions.commands where type='card.temporary_block'").fetchall()==[(('succeeded' if blocked else 'rejected'),)]
        handoffs=db.execute('select reason,priority from support.handoffs order by created_at').fetchall()
        # Confirmed: staff verify the blocked card; Talk to a person adds its own urgent general handoff in a separate case.
        assert handoffs==([('card_verification','P0'),('general_support','P0')] if blocked else [('general_support','P0')])
        assert db.execute("select count(*) from conversation.turns where state='completed'").fetchone()[0]>=5
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
