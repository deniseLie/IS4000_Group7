"""Test vehicle for machinery that outlived the client card actions retired in 0081.

The action ledger (propose, confirm, reject, expiry, rollback), paused chat turns, notifications and the staff queue were
built and tested with the client's temporary card block and dispute draft. Those are bank-only since 0081, but the
machinery still serves other actions and turns saved before the change. This fixture re-enables the retired behaviour
inside one test's private database only, so those mechanics stay tested. It never runs against a real project.
"""
import psycopg
import pytest
from app.services import authorization,orchestrator
from tests.data_api_support import NativeAPI


@pytest.fixture
def legacy_card_actions(local_data_api: NativeAPI,monkeypatch: pytest.MonkeyPatch) -> NativeAPI:
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update actions.executors set validator='actions.card_block_target(jsonb,bigint)'::regprocedure where type='card.temporary_block'")
        db.execute("update actions.executors set validator='actions.dispute_target(jsonb,bigint)'::regprocedure where type='card.dispute'")
    monkeypatch.setattr(authorization,'BANK_ONLY_CARD_ACTIONS',frozenset({'card.unblock','card.cancel_permanently'}))
    monkeypatch.setitem(authorization.AGENT_ACTIONS,'fraud',frozenset({'card.temporary_block','card.dispute','card.order_replacement'}))
    monkeypatch.setattr(orchestrator,'propose_actions',orchestrator.offer_card_actions)
    return local_data_api
