"""The tool boundary: agent, rule, orchestrator, RM and chat-insights code reads records only through tools.call, which
sends every read through the in-process MCP server. Nothing here calls a model or a database: the tools are synthetic
and the planner returns a fixed plan, so the real loaders, rules and graph run."""
import ast
import asyncio
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from app.core.observability import span_sink
from app.schemas.chat import ChatRequest
from app.schemas.contracts import PlanTask
from app.services import orchestrator,tools
from app.services.guardrail import assess_request
from tests.test_orchestrator_graph import context
from tests.test_runner import CARD,TXN

SERVICES=Path(__file__).resolve().parents[1]/'app'/'services'
READERS={'customer_data','kb','external'}  # the read services behind the tools; their error classes and types may be imported
RPC_READS=('customer_read_','customer_topic_','kb_search','kb_hybrid_search','kb_get_')  # the read RPCs behind the tools
# The data layer behind tool functions (only tools.py calls their reads): fraud_records backs tools.travel_flags, rm/records
# the rm_* tools. agents/fraud.py is retired with the Security agent (build plan 2b): its respond() runs only in tests.
LIBRARIES={'agents/fraud_records.py','rm/records.py'}
RETIRED={'agents/fraud.py'}
GUARDED=sorted(p for p in [*SERVICES.glob('agents/*.py'),*SERVICES.glob('rm/*.py'),*(SERVICES/f for f in
    ('orchestrator.py','rm_handover.py','talking_points.py','talking_points_state.py'))] if p.relative_to(SERVICES).as_posix() not in LIBRARIES)


def reads(path: Path) -> list[str]:
    """What a module takes from customer_data, kb or external other than a class (an error or a type), and the read RPC
    names it spells out."""
    tree=ast.parse(path.read_text(encoding='utf-8'))
    aliases,used=set(),[]
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and node.module=='app.services':aliases|={a.asname or a.name for a in node.names if a.name in READERS}
        elif isinstance(node,ast.ImportFrom) and (node.module or '').startswith('app.services.') and node.module.rsplit('.',1)[-1] in READERS:
            used+=[f'{node.module}.{a.name}' for a in node.names if not a.name[:1].isupper()]
        elif isinstance(node,ast.Import):used+=[a.name for a in node.names if a.name.startswith('app.services.') and a.name.rsplit('.',1)[-1] in READERS]
    for node in ast.walk(tree):
        if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id in aliases and not node.attr[:1].isupper():
            used.append(f'{node.value.id}.{node.attr}')
        elif isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value.startswith(RPC_READS):used.append(node.value)
        elif isinstance(node,ast.Attribute) and node.attr=='load' and isinstance(node.value,ast.Name) and node.value.id=='fraud_records' \
                and path.relative_to(SERVICES).as_posix() not in RETIRED:used.append('fraud_records.load')
    return used


def test_agents_rules_orchestrator_rm_and_chat_insights_read_only_through_tools() -> None:
    assert len(GUARDED)>30 and SERVICES/'orchestrator.py' in GUARDED and SERVICES/'rm'/'handlers.py' in GUARDED
    found={p.relative_to(SERVICES).as_posix():used for p in GUARDED if (used:=reads(p))}
    assert found=={},found


def test_the_guard_sees_a_direct_read(tmp_path: Path) -> None:
    sample=tmp_path/'sample.py'
    sample.write_text('from app.services import customer_data,kb\nfrom app.services.external import ExternalError,fx_rate\n'
        'async def f(ctx,db):\n    await customer_data.get_tickets(ctx)\n    await kb.search(None,ctx,db)\n'
        "    db.rpc('customer_read_tickets',{})\n    raise customer_data.CustomerDataIntegrityError('x')\n",encoding='utf-8')
    import tests.test_tool_boundary as guard
    real,guard.SERVICES=guard.SERVICES,tmp_path  # the fraud_records rule names a module by its path under app/services
    try:assert sorted(reads(sample))==['app.services.external.fx_rate','customer_data.get_tickets','customer_read_tickets','kb.search']
    finally:guard.SERVICES=real


NOW=datetime(2026,11,8,1,0,tzinfo=timezone.utc)
TICKET={'ticket_id':str(uuid4()),'reference':'TCK-001040','category':'card_limit_change','category_label':'Card limit change',
    'summary':'Raise my card limit','details':{},'urgency':'normal','status':'resolved','owner_team':'Cards and credit team',
    'decided_by':'Cards and credit team','source':'form','created_at':NOW.isoformat(),'updated_at':NOW.isoformat(),
    'target_at':NOW.isoformat(),'overdue':False,'last_update':None}


def test_a_client_turn_reads_every_record_through_mcp_as_its_own_agent(monkeypatch) -> None:
    """Two agents in parallel (Accounts: her card balance from the retail loader; Relationship: request status from her
    tickets). Every tool_call span says transport=mcp, and each tool ran with the agent's own context, set per call by the
    MCP server from the call's `_meta` key."""
    ran=[]
    def tool(name,result):
        async def run(**args):
            ctx=tools._ctx()
            ran.append((name,ctx.agent,ctx.principal.customer_id))
            return {**result,'call_id':str(uuid4()),'as_of':NOW.isoformat()}
        return run
    rows={'knowledge_search':{'items':[]},'my_profile':{'data':{'name':'Michelle Tan','preferred_language':'English','employment_status':'employed'}},
        'my_accounts':{'items':[]},'my_cards':{'items':[{**CARD,'customer_id':1002}]},'my_loans':{'items':[]},
        'my_transactions':{'items':[{**TXN,'txn_id':'txn_1','customer_id':1002}]},'my_tickets':{'items':[TICKET]}}
    for name,result in rows.items():monkeypatch.setitem(tools.TOOLS,name,tool(name,result))
    async def model_plan(history):
        return [PlanTask(id='t1',agent='accounts',intent='card_figures',question='What is my card balance?'),
            PlanTask(id='t2',agent='relationship',intent='request_status',question='What is the status of my requests?')]
    monkeypatch.setattr(orchestrator,'model_plan',model_plan)
    req=ChatRequest(messages=[{'role':'user','content':'How much do I owe on my card, and is there news on what I asked the bank for?'}],mode='personal')  # not a rule-planned wording
    spans: list=[]
    async def main():
        token=span_sink.set(spans)
        try:return [item async for item in orchestrator.stream(req,assess_request(req),context(conversation=False))]
        finally:span_sink.reset(token)
    items=asyncio.run(main())
    answer=items[-1][1]
    assert items[-1][0]=='answer' and [(s.task_id,s.needs_human) for s in answer.sections]==[('t1',False),('t2',False)],answer
    assert '3,850.25' in answer.sections[0].answer and 'TCK-001040' in answer.sections[1].answer
    calls=[a for n,a in spans if n=='tool_call']
    assert len(calls)==len(ran)>=7 and {a['transport'] for a in calls}=={'mcp'} and all(a['allowed'] for a in calls)
    assert {name for name,_,_ in ran}>={'my_profile','my_accounts','my_cards','my_loans','my_transactions','my_tickets'}
    assert {customer for _,_,customer in ran}=={1002} and {(name,agent) for name,agent,_ in ran if name=='my_tickets'}=={('my_tickets','retail')}
    assert {agent for name,agent,_ in ran if name in {'my_profile','my_cards'}}=={'retail'}  # the v4 retail allow-list the rules run under
