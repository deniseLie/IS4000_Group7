"""Initial v2 LangGraph: plan, prioritise, dispatch and compose public information per turn.

Uses the current approved in-memory knowledge library. Durable conversations, hosted knowledge,
personal tools and confirmed actions require the subsequent service stages; none are simulated.
"""
import asyncio
import re
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from app.core.config import settings
from app.core.observability import span
from app.schemas.agents import Evidence, PlannedIssue, PlannerDraft
from app.schemas.chat import ChatRequest, Message
from app.schemas.contracts import AgentResult, Domain, RequestContext, RiskAssessment, Task, TaskPlan
from app.schemas.events import AnswerEvent, AnswerSection, Source
from app.services import llm_gateway, prompts
from app.services.agents.answer import WITHHELD, respond, response_prompt_id
from app.services.agents.registry import AGENT_FOR
from app.services.answers import NOTICE, clean_history, retrieve
from app.services.knowledge import source
from app.services.guardrail import FRAUD
from app.services.authorization import require_permission

MULTIPLE = re.compile(r'\b(?:and|also|plus|another|as well)\b|;|\?\s*\S|,\s+[A-Za-z]',re.I)
TOPICS: dict[Domain, re.Pattern[str]] = {
    'retail':re.compile(r'\b(?:accounts?|balances?|loans?|cards?|payments?|transfers?|deposits?|statements?|login|password)\b',re.I),
    'compliance':re.compile(r'\b(?:compliance|review|checklist|notice)\b',re.I),
    'document':re.compile(r'\b(?:documents?|docx|pdf|files?|upload|pages)\b',re.I),
    'relationship':re.compile(r'\b(?:relationship manager|advisor|adviser|meeting|appointment|handover)\b',re.I),
}


class Turn(TypedDict):
    request: ChatRequest
    context: RequestContext
    risk: RiskAssessment
    history: list[Message]
    issues: list[PlannedIssue]
    plan: TaskPlan
    results: list[AgentResult]
    evidence: list[list[Evidence]]
    answer: AnswerEvent


class PlanUpdate(TypedDict):
    issues: list[PlannedIssue]


class PriorityUpdate(TypedDict):
    plan: TaskPlan


class DispatchUpdate(TypedDict):
    results: list[AgentResult]
    evidence: list[list[Evidence]]


class ComposeUpdate(TypedDict):
    answer: AnswerEvent


def topic_domain(question: str) -> Domain | None:
    """Rules own clear domain assignments, including questions extracted by the planner."""
    if FRAUD.search(question):return 'fraud'
    domains = [domain for domain,pattern in TOPICS.items() if pattern.search(question)]
    return domains[0] if len(domains)==1 else None


def simple_issue(question: str, language: str) -> PlannedIssue | None:
    """Plan clear single-topic English questions by rules; ambiguity and multiple issues use the planner."""
    if language == 'English' and re.fullmatch(r'\s*(?:hi|hello|hey|thanks|thank you)[!.\s]*', question, re.I):
        return PlannedIssue(domain='smalltalk', question=question)
    if language != 'English' or MULTIPLE.search(question):return None
    domain = topic_domain(question)
    if domain is not None:return PlannedIssue(domain=domain,question=question)
    return None


def route_issues(issues: list[PlannedIssue]) -> list[PlannedIssue]:
    """Apply deterministic topic boundaries to planner questions without modifying the plan input."""
    return [PlannedIssue(domain=topic_domain(issue.question) or issue.domain,question=issue.question) for issue in issues]


async def plan(state: Turn) -> PlanUpdate:
    req = state['request']
    question = state['risk'].redacted_message
    single = simple_issue(question, req.language)
    if single is not None:return {'issues':[single]}
    pv = prompts.current('planner')
    schema = {'title':'issue_plan','type':'object','additionalProperties':False,'properties':{
        'issues':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{
            'domain':{'type':'string','enum':['retail','fraud','compliance','document','relationship','smalltalk']},
            'question':{'type':'string'}},'required':['domain','question']}}},'required':['issues']}
    result = await llm_gateway.call(pv, pv['system'], [(m.role,m.content) for m in state['history']], schema)
    draft = PlannerDraft.model_validate(result.data)
    issues = route_issues(draft.issues) if req.language=='English' else draft.issues
    if 'fraud_suspected' in state['risk'].risk_flags and not any(issue.domain=='fraud' for issue in issues):
        raise llm_gateway.LLMBadOutput('Planner omitted the guardrail fraud issue')
    return {'issues':issues}


def prioritise(state: Turn) -> PriorityUpdate:
    tasks = [Task(task_id=f't{i+1}', domain=issue.domain, intent='answer_question',
        priority='P0' if issue.domain=='fraud' else 'P3', entities={'question':issue.question})
        for i,issue in enumerate(state['issues'])]
    return {'plan':TaskPlan(tasks=tasks)}


def task_evidence(task: Task, history: list[Message], language: str) -> list[Evidence]:
    question = task.entities['question']
    if not isinstance(question, str):raise ValueError('Planned task question must be text')
    articles = retrieve(question, history, language)
    return [Evidence(id=a.id, text=a.content, publisher=a.publisher, scope=a.notice, source=Source.model_validate(source(a))) for a in articles]


async def run_task(task: Task, state: Turn, evidence: list[Evidence]) -> AgentResult:
    question = task.entities['question']
    if not isinstance(question, str):raise ValueError('Planned task question must be text')
    try:
        return await asyncio.wait_for(respond(task, question, state['history'], evidence, state['request'].language),
            timeout=settings.llm_timeout_s * (settings.llm_max_retries + 1) + 5)
    except (llm_gateway.LLMError, TimeoutError, ValidationError):
        # Only this failed section is withheld; the trace keeps the provider error.
        return AgentResult(task_id=task.task_id, agent=AGENT_FOR[task.domain], status='error', text=WITHHELD)


async def dispatch(state: Turn) -> DispatchUpdate:
    ctx = state['context']
    require_permission(ctx.principal,'kb.search',None,ctx)
    tasks = state['plan'].by_priority()
    evidence = [task_evidence(task, state['history'], state['request'].language) for task in tasks]
    results = await asyncio.gather(*(run_task(task,state,items) for task,items in zip(tasks,evidence,strict=True)))
    return {'results':list(results),'evidence':evidence}


def compose(state: Turn) -> ComposeUpdate:
    tasks = state['plan'].by_priority()
    sections: list[AnswerSection] = []
    for task,result,evidence in zip(tasks,state['results'],state['evidence'],strict=True):
        sources = {item.id:item.source for item in evidence}
        sections.append(AnswerSection(task_id=task.task_id, domain=task.domain, priority=task.priority,
            answer_kind=result.answer_kind, answer=result.text,
            sources=[sources[c.source_id] for c in result.citations],
            needs_human=result.status in ('error','needs_human'), response_origin='validation' if result.status=='error' else 'ai',
            prompt_version=response_prompt_id(AGENT_FOR[task.domain])))
    unique = {s.id:s for section in sections for s in section.sources}
    answer = AnswerEvent(answer='\n\n'.join(section.answer for section in sections), sources=list(unique.values()),
        privacy_warning=False, history_trimmed=len(state['history'])<len(state['request'].messages),
        disclaimer=NOTICE if any(section.answer_kind=='banking' for section in sections) else '',
        needs_human=any(section.needs_human for section in sections),
        source_check='rejected' if any(section.response_origin=='validation' for section in sections) else 'basic_checks_passed',
        prompt_version=None,
        response_origin='ai' if any(section.response_origin=='ai' for section in sections) else 'validation', sections=sections)
    return {'answer':answer}


def build_graph() -> CompiledStateGraph[Turn, None, Turn, Turn]:
    graph = StateGraph(Turn)
    graph.add_node('plan',plan)
    graph.add_node('prioritise',prioritise)
    graph.add_node('dispatch',dispatch)
    graph.add_node('compose',compose)
    graph.add_edge(START,'plan')
    graph.add_edge('plan','prioritise')
    graph.add_edge('prioritise','dispatch')
    graph.add_edge('dispatch','compose')
    graph.add_edge('compose',END)
    return graph.compile()


async def answer_public(req: ChatRequest, risk: RiskAssessment, ctx: RequestContext) -> AnswerEvent:
    if req.mode != 'public':raise ValueError('Initial v2 pipeline supports public mode only')
    if risk.fixed_response is not None:raise ValueError('Fixed safety responses must use the no-model pipeline')
    with span('route','agent',route='llm',pipeline='v2'):
        result = await build_graph().ainvoke({'request':req,'risk':risk,'history':clean_history(req.messages),'context':ctx})
    return result['answer']
