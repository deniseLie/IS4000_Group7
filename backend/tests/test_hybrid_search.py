"""Hybrid knowledge search switches and fallback (KNOWLEDGE_SEARCH, KNOWLEDGE_RESPELL, migration 0099).

No vectors are made up here: the fallback test uses a real unconfigured embedding client, and the database stub only
records which RPC was called. SQL ranking is in test_knowledge_database.py; real-provider retrieval quality is the
opt-in hybrid check in test_retrieval_eval.py.
"""
import asyncio
from uuid import uuid4
import pytest
from pydantic import SecretStr, ValidationError
from app.core.config import Settings, settings
from app.schemas.contracts import Principal, RequestContext
from app.schemas.knowledge import KnowledgeQuery
from app.services import audit, embeddings, kb, knowledge
from app.services.typos import for_search


class RecordingDb:
    """Stands in for the Data API client: records the RPC name and arguments and returns no rows."""
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, args: dict) -> 'RecordingDb':
        self.calls.append((name, args))
        return self

    def execute(self) -> object:
        return type('Response', (), {'data': []})()


def guest() -> RequestContext:
    return RequestContext(session_id=uuid4(), principal=Principal(kind='guest'))


def test_hybrid_needs_the_database_store_and_its_own_key() -> None:
    with pytest.raises(ValidationError, match='KNOWLEDGE_STORE=database'):
        Settings(knowledge_search='hybrid', knowledge_store='memory', embedding_api_key=SecretStr('k'))
    with pytest.raises(ValidationError, match='EMBEDDING_API_KEY'):
        Settings(knowledge_search='hybrid', knowledge_store='database', embedding_api_key=None)
    assert Settings(knowledge_search='hybrid', knowledge_store='database', embedding_api_key=SecretStr('k')).knowledge_search == 'hybrid'


def test_keyword_search_calls_kb_search_and_keeps_its_audit_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'knowledge_search', 'keyword')
    db, batch = RecordingDb(), audit.start_batch()
    assert asyncio.run(kb.search(KnowledgeQuery(query='wats my crad bal', scope='public', k=4), guest(), db)) == []
    assert db.calls == [('kb_search', {'p_query': 'whats my card balance', 'p_categories': [], 'p_k': 4})]
    assert batch.events[-1].details == {'tool': 'kb.search', 'result_count': 0}


def test_hybrid_falls_back_to_keyword_search_and_records_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'knowledge_search', 'hybrid')
    monkeypatch.setattr(settings, 'embedding_api_key', None)
    embeddings.client.cache_clear()
    db, batch = RecordingDb(), audit.start_batch()
    try:asyncio.run(kb.search(KnowledgeQuery(query='fixed deposit', scope='retail', k=4), guest(), db))
    finally:embeddings.client.cache_clear()
    assert [name for name, _ in db.calls] == ['kb_search']
    assert batch.events[-1].details == {'tool': 'kb.search', 'result_count': 0, 'mode': 'keyword_only', 'embedding_error': 'not_configured'}


def test_respell_switch_applies_to_both_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    assert for_search('Wats my CRAD bal') == 'whats my card balance'
    monkeypatch.setattr(settings, 'knowledge_respell', False)
    assert for_search('Wats my CRAD bal') == 'wats my crad bal'
    assert 'sg-card-disputes' in [a.id for a in knowledge.search('dispute a charge')]
