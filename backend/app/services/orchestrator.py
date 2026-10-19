"""V2 LangGraph: public information and the first authenticated fraud review workflow.

Uses the explicitly configured knowledge store and an optional owned durable checkpoint saver.
Fraud proposals pause at a durable checkpoint; other personal agent services remain explicit limitations.
"""
import asyncio
import re
from typing import TypedDict
from uuid import uuid5
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError
from app.core.config import settings
from app.core import data_api
from app.core.observability import span
from app.schemas.agents import Evidence, PlannedIssue, PlannerDraft
from app.schemas.chat import ChatRequest, Message
from app.schemas.contracts import ActionCommand,AgentResult, Domain, RequestContext, RiskAssessment, Task, TaskPlan
from app.schemas.events import ActionRequiredEvent,AnswerEvent, AnswerSection, Source,WorkflowPausedEvent
from app.schemas.fraud import FraudProposal
from app.schemas.workflows import OwnedWorkflow,PendingReview,PipelineContext,ResponseMeta
from app.schemas.knowledge import KnowledgeQuery
from app.services import actions,kb, llm_gateway, prompts,workflow_store
from app.services.actions import store as action_store
from app.services.agents import fraud
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
    history_count: int
    risk: RiskAssessment
    history: list[Message]
    issues: list[PlannedIssue]
    plan: TaskPlan
    results: list[AgentResult]
    evidence: list[list[Evidence]]
    metadata: list[ResponseMeta]
    proposals: list[FraudProposal | None]
    pending_reviews: list[PendingReview]
    decision_sections: list[AnswerSection]
    workflow_version: str
    answer: AnswerEvent


class PlanUpdate(TypedDict):
    issues: list[PlannedIssue]


class PriorityUpdate(TypedDict):
    plan: TaskPlan


class DispatchUpdate(TypedDict):
    results: list[AgentResult]
    evidence: list[list[Evidence]]
    metadata: list[ResponseMeta]
    proposals: list[FraudProposal | None]


class ReviewUpdate(TypedDict):
    pending_reviews: list[PendingReview]


class SettlementUpdate(TypedDict):
    decision_sections: list[AnswerSection]


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


async def database_evidence(task: Task,ctx: RequestContext) -> list[Evidence]:
    """Retrieve only the latest planned question; never replace a no-match with unrelated articles."""
    query=KnowledgeQuery(query=task.entities['question'],scope='public',k=4)
    # The supervisor retrieves shared evidence; this is not an agent-initiated tool call.
    with data_api.connect(ctx) as db:sections=await kb.search(query,ctx,db)
    return [Evidence(id=item.cite,text=item.body,publisher=item.publisher,scope=item.scope,source=item.source()) for item in sections]


async def run_task(task: Task, state: Turn, evidence: list[Evidence]) -> AgentResult:
    question = task.entities['question']
    if not isinstance(question, str):raise ValueError('Planned task question must be text')
    try:
        return await asyncio.wait_for(respond(task, question, state['history'], evidence, state['request'].language),
            timeout=settings.llm_timeout_s * (settings.llm_max_retries + 1) + 5)
    except (llm_gateway.LLMError, TimeoutError, ValidationError):
        # Only this failed section is withheld; the trace keeps the provider error.
        return AgentResult(task_id=task.task_id, agent=AGENT_FOR[task.domain], status='error', text=WITHHELD)


async def dispatch(state: Turn, runtime: Runtime[PipelineContext]) -> DispatchUpdate:
    ctx = runtime.context.request
    require_permission(ctx.principal,'kb.search',None,ctx)
    tasks = state['plan'].by_priority()
    if settings.knowledge_store=='database':
        evidence=[await database_evidence(task,ctx) for task in tasks]
    else:evidence = [task_evidence(task, state['history'], state['request'].language) for task in tasks]
    if state['request'].mode=='public':
        results = await asyncio.gather(*(run_task(task,state,items) for task,items in zip(tasks,evidence,strict=True)))
        return {'results':list(results),'evidence':evidence,'proposals':[None for task in tasks],
            'metadata':[ResponseMeta(origin='validation' if result.status=='error' else 'ai',prompt_id=response_prompt_id(AGENT_FOR[task.domain]))
                for task,result in zip(tasks,results,strict=True)]}
    results: list[AgentResult]=[]
    proposals: list[FraudProposal | None]=[]
    metadata: list[ResponseMeta]=[]
    for index,task in enumerate(tasks):
        if task.domain=='fraud':
            try:
                decision,items=await fraud.respond(task,state['history'],state['request'].language,
                    ctx.model_copy(update={'agent':'fraud'}),evidence[index])
                evidence[index]=items
                results.append(decision.result)
                proposals.append(decision.proposal)
                metadata.append(ResponseMeta(origin='validation' if decision.result.status=='error' else 'ai',prompt_id=decision.prompt_id))
            except (llm_gateway.LLMError,ValidationError,TimeoutError):
                results.append(AgentResult(task_id=task.task_id,agent='fraud',status='error',text=WITHHELD))
                proposals.append(None)
                metadata.append(ResponseMeta(origin='validation',prompt_id=prompts.load('fraud','v2')['id']))
        elif task.domain=='smalltalk':
            result=await run_task(task,state,evidence[index])
            results.append(result);proposals.append(None)
            metadata.append(ResponseMeta(origin='validation' if result.status=='error' else 'ai',prompt_id=response_prompt_id('retail')))
        else:
            results.append(AgentResult(task_id=task.task_id,agent=AGENT_FOR[task.domain],status='error',
                text='This personal service is not connected yet. Use General help for public information or your bank’s official support channel.'))
            proposals.append(None);metadata.append(ResponseMeta(origin='validation',prompt_id=None))
    return {'results':results,'evidence':evidence,'metadata':metadata,'proposals':proposals}


async def propose_actions(state: Turn,runtime: Runtime[PipelineContext]) -> ReviewUpdate:
    owned=runtime.context.workflow
    reviews: list[PendingReview]=[]
    cards: set[str]=set()
    for task,proposal in zip(state['plan'].by_priority(),state['proposals'],strict=True):
        if proposal is None or proposal.card_id in cards:continue
        if owned is None:raise ValueError('Personal proposals require an owned durable turn')
        ctx=runtime.context.request.model_copy(update={'agent':'fraud'})
        command=ActionCommand(command_id=uuid5(owned.turn.case_id,task.task_id+':card.temporary_block'),type='card.temporary_block',
            target={'card_id':proposal.card_id},idempotency_key=f'{owned.turn.case_id}:{task.task_id}:card.temporary_block')
        result=await actions.propose_for_workflow(command,task.task_id,owned,ctx)
        reviews.append(PendingReview(task_id=task.task_id,command_id=result.command.command_id))
        cards.add(proposal.card_id)
    return {'pending_reviews':reviews}


async def review_actions(state: Turn,runtime: Runtime[PipelineContext]) -> dict[str,str]:
    if not state['pending_reviews']:return {}
    ctx=runtime.context.request
    with data_api.connect(ctx) as db:
        outcomes=[await action_store.load(item.command_id,ctx,db) for item in state['pending_reviews']]
    pending=[str(item.command.command_id) for item in outcomes if item.command.state=='proposed']
    if pending:
        # A resume value only wakes the graph. Canonical ledger outcomes always decide what happened.
        interrupt({'command_ids':pending})
        with data_api.connect(ctx) as db:
            outcomes=[await action_store.load(item.command_id,ctx,db) for item in state['pending_reviews']]
        if any(item.command.state=='proposed' for item in outcomes):raise ValueError('Workflow decisions are still pending')
    return {}


async def settle_actions(state: Turn,runtime: Runtime[PipelineContext]) -> SettlementUpdate:
    sections: list[AnswerSection]=[]
    ctx=runtime.context.request
    owned=runtime.context.workflow
    for review in state['pending_reviews']:
        with data_api.connect(ctx) as db:outcome=await action_store.load(review.command_id,ctx,db)
        if outcome.command.state in ('proposed','confirmed'):raise ValueError('Cannot compose a reply before a recorded decision')
        if outcome.receipt is not None and outcome.receipt.outcome=='succeeded':
            message='Your confirmed command temporarily blocked the synthetic card ending '+outcome.command.target.last4+'. It did not reverse any transaction.'
        elif outcome.command.state=='rejected':message='You rejected the temporary block. This proposal did not change the synthetic card.'
        elif outcome.command.state=='expired':message='The temporary-block proposal expired without execution.'
        else:message='The temporary block failed because the target changed. This command did not change the synthetic card.'
        if owned is None:raise ValueError('Action settlement requires an owned turn')
        task_id=review.task_id+'_handoff'
        command=ActionCommand(command_id=uuid5(owned.turn.case_id,task_id+':handoff.create'),type='handoff.create',
            target={'reason':'card_verification','priority':'P0','summary':'Customer reported a suspicious synthetic card transaction. Review the recorded card decision.'},
            idempotency_key=f'{owned.turn.case_id}:{task_id}:handoff.create')
        handoff=await actions.propose_for_workflow(command,task_id,owned,ctx.model_copy(update={'agent':'fraud'}))
        if handoff.receipt is None or handoff.receipt.outcome!='succeeded':raise ValueError('The synthetic support handoff did not produce a successful receipt')
        message+=' A support handoff is recorded in this demo. There is no staffed queue. Open Actions to inspect the recorded receipts.'
        sections.append(AnswerSection(task_id=review.task_id+'_decision',domain='fraud',priority='P0',answer_kind='banking',
            answer=message,sources=[Source(id='action-receipt-'+review.task_id,title='Recorded synthetic action decisions',url=None,version='v1',publisher='DemoBank action ledger')],
            needs_human=True,response_origin='safety',prompt_version=None))
    return {'decision_sections':sections}


def compose(state: Turn) -> ComposeUpdate:
    tasks = state['plan'].by_priority()
    sections: list[AnswerSection] = []
    for task,result,evidence,meta in zip(tasks,state['results'],state['evidence'],state['metadata'],strict=True):
        sources = {item.id:item.source for item in evidence}
        sections.append(AnswerSection(task_id=task.task_id, domain=task.domain, priority=task.priority,
            answer_kind=result.answer_kind, answer=result.text,
            sources=[sources[c.source_id] for c in result.citations],
            needs_human=result.status in ('error','needs_human'), response_origin=meta.origin,
            prompt_version=meta.prompt_id))
    sections.extend(state['decision_sections'])
    unique = {s.id:s for section in sections for s in section.sources}
    answer = AnswerEvent(answer='\n\n'.join(section.answer for section in sections), sources=list(unique.values()),
        privacy_warning=False, history_trimmed=len(state['history'])<state['history_count'],
        disclaimer=NOTICE if any(section.answer_kind=='banking' for section in sections) else '',
        needs_human=any(section.needs_human for section in sections),
        source_check='rejected' if any(section.response_origin=='validation' for section in sections) else 'basic_checks_passed',
        prompt_version=None,
        response_origin='ai' if any(section.response_origin=='ai' for section in sections) else 'validation' if any(section.response_origin=='validation' for section in sections) else 'safety', sections=sections)
    return {'answer':answer}


def build_graph(checkpointer: BaseCheckpointSaver[int] | None) -> CompiledStateGraph[Turn, PipelineContext, Turn, Turn]:
    graph = StateGraph(Turn,context_schema=PipelineContext)
    graph.add_node('plan',plan)
    graph.add_node('prioritise',prioritise)
    graph.add_node('dispatch',dispatch)
    graph.add_node('propose_actions',propose_actions)
    graph.add_node('review_actions',review_actions)
    graph.add_node('settle_actions',settle_actions)
    graph.add_node('compose',compose)
    graph.add_edge(START,'plan')
    graph.add_edge('plan','prioritise')
    graph.add_edge('prioritise','dispatch')
    graph.add_edge('dispatch','propose_actions')
    graph.add_edge('propose_actions','review_actions')
    graph.add_edge('review_actions','settle_actions')
    graph.add_edge('settle_actions','compose')
    graph.add_edge('compose',END)
    return graph.compile(checkpointer=checkpointer)


async def answer_public(req: ChatRequest, risk: RiskAssessment, ctx: RequestContext, checkpointer: BaseCheckpointSaver[int] | None) -> AnswerEvent:
    if req.mode != 'public':raise ValueError('Initial v2 pipeline supports public mode only')
    if risk.fixed_response is not None:raise ValueError('Fixed safety responses must use the no-model pipeline')
    history=clean_history(req.messages)
    safe_request=req.model_copy(update={'messages':history})
    if checkpointer is not None and ctx.case_id is None:raise ValueError('Durable workflows require an owned conversation case')
    config={'configurable':{'thread_id':str(ctx.case_id)}} if checkpointer is not None else None
    with span('route','agent',route='llm',pipeline='v2'):
        result = await build_graph(checkpointer).ainvoke({'request':safe_request,'risk':risk,'history':history,
            'history_count':len(req.messages),'workflow_version':'v2-actions-v1'},config,context=PipelineContext(request=ctx,workflow=None),durability='sync' if checkpointer is not None else None)
    return result['answer']


async def answer_personal(req: ChatRequest,risk: RiskAssessment,ctx: RequestContext,owned: OwnedWorkflow,checkpointer: BaseCheckpointSaver[int]) -> AnswerEvent | WorkflowPausedEvent:
    if req.mode!='personal' or risk.fixed_response is not None:raise ValueError('Personal workflow requires a guarded personal request')
    history=clean_history(req.messages)
    config={'configurable':{'thread_id':str(ctx.case_id)}}
    graph=build_graph(checkpointer)
    result=await graph.ainvoke({'request':req.model_copy(update={'messages':history}),'risk':risk,'history':history,
        'history_count':len(req.messages),'workflow_version':'v2-actions-v1'},config,context=PipelineContext(request=ctx,workflow=owned),durability='sync')
    if not result.get('__interrupt__'):return result['answer']
    snapshot=await graph.aget_state(config)
    if snapshot.next!=('review_actions',):raise ValueError('Personal workflow paused outside its review node')
    checkpoint_id=snapshot.config['configurable']['checkpoint_id']
    with data_api.connect(ctx) as db:
        status=await workflow_store.waiting(owned.turn.turn_id,owned.turn.run_id,checkpoint_id,ctx,db)
        outcomes=[await action_store.load(item.command_id,ctx,db) for item in result['pending_reviews']]
    return WorkflowPausedEvent(workflow=status,actions=[ActionRequiredEvent(command_id=item.command.command_id,
        type=item.command.type,summary='Review a temporary block for synthetic card ending '+item.command.target.last4,
        expires_at=item.command.expires_at) for item in outcomes if item.command.command_id in status.pending_commands])
