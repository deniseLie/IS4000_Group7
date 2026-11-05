"""v5 graph wiring: planner → agent (Send ×N) → gather → compose, and planner → act → compose for an answer to an open offer.
Agent outputs here are fixed safety-origin fixtures for the graph mechanics; no model answer is simulated."""
import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentOutput,AgentResult,PlanTask,Principal,RequestContext
from app.services import offers,orchestrator,rm_handover
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
    """Build plan 2b: the planner's charge question becomes the urgent card task, answered by code (fixed steps, no model)
    with one urgent RM request offer; it is P0, so it leads the reply although it finishes last."""
    async def run(*args,**kw):return fixture(*args,**kw)
    monkeypatch.setattr(runner,'run_agent',run)
    async def cards(ctx):
        await asyncio.sleep(0.05)  # the P0 task finishes last
        return 'card ending 7310 (active)'
    monkeypatch.setattr(rm_handover,'urgent_cards',cards)
    saved=[]
    async def save(proposals,ctx,turn):
        saved.extend(proposals)
        return [SimpleNamespace(offer_id=uuid4(),type=p.type,summary=p.summary,expires_at=datetime.now(timezone.utc)+timedelta(minutes=30)) for p in proposals]
    monkeypatch.setattr(offers,'save',save)
    async def plan(history):
        return [PlanTask(id='t1',agent='accounts',intent='transfer_status',question='Is my transfer done?'),
            PlanTask(id='t2',agent='accounts',intent='transactions',question="There's a charge I don't recognise")]
    monkeypatch.setattr(orchestrator,'model_plan',plan)
    req=ChatRequest(messages=[{'role':'user','content':"Is my transfer done? Also there's a charge I don't recognise"}],mode='personal')
    async def main():
        return [item async for item in orchestrator.stream(req,assess_request(req),context(conversation=False))]
    items=asyncio.run(main())
    assert [(kind,getattr(item,'task_id',None)) for kind,item in items]==[('plan',None),('section','t1'),('section','t2'),('answer',None)]
    assert [(t.task_id,t.domain,t.priority) for t in items[0][1].tasks]==[('t2','fraud','P0'),('t1','retail','P3')]  # placeholders in priority order
    answer=items[-1][1]
    assert [(s.task_id,s.priority,s.response_origin) for s in answer.sections]==[('t2','P0','safety'),('t1','P3','safety')]
    assert answer.answer.startswith(rm_handover.URGENT_STEPS) and rm_handover.URGENT_OFFER['charge'] in answer.answer
    assert len(answer.offer.offers)==1 and 'kb:unrecognised-charge@v3#urgent-steps' in [s.id for s in answer.sources]
    [proposal]=saved
    assert (proposal.agent,proposal.type,proposal.target['reason'],proposal.target['priority'])==('relationship','handoff.create','general_support','P0')
    assert proposal.target['summary'].startswith('[Urgent card] Charge the client does not recognise. Cards on record: card ending 7310 (active).')


def test_a_guest_gets_the_fixed_urgent_steps_without_a_read_an_offer_or_a_model(monkeypatch: pytest.MonkeyPatch):
    async def nothing(*a,**kw):raise AssertionError('no model, read or offer for a guest urgent card task')
    for target,name in ((runner,'run_agent'),(orchestrator,'model_plan'),(rm_handover,'urgent_cards'),(rm_handover,'open_urgent'),(offers,'save')):
        monkeypatch.setattr(target,name,nothing)
    req=ChatRequest(messages=[{'role':'user','content':'that charge is not mine'}])
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context('guest',False)))
    assert answer.answer==rm_handover.URGENT_STEPS+' '+rm_handover.URGENT_GUEST and answer.offer is None
    assert [(s.domain,s.priority,s.response_origin) for s in answer.sections]==[('fraud','P0','safety')]


def test_a_lockout_is_answered_by_guide(monkeypatch: pytest.MonkeyPatch):
    seen=[]
    async def run(spec,task,*args,**kw):
        seen.append((spec.name,task.intent,task.priority));return fixture(spec,task,None,None)
    monkeypatch.setattr(runner,'run_agent',run)
    req=ChatRequest(messages=[{'role':'user','content':"I'm locked out of the app"}],mode='personal')
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    assert seen==[('guide','login_help','P3')] and answer.sections[0].priority=='P3' and answer.offer is None


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
    async def open_groups(ctx,conversation):return {group:[]}
    async def confirm(found,ctx,include=None):
        calls.append(('confirm',found));return []
    async def nothing(*a,**kw):raise AssertionError('no planner or agent on a confirmation turn')
    monkeypatch.setattr(offers,'open_groups',open_groups);monkeypatch.setattr(offers,'confirm',confirm)
    async def unchained(*a):return set()
    monkeypatch.setattr(offers,'chained',unchained)
    monkeypatch.setattr(orchestrator,'model_plan',nothing);monkeypatch.setattr(runner,'run_agent',nothing)
    req=ChatRequest(messages=[{'role':'user','content':'charges'},{'role':'assistant','content':'Would you like me to report it?'},
        {'role':'user','content':'yes'}],mode='personal')
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    assert calls==[('confirm',group)] and answer.answer==orchestrator.EXPIRED  # nothing left to run: already answered


def test_a_yes_with_no_open_offer_after_one_was_shown_says_it_expired(monkeypatch: pytest.MonkeyPatch):
    async def none(ctx,conversation):return {}
    monkeypatch.setattr(offers,'open_groups',none)
    req=ChatRequest(messages=[{'role':'user','content':'charges'},{'role':'assistant','content':'x '+orchestrator.CONFIRM_LINE},
        {'role':'user','content':'yes'}],mode='personal')
    turn=start(req,assess_request(req))
    reply=asyncio.run(orchestrator.confirmation(req,'yes',turn['history'],context()))
    assert reply=='expired'
    assert asyncio.run(orchestrator.confirmation(req,'yes',turn['history'],context(conversation=False))) is None
