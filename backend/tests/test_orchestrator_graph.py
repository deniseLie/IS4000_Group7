"""v5 graph wiring: planner → agent (Send ×N) → gather → compose, and planner → act → compose for an answer to an open offer.
Agent outputs here are fixed safety-origin fixtures for the graph mechanics; no model answer is simulated."""
import asyncio
from uuid import uuid4
import pytest
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentOutput,AgentResult,PlanTask,Principal,RequestContext
from app.services import offers,orchestrator
from app.services.agents import runner
from app.services.guardrail import assess_request
from app.services.orchestrator import Confirmation,TurnContext,after_planner,build_graph,start


def context(kind: str='customer',conversation: bool=True) -> TurnContext:
    principal=Principal(kind=kind,user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token') if kind=='customer' else Principal(kind='guest')
    ctx=RequestContext(session_id=uuid4(),case_id=uuid4() if kind=='customer' else None,principal=principal)
    return TurnContext(request=ctx,turn_id=uuid4(),conversation_id=uuid4() if conversation else None)


def fixture(spec,task,inputs,ctx,history=None,language='English') -> AgentOutput:
    return AgentOutput(task_id=task.id,agent=spec.name,status='ok',origin='safety',
        result=AgentResult(task_id=task.id,agent=spec.name,status='ok',answer_kind='conversation',text=f'{spec.name}: {task.question}'))


def test_graph_nodes_and_edges():
    graph=build_graph().get_graph()
    assert set(graph.nodes)-{'__start__','__end__'}=={'planner','agent','gather','act','compose'}
    edges={(e.source,e.target) for e in graph.edges}
    assert {('__start__','planner'),('planner','agent'),('planner','act'),('planner','compose'),('agent','gather'),('gather','compose'),
        ('act','compose'),('compose','__end__')}<=edges


def test_after_planner_sends_one_agent_per_task_with_its_own_question():
    req=ChatRequest(messages=[{'role':'user','content':'balance and my review'}],mode='personal')
    tasks=[PlanTask(id='t1',agent='accounts',intent='account_balance',question='balance'),
        PlanTask(id='t2',agent='compliance',intent='review_status',question='my review')]
    sends=after_planner({'request':req,'history':req.messages,'tasks':tasks,'confirm':None})
    assert [(s.node,s.arg['task'].id,[m.content for m in s.arg['history']]) for s in sends]==[('agent','t1',['balance']),('agent','t2',['my review'])]
    assert after_planner({'request':req,'history':req.messages,'tasks':[],'confirm':None})=='compose'
    assert after_planner({'request':req,'history':req.messages,'tasks':tasks,'confirm':Confirmation(group=uuid4(),decision='confirm')})=='act'


def test_a_turn_streams_each_section_then_the_answer_in_priority_order(monkeypatch: pytest.MonkeyPatch):
    async def run(spec,task,inputs,ctx,history=None,language='English'):
        await asyncio.sleep(0.05 if task.agent=='security' else 0)  # the P0 task finishes last
        return fixture(spec,task,inputs,ctx,history,language)
    monkeypatch.setattr(runner,'run_agent',run)
    async def plan(history):
        return [PlanTask(id='t1',agent='accounts',intent='transfer_status',question='Is my transfer done?'),
            PlanTask(id='t2',agent='security',intent='login_help',question='I am locked out of the app')]
    monkeypatch.setattr(orchestrator,'model_plan',plan)
    req=ChatRequest(messages=[{'role':'user','content':'Is my transfer done? Also I am locked out of the app'}],mode='personal')
    async def main():
        return [item async for item in orchestrator.stream(req,assess_request(req),context(conversation=False))]
    items=asyncio.run(main())
    assert [(kind,getattr(item,'task_id',None)) for kind,item in items]==[('plan',None),('section','t1'),('section','t2'),('answer',None)]
    assert [(t.task_id,t.domain,t.priority) for t in items[0][1].tasks]==[('t2','fraud','P1'),('t1','retail','P3')]  # placeholders in priority order
    answer=items[-1][1]
    assert [(s.task_id,s.priority) for s in answer.sections]==[('t2','P1'),('t1','P3')] and answer.offer is None
    assert answer.answer.startswith('security: I am locked out of the app')


def test_guests_get_guide_only_and_a_planner_failure_asks_to_rephrase(monkeypatch: pytest.MonkeyPatch):
    seen=[]
    async def run(spec,task,*args,**kw):
        seen.append(spec.name);return fixture(spec,task,None,None)
    monkeypatch.setattr(runner,'run_agent',run)
    req=ChatRequest(messages=[{'role':'user','content':'Hello!'}])
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context('guest',False)))
    assert seen==['guide'] and answer.sections[0].domain=='smalltalk'
    async def fails(history):
        from app.services.llm_gateway import LLMError
        raise LLMError('provider unavailable')
    monkeypatch.setattr(orchestrator,'model_plan',fails)
    req=ChatRequest(messages=[{'role':'user','content':'Tell me about the thing with the stuff, and the other one'}],mode='personal')
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    assert answer.answer==orchestrator.REPHRASE and seen==['guide']


def test_a_yes_answers_the_open_offer_without_planning_or_agents(monkeypatch: pytest.MonkeyPatch):
    group=uuid4();calls=[]
    async def open_group(ctx,conversation):return (group,1)
    async def confirm(found,ctx):
        calls.append(('confirm',found));return []
    async def nothing(*a,**kw):raise AssertionError('no planner or agent on a confirmation turn')
    monkeypatch.setattr(offers,'open_group',open_group);monkeypatch.setattr(offers,'confirm',confirm)
    async def unchained(*a):return set()
    monkeypatch.setattr(offers,'chained',unchained)
    monkeypatch.setattr(orchestrator,'model_plan',nothing);monkeypatch.setattr(runner,'run_agent',nothing)
    req=ChatRequest(messages=[{'role':'user','content':'charges'},{'role':'assistant','content':'Would you like me to report it?'},
        {'role':'user','content':'yes'}],mode='personal')
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    assert calls==[('confirm',group)] and answer.answer==orchestrator.EXPIRED  # nothing left to run: already answered


def test_a_yes_with_no_open_offer_after_one_was_shown_says_it_expired(monkeypatch: pytest.MonkeyPatch):
    async def none(ctx,conversation):return None
    monkeypatch.setattr(offers,'open_group',none)
    req=ChatRequest(messages=[{'role':'user','content':'charges'},{'role':'assistant','content':'x '+orchestrator.CONFIRM_LINE},
        {'role':'user','content':'yes'}],mode='personal')
    turn=start(req,assess_request(req))
    reply=asyncio.run(orchestrator.confirmation(req,'yes',turn['history'],context()))
    assert reply=='expired'
    assert asyncio.run(orchestrator.confirmation(req,'yes',turn['history'],context(conversation=False))) is None
