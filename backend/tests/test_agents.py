"""Direct validation and composition checks; live checks call the real provider only when enabled."""
import asyncio
import json
import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.core.config import Settings, settings
from app.main import app
from app.schemas.agents import Evidence, PlannedIssue, PlannerDraft
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentOutput,AgentResult,PlanTask,Principal,RequestContext, Task
from app.schemas.events import Source
from app.services.agents.answer import respond, validate_draft
from app.services.guardrail import assess_request
from app.services.knowledge import INDEX, source
from app.services import orchestrator
from app.services.orchestrator import answer_for, checked, overridden, priority, section_for, simple_task


def evidence():
    article=INDEX['sg-accounts']
    return [Evidence(id=article.id,text=article.content,publisher=article.publisher,scope=article.notice,
        source=Source.model_validate(source(article)))]


def task():
    return Task(task_id='t1',domain='retail',intent='answer_question',entities={'question':'What happens on early withdrawal?'})


def draft(answer,ids,kind,status,question):
    return json.dumps({'answer':answer,'source_ids':ids,'answer_kind':kind,'status':status,'clarification_question':question})


def test_agent_rejects_foreign_sources_and_unsupported_figures():
    for raw in [draft('Contact support.',['security'],'banking','ok',''),
        draft('The fee is SGD 199.',['sg-accounts'],'banking','ok',''),
        draft('I have refunded your payment.',['sg-accounts'],'banking','ok',''),
        'not json']:
        result=validate_draft(task(),raw,evidence())
        assert result.status=='error' and result.citations==[] and result.receipts==[]


def test_agent_accepts_supported_guidance_and_valid_clarification():
    result=validate_draft(task(),draft('Early withdrawal may reduce or remove interest.',['sg-accounts'],'banking','ok',''),evidence())
    assert result.status=='ok' and result.citations[0].source_id=='sg-accounts'
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


def test_planning_prioritises_fraud_without_modifying_input():
    tasks=[planned('accounts','Explain my loan.','loans'),planned('security','Unfamiliar charge','unrecognised_charge','t2')]
    before=[item.model_dump() for item in tasks]
    plan=checked(overridden(tasks,assess_request(PERSONAL)),PERSONAL)
    assert [item.agent for item in sorted(plan,key=lambda t:t.priority)]==['security','accounts']
    assert [item.model_dump() for item in tasks]==before
    assert simple_task('Hello!','English').agent=='guide'
    assert simple_task('What happens if I withdraw my fixed deposit early?','English').agent=='accounts'
    assert simple_task('I cannot log in.','English').agent=='security'
    assert simple_task('There is an unfamiliar card charge. Also, explain loans.','English') is None
    # Ordinary words never override the planner: a loan comparison it gave to Compliance stays there.
    mistaken=[planned('compliance','What is the difference between secured and unsecured loans?','guidance')]
    assert overridden(mistaken,assess_request(PERSONAL))[0].agent=='compliance' and mistaken[0].agent=='compliance'


def test_security_deadline_order_and_guardrail_override_preserve_every_issue():
    tasks=[planned('accounts','Explain account fees.','card_fees'),planned('compliance','When is my review due?','review_status','t2'),
        planned('accounts','I cannot log in.','open','t3'),planned('compliance','When is the document deadline?','document_findings','t4'),
        planned('accounts','Explain deposit interest.','product_info','t5')]
    request=ChatRequest(messages=[{'role':'user','content':'There is an unfamiliar card charge.'}],mode='personal')
    before=[item.model_dump() for item in tasks]
    plan=sorted(checked(overridden(tasks,assess_request(request)),request),key=lambda t:t.priority)
    assert len(plan)==5 and [t.priority for t in plan]==['P0','P1','P2','P2','P3']  # at most five tasks: the last ordinary one is dropped
    assert plan[0].agent=='security' and plan[0].intent=='unrecognised_charge' and plan[0].question==request.messages[-1].content
    assert plan[1].agent=='security' and plan[1].intent=='login_help'
    assert plan[0].entities['lockout'] is True and plan[0].entities['message']==request.messages[-1].content
    assert [t.model_dump() for t in tasks]==before
    assert priority(planned('accounts','When is my loan payment due?','loans'))=='P3'
    for question in ['Explain compliance due diligence.','Is my review due to a transfer?']:
        assert priority(planned('compliance',question,'review_status'))=='P3'


def test_guests_get_only_guide_and_unknown_intents_are_checked():
    guest=ChatRequest(messages=[{'role':'user','content':'What is my balance?'}])
    plan=checked([planned('accounts','What is my balance?','account_balance')],guest)
    assert [(t.agent,t.intent) for t in plan]==[('guide','general')]
    plan=checked([planned('accounts','x','made_up'),planned('security','y','made_up','t2'),planned('accounts','z','transactions','t3')],PERSONAL)
    assert [(t.agent,t.intent) for t in plan]==[('accounts','open'),('security','unrecognised_charge'),('accounts','transactions')]
    days=checked([PlanTask(id='t1',agent='accounts',intent='transactions',question='q',entities={'days':'31','status':'pending'}),
        PlanTask(id='t2',agent='accounts',intent='transactions',question='q',entities={'days':'400'})],PERSONAL)
    assert days[0].entities=={'days':31,'status':'pending'} and days[1].entities=={}


def test_one_withheld_section_does_not_discard_other_sections():
    req=ChatRequest(messages=[{'role':'user','content':'Explain early withdrawal and check my document'}])
    valid=validate_draft(task(),draft('Early withdrawal may reduce interest.',['sg-accounts'],'banking','ok',''),evidence())
    rows=[(planned('guide','Explain early withdrawal','general'),AgentOutput(task_id='t1',agent='guide',status='ok',result=valid,
            evidence=[e.model_dump(mode='json') for e in evidence()],prompt_id='v1+test')),
        (planned('compliance','check my document','document_findings','t2'),AgentOutput(task_id='t2',agent='compliance',status='error',origin='validation',
            result=AgentResult(task_id='t2',agent='document',status='error',text='This part could not be verified.')))]
    answer=answer_for([section_for(t,o) for t,o in rows],{'history':req.messages,'history_count':1})
    assert answer.sections[0].response_origin=='ai' and answer.sections[0].sources[0].id=='sg-accounts'
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
    monkeypatch.setattr(settings,'knowledge_store','memory')
    req=ChatRequest(messages=[{'role':'user','content':'Hello'}])
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),orchestrator.TurnContext(request=ctx)))
    assert answer.response_origin=='validation' and answer.sections[0].response_origin=='validation' and answer.sections[0].domain=='retail'


@pytest.mark.skipif(os.getenv('TEST_AGENTS_LIVE')!='1',reason='Opt in to real provider calls with TEST_AGENTS_LIVE=1')
def test_real_domain_agents():
    # Keep one event loop for the real provider's pooled asynchronous HTTP clients.
    from app.services.answers import retrieve
    def task_evidence(planned,history,language):
        return [Evidence(id=a.id,text=a.content,publisher=a.publisher,scope=a.notice,source=Source.model_validate(source(a)))
            for a in retrieve(planned.entities['question'],history,language)]
    async def run():
        for domain,question in [
            ('retail','What happens if I withdraw my fixed deposit early?'),
            ('fraud','There is an unfamiliar charge on my card.'),
            ('compliance','Can you show my compliance review status?'),
            ('document','Can you check the pages in my document?'),
            ('relationship','Who is my relationship manager?'),
        ]:
            planned=Task(task_id='live1',domain=domain,intent='answer_question',entities={'question':question})
            req=ChatRequest(messages=[{'role':'user','content':question}])
            result=await respond(planned,question,req.messages,task_evidence(planned,req.messages,'English'),'English')
            assert result.status!='error',domain
            assert result.pending_action is None and result.receipts==[]
    asyncio.run(run())
