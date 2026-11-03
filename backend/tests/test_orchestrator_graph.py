"""Graph wiring: pending replies skip planning, guests and plain answers skip the action nodes."""
from uuid import uuid4
import pytest
from langgraph.runtime import Runtime
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentResult,Principal,RequestContext,Task,TaskPlan
from app.schemas.fraud import FraudProposal
from app.schemas.workflows import PipelineContext,PendingReview,ResponseMeta
from app.services import orchestrator
from app.services.guardrail import assess_request
from app.services.orchestrator import after_dispatch,after_proposals,after_resume,build_graph,resume_pending


def turn(text: str,mode: str='personal') -> dict:
    req=ChatRequest(messages=[{'role':'assistant','content':'Earlier reply.'},{'role':'user','content':text}],mode=mode)
    return {'request':req,'history':req.messages,'risk':assess_request(req)}


def runtime(kind: str='customer',case: bool=True,owned: bool=False) -> Runtime[PipelineContext]:
    principal=Principal(kind=kind,user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token') if kind=='customer' else Principal(kind='guest')
    ctx=RequestContext(session_id=uuid4(),case_id=uuid4() if case else None,principal=principal)
    return Runtime(context=PipelineContext.model_construct(request=ctx,workflow=object() if owned else None))


def dispatched(*tasks: Task,proposal: bool=False,status: str='ok',origin: str='ai') -> dict:
    found=FraudProposal(card_id='card_10021',transaction_id='txn_100215') if proposal else None
    return {'plan':TaskPlan(tasks=list(tasks)),'proposals':[found]+[None]*(len(tasks)-1),
        'results':[AgentResult(task_id=t.task_id,agent='retail',status=status,text='x') for t in tasks],
        'metadata':[ResponseMeta(origin=origin,prompt_id=None) for _ in tasks]}


TASK=Task(task_id='t1',domain='retail',intent='answer_question',entities={'question':'What is my balance?'})
HANDOVER=TASK.model_copy(update={'entities':{'question':'Can my RM waive the fee?','handover':'rm'}})


def test_graph_nodes_and_branches():
    graph=build_graph(None).get_graph()
    assert set(graph.nodes)-{'__start__','__end__'}=={'resume_pending','plan','prioritise','dispatch','propose_actions','review_actions','act','compose'}
    edges={(e.source,e.target) for e in graph.edges}
    assert {('__start__','resume_pending'),('resume_pending','plan'),('resume_pending','prioritise'),('dispatch','compose'),
        ('dispatch','act'),('dispatch','propose_actions'),('propose_actions','review_actions'),('review_actions','act'),('act','compose')}<=edges


def test_new_message_is_planned_and_clears_last_turn():
    update=resume_pending(turn('What is my balance?'))
    assert update=={'issues':[],'pending_reviews':[],'decision_sections':[]} and after_resume(update)=='plan'


def test_reply_to_our_question_skips_planning(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(orchestrator.meeting_answers,'awaiting',lambda history:True)
    update=resume_pending(turn('Tuesday 10am please'))
    assert [(i.domain,i.question) for i in update['issues']]==[('relationship','Tuesday 10am please')] and after_resume(update)=='prioritise'
    # Guests have no pending flows; a fresh request still wins over an older pending question.
    assert resume_pending(turn('Tuesday 10am please','public'))['issues']==[]
    assert resume_pending(turn('Raise a new request for a statement copy'))['issues']==[]


def test_guest_and_plain_answers_skip_the_action_nodes():
    assert after_dispatch(dispatched(TASK),runtime('guest',case=False))=='compose'
    assert after_dispatch(dispatched(HANDOVER),runtime('guest',case=False))=='compose'
    assert after_dispatch(dispatched(TASK),runtime(owned=True))=='compose'
    assert after_dispatch(dispatched(TASK,status='error',origin='validation'),runtime(owned=True))=='compose'


def test_handover_and_fraud_reach_act(monkeypatch: pytest.MonkeyPatch):
    assert after_dispatch(dispatched(HANDOVER),runtime())=='act'
    assert after_dispatch(dispatched(TASK,proposal=True),runtime(owned=True))=='propose_actions'
    assert after_proposals({'pending_reviews':[]})=='act'
    assert after_proposals({'pending_reviews':[PendingReview(task_id='t1',command_id=uuid4())]})=='review_actions'
    monkeypatch.setenv('VALIDATION_HANDOFF','1')
    assert after_dispatch(dispatched(TASK,status='error',origin='validation'),runtime())=='act'
