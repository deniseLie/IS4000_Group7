"""Tool layer (F21): every tool call is a real MCP tools/call through the in-process server, with the caller's identity
looked up per call from an opaque `_meta` key (never an argument), on one shared client per event loop."""
import asyncio
import os
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from app import mcp_server
from app.core.observability import span_sink
from app.schemas.contracts import Principal,RequestContext
from app.schemas.knowledge import KnowledgeSection
from app.services import kb,tools
from app.services.agents import runner
from app.services.authorization import PolicyDenied
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


def guest(agent='guide') -> RequestContext:
    return RequestContext(session_id=uuid4(),principal=Principal(kind='guest'),agent=agent)


def test_a_tool_call_is_an_mcp_tools_call_that_feeds_agent_evidence(fake_kb: list[str],monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip('mcp.server.mcpserver')
    sent: list[tuple[str,dict,dict]]=[]
    real=mcp_server.call_in_process
    async def watched(name,args,ctx):
        client=await mcp_server.client()
        original=client.call_tool
        async def call_tool(tool,arguments,*,meta):
            sent.append((tool,arguments,meta))
            return await original(tool,arguments,meta=meta)
        monkeypatch.setattr(client,'call_tool',call_tool)
        try:return await real(name,args,ctx)
        finally:monkeypatch.setattr(client,'call_tool',original)
    monkeypatch.setattr(mcp_server,'call_in_process',watched)
    spans: list=[]
    async def run():
        token=span_sink.set(spans)
        try:return await runner.read('knowledge_search',{'query':'increase my card limit','k':4},guest(),set())
        finally:span_sink.reset(token)
    rows=asyncio.run(run())
    assert [row.id for row in rows]==['kb:card-limit-change@v1#main'] and fake_kb==['increase my card limit']
    (tool,arguments,meta),=sent
    assert tool=='knowledge_search' and arguments=={'query':'increase my card limit','k':4}  # no identity or JWT in the arguments
    assert set(meta)=={mcp_server.CALL_KEY} and meta[mcp_server.CALL_KEY] not in mcp_server.CALLS  # a one-time key, gone after the call
    assert [a['transport'] for n,a in spans if n=='tool_call']==['mcp']
    import mcp
    async def listed():
        async with mcp.Client(mcp_server.build_server()) as client:return {t.name:t for t in (await client.list_tools()).tools}
    served=asyncio.run(listed())
    assert set(served)==set(tools.SERVED) and {'my_requests','my_introduction','my_consent_sharing','my_meeting_requests','my_topic_state'}<=set(served)
    assert all('mcp_call' not in t.input_schema.get('properties',{}) for t in served.values())  # the request Context is not an argument
    assert served['my_topic_state'].input_schema['required']==['turn_id'] and served['my_tickets'].input_schema.get('properties',{})=={}


def test_one_shared_client_keeps_each_parallel_call_s_own_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def my_tickets():
        ctx=tools._ctx()
        await asyncio.sleep(0.01*(ctx.principal.customer_id%3))  # finish out of order
        return {'items':[{'reference':f'TCK-{ctx.principal.customer_id}'}],'call_id':str(uuid4()),'as_of':'2026-11-08T09:00:00+08:00'}
    monkeypatch.setitem(tools.TOOLS,'my_tickets',my_tickets)
    def customer(n: int) -> RequestContext:
        return RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=n),agent='retail')
    async def run():
        out=await asyncio.gather(*(tools.call('my_tickets',{},customer(n)) for n in range(1001,1009)))
        first=await mcp_server.client()
        assert await mcp_server.client() is first  # one client per loop, reused
        return out
    out=asyncio.run(run())
    assert [o['items'][0]['reference'] for o in out]==[f'TCK-{n}' for n in range(1001,1009)] and not mcp_server.CALLS


def test_the_tool_s_own_exception_reaches_the_caller_and_a_call_without_a_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused():
        raise PolicyDenied('Agent may not use tool: tickets.list')
    monkeypatch.setitem(tools.TOOLS,'my_tickets',refused)
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=1001),agent='retail')
    with pytest.raises(PolicyDenied,match='tickets.list'):asyncio.run(tools.call('my_tickets',{},ctx))
    async def unkeyed():
        client=await mcp_server.client()
        return [await client.call_tool('my_tickets',{}),await client.call_tool('my_tickets',{},meta={mcp_server.CALL_KEY:'made-up'})]
    assert all(result.is_error for result in asyncio.run(unkeyed()))


def test_tools_refuse_to_run_without_a_backend_context() -> None:
    with pytest.raises(tools.ToolContextMissing):asyncio.run(tools.my_tickets())
    with pytest.raises(KeyError):asyncio.run(tools.call('transfer_money',{},guest(None)))


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native JWT/RLS Data API')
def test_compliance_tools_keep_owned_records_and_policy_through_mcp(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    from app.schemas.contracts import ToolResult
    from app.services.prechecks import Workspace
    from tests.test_actions_api import context
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
