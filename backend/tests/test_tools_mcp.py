"""Tool layer (F21): the same audited tool gives the same result directly and through an in-process MCP server."""
import asyncio
from uuid import uuid4
import pytest
from app.core.config import settings
from app.schemas.contracts import Principal,RequestContext,Task
from app.schemas.knowledge import KnowledgeSection
from app.services import kb,orchestrator,tools

SECTION=KnowledgeSection(article_id='card-limit-change',version=1,anchor='main',cite='kb:card-limit-change@v1#main',title='Changing your credit card limit',
    heading='Changing your credit card limit',body='The cards and credit team decides.',publisher='DemoBank coursework project',
    scope='Fictional demo guidance; not a live bank policy.',reference_url=None,checked_on=None,review_due=None,score=1.0)


@pytest.fixture
def fake_kb(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str]=[]
    async def search(query,ctx,db):
        seen.append(query.query)
        return [SECTION]
    monkeypatch.setattr(kb,'search',search)
    monkeypatch.setattr(tools.data_api,'connect',lambda ctx:__import__('contextlib').nullcontext())
    return seen


def evidence(transport: str) -> list:
    settings.tool_transport=transport
    try:
        task=Task(task_id='t1',domain='retail',intent='answer_question',priority='P3',entities={'question':'increase my card limit'})
        return asyncio.run(orchestrator.database_evidence(task,RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))))
    finally:settings.tool_transport='direct'


def test_direct_tool_call_feeds_orchestrator_evidence(fake_kb: list[str]) -> None:
    rows=evidence('direct')
    assert [row.id for row in rows]==['kb:card-limit-change@v1#main'] and fake_kb==['increase my card limit']


def test_mcp_transport_is_a_real_tools_call_with_the_same_result(fake_kb: list[str]) -> None:
    pytest.importorskip('mcp.server.mcpserver')
    assert [row.model_dump() for row in evidence('mcp')]==[row.model_dump() for row in evidence('direct')]
    from app.mcp_server import build_server
    import mcp
    async def listed():
        async with mcp.Client(build_server()) as client:return {tool.name for tool in (await client.list_tools()).tools}
    assert {'knowledge_search','request_categories','my_tickets','my_cards','my_transactions'}<=asyncio.run(listed())


def test_tools_refuse_to_run_without_a_backend_context() -> None:
    with pytest.raises(tools.ToolContextMissing):asyncio.run(tools.my_tickets())
    with pytest.raises(KeyError):asyncio.run(tools.call('transfer_money',{},RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))))
