"""Tool layer (F21): the same audited tool gives the same result directly and through an in-process MCP server."""
import asyncio
import os
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.schemas.contracts import Principal,RequestContext,Task
from app.schemas.knowledge import KnowledgeSection
from app.services import kb,tools
from app.services.agents import runner
from tests.data_api_support import NativeAPI

SECTION=KnowledgeSection(article_id='card-limit-change',version=1,anchor='main',cite='kb:card-limit-change@v1#main',title='Changing your credit card limit',
    heading='Changing your credit card limit',body='The cards and credit team decides.',publisher='UBS Connect',
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
        ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='guest'),agent='guide')
        return asyncio.run(runner.read('knowledge_search',{'query':'increase my card limit','k':4},ctx,set()))
    finally:settings.tool_transport='direct'


def test_direct_tool_call_feeds_agent_evidence(fake_kb: list[str]) -> None:
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


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native JWT/RLS Data API')
@pytest.mark.parametrize('transport',['direct','mcp'])
def test_compliance_tools_keep_owned_records_and_policy_under_both_transports(local_data_api: NativeAPI,native_api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,transport: str) -> None:
    from app.schemas.contracts import ToolResult
    from app.services.authorization import PolicyDenied
    from app.services.prechecks import Workspace
    from tests.test_actions_api import context
    monkeypatch.setattr(settings,'tool_transport',transport)
    async def read() -> None:
        ctx=context(local_data_api,1002).model_copy(update={'agent':'compliance'})
        case=ToolResult.model_validate(await tools.call('my_compliance_case',{},ctx))
        checks=ToolResult.model_validate(await tools.call('my_document_checks',{},ctx))
        workspace=Workspace.model_validate(checks.data)
        assert workspace.review.case_id==UUID(case.data['case_id'])
        assert len(workspace.review.requested_items)==4
        other=context(local_data_api,1003).model_copy(update={'agent':'compliance'})
        assert (await tools.call('my_compliance_case',{},other))['data'] is None
        for name in ['my_compliance_case','my_document_checks']:
            with pytest.raises(PolicyDenied):await tools.call(name,{},ctx.model_copy(update={'agent':'retail'}))
            with pytest.raises(PolicyDenied):await tools.call(name,{},RequestContext(session_id=uuid4(),principal=Principal(kind='guest')))
        denied,allowed=await asyncio.gather(tools.call('my_compliance_case',{},ctx.model_copy(update={'agent':'retail'})),
            tools.call('my_compliance_case',{},ctx),return_exceptions=True)
        assert isinstance(denied,PolicyDenied) and allowed['data']['case_id']==case.data['case_id']
    asyncio.run(read())
