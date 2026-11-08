"""Direct validation and composition checks; live checks call the real provider only when enabled."""
import asyncio
import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.core.config import Settings, settings
from app.main import app
from app.schemas.agents import PlannedIssue, PlannerDraft
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentOutput,AgentResult,PlanTask,Principal,RequestContext, Task
from app.schemas.events import Source
from app.services.agents.answer import validate_draft
from app.services.guardrail import assess_request
from tests import knowledge_fixtures
from tests.knowledge_fixtures import cite
from app.services import orchestrator
from app.services.orchestrator import answer_for, checked, overridden, priority, section_for, simple_task


def evidence():
    return knowledge_fixtures.evidence('sg-accounts')


def task():
    return Task(task_id='t1',domain='retail',intent='answer_question',entities={'question':'What happens on early withdrawal?'})


def draft(answer,ids,kind,status,question):
    return json.dumps({'answer':answer,'source_ids':ids,'answer_kind':kind,'status':status,'clarification_question':question})


def test_agent_rejects_foreign_sources_and_unsupported_figures():
    for raw in [draft('Contact support.',['security'],'banking','ok',''),
        draft('The fee is SGD 199.',[cite('sg-accounts')],'banking','ok',''),
        draft('I have refunded your payment.',[cite('sg-accounts')],'banking','ok',''),
        'not json']:
        result=validate_draft(task(),raw,evidence())
        assert result.status=='error' and result.citations==[] and result.receipts==[]


def test_agent_accepts_supported_guidance_and_valid_clarification():
    result=validate_draft(task(),draft('Early withdrawal may reduce or remove interest.',[cite('sg-accounts')],'banking','ok',''),evidence())
    assert result.status=='ok' and result.citations[0].source_id==cite('sg-accounts')
    question='Which account do you mean?'
    result=validate_draft(task(),draft(question,[],'conversation','needs_clarification',question),evidence())
    assert result.status=='needs_clarification' and result.question==question


def test_inconsistent_clarification_is_withheld():
    result=validate_draft(task(),draft('Which account?',[],'conversation','needs_clarification',''),evidence())
    assert result.status=='error'
    with pytest.raises(ValidationError):AgentResult(task_id='t1',agent='retail',status='needs_clarification')


PERSONAL=ChatRequest(messages=[{'role':'user','content':'Explain my loan.'}],mode='personal')


def planned(agent,question,intent='open',id_='t1'):
    return PlanTask(id=id_,agent=agent,intent=intent,question=question)


def test_planning_prioritises_the_urgent_card_task_without_modifying_input():
    tasks=[planned('accounts','Explain my loan.','loans'),planned('accounts','Unfamiliar charge','transactions','t2')]
    before=[item.model_dump() for item in tasks]
    plan=checked(overridden(tasks,assess_request(PERSONAL)),PERSONAL)
    assert [(item.agent,item.priority) for item in sorted(plan,key=lambda t:t.priority)]==[('relationship','P0'),('accounts','P3')]
    assert [item.model_dump() for item in tasks]==before
    assert simple_task('Hello!','English').agent=='guide'
    assert simple_task('What happens if I withdraw my fixed deposit early?','English').agent=='accounts'
    assert (simple_task('I cannot log in.','English').agent,simple_task('I cannot log in.','English').intent)==('guide','login_help')
    assert simple_task('There is an unfamiliar card charge. Also, explain loans.','English') is None
    # Ordinary words never override the planner: a loan comparison it gave to Compliance stays there.
    mistaken=[planned('compliance','What is the difference between secured and unsecured loans?','guidance')]
    assert overridden(mistaken,assess_request(PERSONAL))[0].agent=='compliance' and mistaken[0].agent=='compliance'


def test_urgent_deadline_order_and_guardrail_override_preserve_every_issue():
    tasks=[planned('accounts','Explain account fees.','card_fees'),planned('compliance','When is my review due?','review_status','t2'),
        planned('accounts','I cannot log in.','open','t3'),planned('compliance','When is the document deadline?','document_findings','t4'),
        planned('accounts','Explain deposit interest.','product_info','t5')]
    request=ChatRequest(messages=[{'role':'user','content':'There is an unfamiliar card charge.'}],mode='personal')
    before=[item.model_dump() for item in tasks]
    plan=sorted(checked(overridden(tasks,assess_request(request)),request),key=lambda t:t.priority)
    assert len(plan)==5 and [t.priority for t in plan]==['P0','P2','P2','P3','P3']  # at most five tasks: the last ordinary one is dropped
    message=request.messages[-1].content
    assert plan[0].agent=='relationship' and plan[0].question==message and plan[0].entities=={'urgent':True,'message':message}
    assert (plan[4].agent,plan[4].intent)==('guide','login_help')  # build plan 2b: sign-in help is Guide's, no longer P1
    assert [t.model_dump() for t in tasks]==before
    assert priority(planned('accounts','When is my loan payment due?','loans'))=='P3'
    for question in ['Explain compliance due diligence.','Is my review due to a transfer?']:
        assert priority(planned('compliance',question,'review_status'))=='P3'


def test_guests_get_only_guide_and_unknown_intents_are_checked():
    guest=ChatRequest(messages=[{'role':'user','content':'What is my balance?'}])
    plan=checked([planned('accounts','What is my balance?','account_balance')],guest)
    assert [(t.agent,t.intent) for t in plan]==[('guide','general')]
    plan=checked([planned('accounts','x','made_up'),planned('compliance','y','made_up','t2'),planned('accounts','z','transactions','t3')],PERSONAL)
    assert [(t.agent,t.intent) for t in plan]==[('accounts','open'),('compliance','open'),('accounts','transactions')]
    # A guest's urgent card task keeps only its marker, so she gets the fixed steps (no offer, no reads).
    urgent=checked([PlanTask(id='t1',agent='relationship',intent='open',question='not mine',priority='P0',entities={'urgent':True,'message':'x'})],guest)
    assert [(t.agent,t.priority,t.entities) for t in urgent]==[('guide','P0',{'urgent':True})]
    days=checked([PlanTask(id='t1',agent='accounts',intent='transactions',question='q',entities={'days':'31','status':'pending'}),
        PlanTask(id='t2',agent='accounts',intent='transactions',question='q',entities={'days':'400'})],PERSONAL)
    assert days[0].entities=={'days':31,'status':'pending'} and days[1].entities=={}


def test_one_withheld_section_does_not_discard_other_sections():
    req=ChatRequest(messages=[{'role':'user','content':'Explain early withdrawal and check my document'}])
    valid=validate_draft(task(),draft('Early withdrawal may reduce interest.',[cite('sg-accounts')],'banking','ok',''),evidence())
    rows=[(planned('guide','Explain early withdrawal','general'),AgentOutput(task_id='t1',agent='guide',status='ok',result=valid,
            evidence=[e.model_dump(mode='json') for e in evidence()],prompt_id='v1+test')),
        (planned('compliance','check my document','document_findings','t2'),AgentOutput(task_id='t2',agent='compliance',status='error',origin='validation',
            result=AgentResult(task_id='t2',agent='document',status='error',text='This part could not be verified.')))]
    answer=answer_for([section_for(t,o) for t,o in rows],{'history':req.messages,'history_count':1})
    assert answer.sections[0].response_origin=='ai' and answer.sections[0].sources[0].id==cite('sg-accounts')
    assert answer.sections[1].response_origin=='validation' and answer.needs_human and answer.source_check=='rejected'
    assert answer.prompt_version is None and answer.sections[0].prompt_version=='v1+test'


def test_v2_safety_requests_remain_fixed_without_a_provider(monkeypatch):
    monkeypatch.setattr(settings,'pipeline','v2')
    monkeypatch.delenv('LLM_MODEL',raising=False)
    response=TestClient(app).post('/chat',json={'messages':[{'role':'user','content':'I lost my card'}]})
    assert 'event: answer' in response.text and '"response_origin": "safety"' in response.text
    assert '"sections"' not in response.text
    with pytest.raises(ValidationError):Settings(pipeline='invalid')


def test_provider_configuration_failure_withholds_a_social_section(monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    req=ChatRequest(messages=[{'role':'user','content':'Hello'}])
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),orchestrator.TurnContext(request=ctx)))
    assert answer.response_origin=='validation' and answer.sections[0].response_origin=='validation' and answer.sections[0].domain=='retail'
