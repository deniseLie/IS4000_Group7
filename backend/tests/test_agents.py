"""Direct validation and composition checks; live checks call the real provider only when enabled."""
import asyncio
import json
import os
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.core.config import Settings, settings
from app.main import app
from app.schemas.agents import Evidence, PlannedIssue, PlannerDraft
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentResult, Task, TaskPlan
from app.schemas.events import Source
from app.services.agents.answer import respond, validate_draft
from app.services.guardrail import assess_request
from app.services.knowledge import INDEX, source
from app.services.orchestrator import compose, prioritise, route_issues, run_task, simple_issue


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


def test_planning_prioritises_fraud_without_modifying_input():
    issues=[PlannedIssue(domain='retail',question='Explain my loan.'),PlannedIssue(domain='fraud',question='Unfamiliar charge')]
    before=[issue.model_dump() for issue in issues]
    plan=prioritise({'issues':issues})['plan']
    assert [item.domain for item in plan.by_priority()]==['fraud','retail']
    assert [issue.model_dump() for issue in issues]==before
    with pytest.raises(ValidationError):PlannerDraft(issues=[])
    with pytest.raises(ValidationError):PlannerDraft(issues=issues*3)
    assert simple_issue('Hello!','English').domain=='smalltalk'
    assert simple_issue('What happens if I withdraw my fixed deposit early?','English').domain=='retail'
    assert simple_issue('There is an unfamiliar card charge. Also, explain loans.','English') is None
    mistaken=[PlannedIssue(domain='compliance',question='What is the difference between secured and unsecured loans?')]
    assert route_issues(mistaken)[0].domain=='retail' and mistaken[0].domain=='compliance'


def test_one_withheld_section_does_not_discard_other_sections():
    req=ChatRequest(messages=[{'role':'user','content':'Explain early withdrawal and check my document'}])
    tasks=[task(),Task(task_id='t2',domain='document',intent='check_document')]
    valid=validate_draft(task(),draft('Early withdrawal may reduce interest.',['sg-accounts'],'banking','ok',''),evidence())
    invalid=AgentResult(task_id='t2',agent='document',status='error',text='This part could not be verified.')
    state={'request':req,'history':req.messages,'history_count':len(req.messages),'plan':TaskPlan(tasks=tasks),'results':[valid,invalid],'evidence':[evidence(),[]]}
    answer=compose(state)['answer']
    assert answer.sections[0].response_origin=='ai' and answer.sections[0].sources[0].id=='sg-accounts'
    assert answer.sections[1].response_origin=='validation' and answer.needs_human
    assert answer.prompt_version is None and answer.sections[0].prompt_version.startswith('v1+')


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
    planned=Task(task_id='t1',domain='smalltalk',intent='answer_question',entities={'question':'Hello'})
    result=asyncio.run(run_task(planned,{'history':req.messages,'request':req},[]))
    assert result.status=='error' and result.agent=='retail'
    answer=compose({'request':req,'history':req.messages,'history_count':len(req.messages),'plan':TaskPlan(tasks=[planned]),'results':[result],'evidence':[[]]})['answer']
    assert answer.response_origin=='validation' and answer.sections[0].response_origin=='validation'


@pytest.mark.skipif(os.getenv('TEST_AGENTS_LIVE')!='1',reason='Opt in to real provider calls with TEST_AGENTS_LIVE=1')
def test_real_domain_agents():
    # Keep one event loop for the real provider's pooled asynchronous HTTP clients.
    from app.services.orchestrator import task_evidence
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
