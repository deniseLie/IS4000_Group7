"""Hybrid knowledge search with its keyword fallback (KNOWLEDGE_RESPELL, migration 0099).

No vectors are made up here: the fallback test uses a real unconfigured embedding client, and the database stub only
records which RPC was called. SQL ranking is in test_knowledge_database.py; real-provider retrieval quality is the
opt-in hybrid check in test_retrieval_eval.py.
"""
import asyncio
from uuid import uuid4
import pytest
from app.core.config import Settings, settings
from app.schemas.contracts import Principal, RequestContext
from app.schemas.knowledge import KnowledgeQuery
from app.services import audit, embeddings, kb
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


def test_search_needs_no_switch_and_runs_without_an_embedding_key() -> None:
    assert not hasattr(Settings(embedding_api_key=None), 'knowledge_search') and not hasattr(Settings(), 'knowledge_store')


def test_without_an_embedding_key_search_is_keyword_only_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'embedding_api_key', None)
    embeddings.client.cache_clear()
    db, batch = RecordingDb(), audit.start_batch()
    try:assert asyncio.run(kb.search(KnowledgeQuery(query='wats my crad bal', scope='public', k=4), guest(), db)) == []
    finally:embeddings.client.cache_clear()
    assert db.calls == [('kb_search', {'p_query': 'whats my card balance', 'p_categories': [], 'p_k': 4})]
    assert batch.events[-1].details == {'tool': 'kb.search', 'result_count': 0, 'mode': 'keyword_only', 'embedding_error': 'not_configured'}


def test_a_failed_embedding_request_falls_back_to_keyword_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(text: str, model: str | None = None, api: object = None) -> object:
        raise embeddings.EmbeddingError('request_failed:APITimeoutError')
    monkeypatch.setattr(embeddings, 'embed_query', failing)
    db, batch = RecordingDb(), audit.start_batch()
    asyncio.run(kb.search(KnowledgeQuery(query='fixed deposit', scope='retail', k=4), guest(), db))
    assert [name for name, _ in db.calls] == ['kb_search'] and db.calls[0][1]['p_categories'] == ['retail']
    assert batch.events[-1].details['mode'] == 'keyword_only' and batch.events[-1].details['embedding_error'] == 'request_failed:APITimeoutError'


def test_respell_switch_changes_the_search_text(monkeypatch: pytest.MonkeyPatch) -> None:
    assert for_search('Wats my CRAD bal') == 'whats my card balance'
    monkeypatch.setattr(settings, 'knowledge_respell', False)
    assert for_search('Wats my CRAD bal') == 'wats my crad bal'


def test_hosted_sql_embedder_matches_the_python_one_and_holds_no_key() -> None:
    """hosted/embed_knowledge.sql must store what scripts/embed_knowledge.py stores, so --check agrees with it."""
    from pathlib import Path
    from scripts import embed_knowledge
    sql = (Path(__file__).parents[1] / 'supabase/hosted/embed_knowledge.sql').read_text(encoding='utf-8')
    assert embed_knowledge.text('T', 'H', 'B') == 'T\nH\nB'
    assert sql.count("a.title||E'\\n'||s.heading||E'\\n'||s.body") == 3
    assert "extensions.digest(convert_to(b->>'text','UTF8'),'sha256'),'hex')" in sql
    assert f"p_model text:='{Settings().embedding_model}'" in sql and f"'{Settings().embedding_base_url}/embeddings'" in sql
    assert "api_key text:='PASTE_EMBEDDING_API_KEY_HERE'" in sql and 'sk-' not in sql
    assert sql.index('create extension if not exists http') < sql.index('drop extension http') < sql.index('commit;')
