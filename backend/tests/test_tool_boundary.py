"""The tool boundary: agent, rule, orchestrator, RM, chat-insights and meeting code reads records only through tools.call
or, for the meeting agents, tools.meeting_read, which send every read through the in-process MCP server. Nothing here
calls a model or a database: the tools and the meeting SQL reader are synthetic and the planner returns a fixed plan, so
the real loaders, rules, graph and meeting policy run."""
import ast
import asyncio
from datetime import datetime,timezone
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from app import mcp_server
from app.core.observability import span_sink
from app.schemas.chat import ChatRequest
from app.schemas.contracts import PlanTask,RequestContext
from app.services import orchestrator,tools
from app.services.agents import registry
from app.services.authorization import PolicyDenied
from app.services.guardrail import assess_request
from app.services.meetings import database as meeting_db
from app.services.meetings.database import ApplicationDatabase,MeetingStoreError
from evaluation.meeting_boundaries import attempt,cases
from tests.test_orchestrator_graph import context
from tests.test_runner import CARD,TXN

SERVICES=Path(__file__).resolve().parents[1]/'app'/'services'
READERS={'customer_data','kb','external'}  # the read services behind the tools; their error classes and types may be imported
RPC_READS=('customer_read_','customer_topic_','kb_search','kb_hybrid_search','kb_get_')  # the read RPCs behind the tools
# The data layer behind tool functions (only tools.py calls their reads): fraud_records backs tools.travel_flags, rm/records
# the rm_* tools. agents/fraud.py is retired with the Security agent (build plan 2b): its respond() runs only in tests.
LIBRARIES={'agents/fraud_records.py','rm/records.py'}
RETIRED={'agents/fraud.py'}
GUARDED=sorted(p for p in [*SERVICES.glob('agents/*.py'),*SERVICES.glob('rm/*.py'),*SERVICES.glob('meetings/*.py'),*(SERVICES/f for f in
    ('orchestrator.py','rm_handover.py','talking_points.py','talking_points_state.py'))] if p.relative_to(SERVICES).as_posix() not in LIBRARIES)
# The meeting side (AGENTS.md "Meeting lifecycle seam"): its services and its two routers.
MEETING_CODE=sorted([*SERVICES.glob('meetings/*.py'),SERVICES.parent/'api'/'meetings.py',SERVICES.parent/'api'/'meeting_preparation.py'])
# Connectors that do not go through MCP: the UI's ApplicationDatabase.call, the draft gate agent_call/meeting_call, the SQL
# reader itself, the Data API and psycopg. A thread hop (asyncio.to_thread, run_in_threadpool) counts as the function it runs.
DIRECT={'call','agent_call','meeting_call','read','_execute','rpc','execute'}
HOPS={'to_thread','run_in_threadpool'}


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


def direct_meeting_reads(path: Path) -> list[str]:
    """Meeting agent reads (tools.MEETING_READS) a module sends past MCP: named as an argument of a direct connector, or in
    SQL text it executes. agent_read (MCP) and error records such as store_error may name them."""
    found=[]
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if not isinstance(node,ast.Call):continue
        named=lambda f:f.attr if isinstance(f,ast.Attribute) else getattr(f,'id',None)
        callee=named(node.func)
        if callee in HOPS and node.args:callee=named(node.args[0])
        if callee not in DIRECT:continue
        for arg in [*node.args,*(k.value for k in node.keywords)]:
            if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and any(name in arg.value for name in tools.MEETING_READS):
                found.append(f'{callee}:{arg.value}')
    return found


def test_agents_rules_orchestrator_rm_and_chat_insights_read_only_through_tools() -> None:
    assert len(GUARDED)>50 and SERVICES/'orchestrator.py' in GUARDED and SERVICES/'rm'/'handlers.py' in GUARDED
    assert SERVICES/'meetings'/'preparation.py' in GUARDED and SERVICES/'meetings'/'worker.py' in GUARDED
    found={p.relative_to(SERVICES).as_posix():used for p in GUARDED if (used:=reads(p))}
    assert found=={},found


def test_meeting_code_sends_every_agent_read_through_mcp() -> None:
    """The three assigned-RM reads in the meeting catalogue are the MCP meeting reads; meeting code names them only to
    agent_read, never to a direct connector, and the UI's ApplicationDatabase.call cannot run one."""
    assert set(tools.MEETING_READS)=={t.name for s in registry.MEETING_SPECS.values() for t in s.tools if t.access=='read' and t.actor=='assigned_rm'}
    assert set(tools.MEETING_READS)<=set(tools.SERVED) and set(tools.MEETING_READS).isdisjoint(tools.TOOLS)  # in-process only: not call(), not stdio
    assert registry.MEETING_TOOLS-set(tools.MEETING_READS)=={'meeting_save_brief','meeting_save_pack','meeting_save_reviewed_draft',
        'meeting_job_context','meeting_save_job_draft'}  # drafts and the worker's claimed calls stay off MCP
    assert meeting_db.PUBLIC.isdisjoint(tools.MEETING_READS)
    assert len(MEETING_CODE)>20
    found={p.name:used for p in MEETING_CODE if (used:=direct_meeting_reads(p))}
    assert found=={},found


def test_the_meeting_guard_sees_a_direct_read(tmp_path: Path) -> None:
    sample=tmp_path/'sample.py'
    sample.write_text('import asyncio\nasync def f(database,meeting,claims,db):\n'
        "    await asyncio.to_thread(database.agent_call,'brief_writer','meeting_preparation_inputs',meeting,{},claims)\n"
        "    database.call('meeting_notes_context',{},claims)\n    db.execute('select public.meeting_pack_inputs(%s)',(meeting,))\n"
        "    await database.agent_read('brief_writer','meeting_preparation_inputs',meeting,{},claims)\n",encoding='utf-8')
    assert sorted(direct_meeting_reads(sample))==['agent_call:meeting_preparation_inputs','call:meeting_notes_context','execute:select public.meeting_pack_inputs(%s)']


def test_the_meeting_worker_stays_off_mcp_and_out_of_client_records() -> None:
    """The worker has no user JWT: its connector (WorkerDatabase, its private login) never reaches MCP or the Data API, and
    the modules only the worker runs import no tool, MCP, RM or client-record service."""
    source=(SERVICES/'meetings'/'database.py').read_text(encoding='utf-8')
    worker=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='WorkerDatabase')
    used={n.id for n in ast.walk(worker) if isinstance(n,ast.Name)}|{n.attr for n in ast.walk(worker) if isinstance(n,ast.Attribute)}
    assert used.isdisjoint({'meeting_read','agent_read','call_in_process','data_api','ApplicationDatabase','MEETING_ACCESS'}),used
    only=[p for p in SERVICES.glob('meetings/*.py') if 'WorkerDatabase' in (text:=p.read_text(encoding='utf-8')) and 'ApplicationDatabase' not in text]
    assert {p.name for p in only}>={'worker.py','lifecycle.py','chunks.py','discovery.py','scheduling.py','zoom_ingest.py','checkpoints.py'}
    banned=('app.services.tools','app.mcp_server','app.services.rm','app.services.customer_data','app.services.kb','app.core.data_api')
    imports={p.name:[m for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.Import,ast.ImportFrom))
        for m in ([n.module or ''] if isinstance(n,ast.ImportFrom) else [a.name for a in n.names])
        +([f'{n.module}.{a.name}' for a in n.names] if isinstance(n,ast.ImportFrom) else []) if m.startswith(banned)] for p in only}
    assert {k:v for k,v in imports.items() if v}=={}


def watch_meeting_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str,dict,dict]]:
    """Record each MCP tools/call a meeting read sends (tool, arguments, `_meta`)."""
    sent: list[tuple[str,dict,dict]]=[]
    real=mcp_server.call_in_process
    async def watched(name,args,ctx,access=None):
        client=await mcp_server.client()
        original=client.call_tool
        async def call_tool(tool,arguments,*,meta):
            sent.append((tool,arguments,meta))
            return await original(tool,arguments,meta=meta)
        monkeypatch.setattr(client,'call_tool',call_tool)
        try:return await real(name,args,ctx,access)
        finally:monkeypatch.setattr(client,'call_tool',original)
    monkeypatch.setattr(mcp_server,'call_in_process',watched)
    return sent


AMOUNTS=[{'label':'Deposit','amount':'250000.00','currency':'SGD'}]


@pytest.mark.parametrize('case',[c for c in cases() if c.tool in tools.MEETING_READS and c.actor not in ('rm_chat','client_chat')],ids=lambda c:c.id)
def test_a_meeting_read_is_an_mcp_tools_call_under_the_meeting_policy(case,monkeypatch: pytest.MonkeyPatch) -> None:
    """Each catalogue policy case for an MCP meeting read, sent through the real in-process server: the identity travels
    only as the one-time `_meta` key, the tool decides with the same rule as meeting_call, and only an allowed call reaches
    the SQL reader, with the caller's own principal (her JWT)."""
    access,target=attempt(case)
    ran: list=[]
    def read(self,name,parameters):
        ran.append((name,dict(parameters),self.ctx.principal))
        return {'meeting_id':target['meeting_id'],'revision':3}
    monkeypatch.setattr(ApplicationDatabase,'read',read)
    sent=watch_meeting_calls(monkeypatch)
    args={'p_meeting_id':target['meeting_id'],**({'p_amounts':AMOUNTS} if case.tool=='meeting_pack_inputs' else {})}
    spans: list=[]
    async def run():
        token=span_sink.set(spans)
        try:return await tools.meeting_read(case.tool,args,RequestContext(session_id=uuid4(),principal=access.principal),access)
        finally:span_sink.reset(token)
    allowed=case.expected_rule=='explicit_rule'
    if allowed:
        assert asyncio.run(run())=={'meeting_id':target['meeting_id'],'revision':3}
        (name,parameters,principal),=ran
        assert name==case.tool and principal==access.principal and parameters['p_meeting_id']==UUID(target['meeting_id'])
        if case.tool=='meeting_pack_inputs':assert parameters['p_amounts'].obj==AMOUNTS
    else:
        with pytest.raises(PolicyDenied,match=case.expected_rule):asyncio.run(run())
        assert ran==[]
    (tool,arguments,meta),=sent
    assert tool==case.tool and arguments==args  # no identity, claims or JWT in the arguments
    assert set(meta)=={mcp_server.CALL_KEY} and not mcp_server.CALLS  # a one-time key, gone after the call
    (name,facts),=[s for s in spans if s[0]=='meeting.tool_call']
    assert facts['transport']=='mcp' and facts['side']=='meeting' and facts['agent']==case.agent and facts['allowed']==allowed
    assert set(facts)<={'tool','agent','side','transport','allowed','reason','error_type'}
    if not allowed:assert facts['reason']==case.expected_rule


def test_agent_read_keeps_the_connector_s_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through ApplicationDatabase.agent_read: an overridden meeting id is refused before the SQL reader (42501, as
    agent_call does for drafts), a SQL refusal is traced as meeting_scope_denied, and agent_call no longer runs a read."""
    access,_=attempt(next(c for c in cases() if c.id=='MA01'))
    database=ApplicationDatabase(RequestContext(session_id=uuid4(),principal=access.principal))
    outcomes: list=[{'sources':[]},MeetingStoreError('42501','permission denied for meeting')]
    def read(self,name,parameters):
        outcome=outcomes.pop(0)
        if isinstance(outcome,Exception):raise outcome
        return outcome
    monkeypatch.setattr(ApplicationDatabase,'read',read)
    spans: list=[]
    async def run(meeting):
        token=span_sink.set(spans)
        try:return await database.agent_read(access.agent,'meeting_preparation_inputs',access.meeting_id,{'p_meeting_id':meeting},access.claims)
        finally:span_sink.reset(token)
    with pytest.raises(MeetingStoreError,match='meeting_scope_mismatch') as refused:asyncio.run(run(uuid4()))
    assert refused.value.code=='42501' and len(outcomes)==2
    assert asyncio.run(run(access.meeting_id))=={'sources':[]}
    with pytest.raises(MeetingStoreError) as denied:asyncio.run(run(access.meeting_id))
    assert denied.value.code=='42501'
    assert [(f['transport'],f['allowed'],f.get('reason')) for n,f in spans if n=='meeting.tool_call']==[
        ('mcp',False,'meeting_scope_mismatch'),('mcp',True,None),('mcp',False,'meeting_scope_denied')]
    with pytest.raises(ValueError,match='agent_read'):
        database.agent_call(access.agent,'meeting_preparation_inputs',access.meeting_id,{'p_meeting_id':access.meeting_id},access.claims)


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
