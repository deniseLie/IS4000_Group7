"""Tool layer (F25): the same audited tool gives the same result directly and through an in-process MCP server."""
import asyncio
from contextlib import contextmanager
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from app.core.config import settings
from app.schemas.contracts import AgentName,Principal,RequestContext,Task,ToolResult
from app.schemas.knowledge import KnowledgeSection
from app.services import customer_data,kb,orchestrator,tools
from app.services.agents import fraud_records,retail_records
from app.services.authorization import PolicyDenied

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


@contextmanager
def transport(name: str):
    previous=settings.tool_transport
    settings.tool_transport=name
    try:yield
    finally:settings.tool_transport=previous


def evidence(name: str) -> list:
    with transport(name):
        task=Task(task_id='t1',domain='retail',intent='answer_question',priority='P3',entities={'question':'increase my card limit'})
        return asyncio.run(orchestrator.database_evidence(task,RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))))


def customer(agent: AgentName | None) -> RequestContext:
    return RequestContext(session_id=uuid4(),agent=agent,
        principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token'))


PROFILE={'name':'Michelle Ng (DEMO)','preferred_language':'en','employment_status':'self_employed'}
CARD={'card_id':'card_10021','name':'Signature Card','network':'mastercard','last4':'7310','status':'active','currency':'SGD',
    'limit':20000.0,'outstanding':5169.6,'minimum_payment':50.0,'due_date':'2026-11-20'}


@pytest.fixture
def readers(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Synthetic reader results; the tools, MCP transport and record loaders around them are the real ones."""
    seen: list[tuple]=[]
    def reader(name,data):
        async def read(ctx,*args):
            seen.append((name,ctx.agent,args))
            return ToolResult(data=data,as_of=datetime(2026,10,31,1,tzinfo=timezone.utc))
        return read
    for name,data in [('get_profile',PROFILE),('get_accounts',[]),('get_credit_cards',[CARD]),('get_loans',[]),('get_transactions',[])]:
        monkeypatch.setattr(customer_data,name,reader(name,data))
    return seen


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
    assert set(tools.TOOLS)==asyncio.run(listed())
    assert {'my_profile','my_accounts','my_cards','my_loans','my_transactions','my_tickets','my_compliance_case','my_documents',
        'my_document_checks','my_relationship_manager','my_requests','my_appointments','free_slots','knowledge_search'}<=set(tools.TOOLS)


def test_mcp_is_the_default_transport() -> None:
    from app.core.config import Settings
    assert Settings.model_fields['tool_transport'].default=='mcp'


@pytest.mark.parametrize('name',['direct','mcp'])
def test_agent_records_load_through_the_tool_layer(readers: list[tuple],name: str) -> None:
    pytest.importorskip('mcp.server.mcpserver')
    with transport(name):
        records=asyncio.run(retail_records.load(customer('retail')))
        security=asyncio.run(fraud_records.load(customer('fraud')))
    assert records.profile.name=='Michelle Ng (DEMO)' and [card.last4 for card in records.cards]==['7310']
    assert [card.last4 for card in security.cards]==['7310'] and security.transactions==[]
    # Each reader ran once per load inside the tool, with the calling agent's context and the 90-day window.
    assert [(row[0],row[1]) for row in readers]==[('get_profile','retail'),('get_accounts','retail'),('get_credit_cards','retail'),
        ('get_loans','retail'),('get_transactions','retail'),('get_credit_cards','fraud'),('get_transactions','fraud')]
    window=readers[4][2]
    assert (window[1]-window[0]).days==90 and window[2] is None


def test_mcp_keeps_the_tools_own_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip('mcp.server.mcpserver')
    async def changed(ctx):raise customer_data.CustomerDataIntegrityError('Customer snapshot does not match')
    monkeypatch.setattr(customer_data,'get_credit_cards',changed)
    with transport('mcp'):
        with pytest.raises(customer_data.CustomerDataIntegrityError):asyncio.run(tools.read('my_cards',{},customer('fraud')))


@pytest.mark.parametrize('name',['direct','mcp'])
def test_agent_allow_list_is_enforced_inside_the_tool(name: str) -> None:
    # The fraud agent may read cards and transactions only; the real reader refuses a loan read before opening a DB client.
    pytest.importorskip('mcp.server.mcpserver')
    with transport(name):
        with pytest.raises(PolicyDenied):asyncio.run(tools.read('my_loans',{},customer('fraud')))
        with pytest.raises(PolicyDenied):asyncio.run(tools.read('my_compliance_case',{},customer('retail')))


def test_every_tool_call_is_a_span_with_its_request_id(readers: list[tuple],monkeypatch: pytest.MonkeyPatch) -> None:
    spans: list[dict]=[]
    @contextmanager
    def span(name,kind,**attrs):
        spans.append({'name':name,**attrs})
        yield attrs
    monkeypatch.setattr(tools,'span',span)
    ctx=customer('retail')
    with transport('direct'):asyncio.run(tools.read('my_profile',{},ctx))
    assert spans==[{'name':'tool_call','tool':'my_profile','transport':'direct','request_id':str(ctx.request_id),'agent':'retail'}]


def test_tools_refuse_to_run_without_a_backend_context() -> None:
    with pytest.raises(tools.ToolContextMissing):asyncio.run(tools.my_tickets())
    with pytest.raises(KeyError):asyncio.run(tools.call('transfer_money',{},RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))))
