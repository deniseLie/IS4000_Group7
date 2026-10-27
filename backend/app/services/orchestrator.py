"""V2 LangGraph: public information, owned retail/compliance reads and authenticated fraud review.

Uses the explicitly configured knowledge store and an optional owned durable checkpoint saver.
Fraud proposals pause at a durable checkpoint; the remaining personal agents report explicit limitations.
Independent public/personal tasks run concurrently under AGENT_TIMEOUT_S (reads, retries and
answer generation together); failed sections have no evidence or proposals. Bounded English
rules prioritise fraud, security, regulatory deadlines, then ordinary tasks, preserving ties.
"""
import asyncio
import re
from datetime import timedelta
from zoneinfo import ZoneInfo
from typing import TypedDict
from uuid import UUID,uuid5
import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import TypeAdapter,ValidationError
from postgrest.exceptions import APIError
from app.core.config import settings
from app.core import data_api
from app.core.observability import log_event,span
from app.schemas.agents import Evidence, PlannedIssue, PlannerDraft
from app.schemas.chat import ChatRequest, Message
from app.schemas.customer_data import CustomerTransaction
from app.schemas.contracts import ActionCommand,AgentResult, Domain, Priority,RequestContext, RiskAssessment, Task, TaskPlan,Receipt
from app.schemas.events import ActionRequiredEvent,AnswerEvent, AnswerSection, Source,WorkflowPausedEvent
from app.schemas.actions import ActionOutcome
from app.schemas.disputes import StoredDisputeTarget
from app.schemas.fraud import DisputeProposal,FraudProposal
from app.schemas.workflows import OwnedWorkflow,PendingReview,PipelineContext,ResponseMeta
from app.schemas.knowledge import KnowledgeQuery,SectionKey
from app.services import actions,audit,case_summary,customer_data,kb, llm_gateway, prompts,workflow_store
from app.services.actions import store as action_store
from app.services.actions.store import ActionIntegrityError
from app.services.agents import compliance,document,fraud,relationship,retail,retail_checks
from app.services.agents.answer import WITHHELD, respond, response_prompt_id
from app.services.agents.registry import AGENT_FOR
from app.services.answers import NOTICE, clean_history, retrieve
from app.services.knowledge import INDEX,source
from app.services.guardrail import FRAUD
from app.services.authorization import require_permission

DISPUTE='_dispute'
MULTIPLE = re.compile(r'\b(?:and|also|plus|another|as well)\b|;|\?\s*\S|,\s+[A-Za-z]',re.I)
SECURITY = re.compile(r'\b(?:security|login|log\s*in|sign\s*in|locked\s+out|lockout|password|passcode|phishing|scams?|hacked|compromised|2fa|mfa|otp)\b',re.I)
# Signing in is retail app help even when the question asks whether it relates to a reported charge; the fraud task covers the charge.
LOGIN = re.compile(r"\b(?:locked\s+out|lockout|log\s*in|sign\s*in|password|passcode|can['’]?t\s+(?:get\s+into|access|open)\s+(?:the|my)\s+app"
    r"|(?:app|account)\s+(?:is\s+|was\s+|got\s+|has\s+been\s+)?locked|otps?|one[- ]time\s+(?:codes?|passwords?))\b",re.I)
CHARGE = re.compile(r'\bcharges?\b',re.I)
RELATED = re.compile(r'\b(?:related|connected|linked|because of|caused by)\b',re.I)
DEADLINE = re.compile(r'\b(?:deadline|overdue|expir(?:ation|e[sd]?|ing|y)|time left)\b|\bdue\b(?!\s+(?:to|diligence)\b)',re.I)
TOPICS: dict[Domain, re.Pattern[str]] = {
    'retail':re.compile(r'\b(?:accounts?|balances?|loans?|cards?|payments?|transfers?|deposits?|statements?|log\s*in|sign\s*in|password|passcode)\b',re.I),
    'compliance':re.compile(r'\b(?:compliance|review|checklist|notice)\b|\bletter\b[^.!?]{0,100}\b(?:update|provide|send)\b[^.!?]{0,80}\b(?:info(?:rmation)?|documents?|details)\b',re.I),
    'document':re.compile(r'\b(?:documents?|docx|pdf|files?|upload|pages)\b',re.I),
    'relationship':re.compile(r'\b(?:relationship manager|advisor|adviser|meeting|appointment|handover)\b|\bRMs?\b|\bmeet (?:her|him|them|my)\b'
        r'|\b(?:old|previous|former|new)\s+(?:guy|lady|person|rm|banker|manager|advisor|adviser)\b|\bmy (?:open |pending )?requests?\b',re.I),
}
# Asking a person at the bank to do something is relationship intake even when it names a retail word such as "statement".
RM_REQUEST=re.compile(r'\b(?:can|could|would)\s+(?:someone|somebody|my (?:rm|relationship manager|advisor|adviser|banker))\s+(?:please\s+)?'
    r'(?:send|arrange|prepare|get|call|email|post)\b',re.I)


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
    disputes: list[DisputeProposal | None]
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
    disputes: list[DisputeProposal | None]


class TaskDispatch(TypedDict):
    result: AgentResult
    evidence: list[Evidence]
    metadata: ResponseMeta
    proposal: FraudProposal | None
    dispute: DisputeProposal | None


class ReviewUpdate(TypedDict):
    pending_reviews: list[PendingReview]


class SettlementUpdate(TypedDict):
    decision_sections: list[AnswerSection]


class ComposeUpdate(TypedDict):
    answer: AnswerEvent


def topic_domain(question: str) -> Domain | None:
    """Rules own clear domain assignments, including questions extracted by the planner."""
    if LOGIN.search(question) and (not FRAUD.search(question) or RELATED.search(question)):return 'retail'
    if FRAUD.search(question):return 'fraud'
    if RM_REQUEST.search(question):return 'relationship'
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
    if req.record_context is not None:
        return {'issues':[PlannedIssue(domain='document' if req.record_context.kind=='document' else 'compliance',question=question)]}
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
    return {'issues':issues}


def task_priority(issue: PlannedIssue) -> Priority:
    """Bounded English rules classify security and regulatory deadlines; ties retain message order."""
    if issue.domain=='fraud':return 'P0'
    if SECURITY.search(issue.question):return 'P1'
    if issue.domain in {'compliance','document'} and DEADLINE.search(issue.question):return 'P2'
    return 'P3'


def prioritise(state: Turn) -> PriorityUpdate:
    risk=state['risk']
    issues=state['issues']
    if {'fraud_suspected','lost_card'}.intersection(risk.risk_flags) and not any(issue.domain=='fraud' for issue in issues):
        # The planner's own charge question becomes the fraud task, so the charge is not answered twice.
        charge=next((i for i,issue in enumerate(issues) if CHARGE.search(issue.question) and not LOGIN.search(issue.question)),None)
        issues=([*issues[:charge],PlannedIssue(domain='fraud',question=issues[charge].question),*issues[charge+1:]] if charge is not None
            else [PlannedIssue(domain='fraud',question=risk.redacted_message),*issues])
    fraud_reported=any(issue.domain=='fraud' for issue in issues)
    # A sign-in problem raised with a suspected charge is answered as a security step; the link is a rule, not a finding.
    tasks = [Task(task_id=f't{i+1}', domain=issue.domain, intent='answer_question',
        priority=task_priority(issue), entities={'question':issue.question,
            **({'linked':'card_fraud'} if fraud_reported and issue.domain=='retail' and LOGIN.search(issue.question) else {})})
        for i,issue in enumerate(issues)]
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


def focused_history(state: Turn,task: Task) -> list[Message]:
    """With several issues in one message, the retail agent sees its own planned issue in place of that message,
    so it does not answer (and misquote figures from) issues that other sections answer."""
    history=state['history']
    question=task.entities.get('question')
    if task.domain!='retail' or len(state['plan'].tasks)<2 or not history or history[-1].role!='user' or not isinstance(question,str):return history
    return [*history[:-1],history[-1].model_copy(update={'content':question})]


async def linked_evidence(task: Task,ctx: RequestContext) -> list[Evidence]:
    """Approved security guidance for a sign-in task linked to a reported charge, fetched by id rather than by keyword."""
    if task.entities.get('linked')!='card_fraud':return []
    if settings.knowledge_store!='database':
        article=INDEX['app-login-recovery']
        return [Evidence(id=article.id,text=article.content,publisher=article.publisher,scope=article.notice,source=Source.model_validate(source(article)))]
    with data_api.connect(ctx) as db:item=await kb.get_section(SectionKey(article_id='app-login-recovery',version=2,anchor='unexpected-code'),ctx,db)
    if item is None or not item.current:return []
    return [Evidence(id=item.cite,text=item.body,publisher=item.publisher,scope=item.scope,source=item.source())]


def task_prompt_id(task: Task,request: ChatRequest) -> str | None:
    agent=AGENT_FOR[task.domain]
    if request.mode=='public' or task.domain=='smalltalk':return response_prompt_id(agent)
    if task.domain=='fraud':return prompts.load(agent,fraud.PROMPT)['id']
    if task.domain=='retail':return prompts.load(agent,retail.PROMPT)['id']
    if task.domain=='document':return prompts.load('document','v3')['id']
    if task.domain=='relationship':return prompts.load(agent,relationship.PROMPT)['id']
    if task.domain=='compliance':return prompts.load(agent,'v2')['id']
    return None


async def generate_task(task: Task,state: Turn,ctx: RequestContext) -> TaskDispatch:
    """One independent task owns its evidence; only the later proposal node can write actions."""
    if settings.knowledge_store=='database':
        evidence=await database_evidence(task,ctx)
    else:evidence=task_evidence(task,state['history'],state['request'].language)
    if state['request'].mode=='personal':
        known={item.id for item in evidence}
        evidence=[*evidence,*(item for item in await linked_evidence(task,ctx) if item.id not in known)]
    proposal: FraudProposal | None=None
    dispute: DisputeProposal | None=None
    agent=AGENT_FOR[task.domain]
    prompt_id=task_prompt_id(task,state['request'])
    if state['request'].mode=='public' or task.domain=='smalltalk':
        question=task.entities['question']
        if not isinstance(question,str):raise ValueError('Planned task question must be text')
        result=await respond(task,question,state['history'],evidence,state['request'].language)
    elif task.domain=='fraud':
        decision,evidence=await fraud.respond(task,state['history'],state['request'].language,
            ctx.model_copy(update={'agent':'fraud'}),evidence)
        result=decision.result;proposal=decision.proposal;dispute=decision.dispute;prompt_id=decision.prompt_id
    elif task.domain in {'retail','compliance','relationship','document'}:
        responder={'retail':retail.respond,'compliance':compliance.respond,'relationship':relationship.respond,'document':document.respond}[task.domain]
        from app.services.compliance_selection import selection
        token=selection.set(state['request'].record_context)
        try:
            result,evidence,prompt_id=await responder(task,focused_history(state,task),state['request'].language,
                ctx.model_copy(update={'agent':agent}),evidence)
        finally:selection.reset(token)
    else:
        result=AgentResult(task_id=task.task_id,agent=agent,status='error',
            text='This personal service is not connected yet. Use General help for public information or your bank’s official support channel.')
    return {'result':result,'evidence':evidence,'proposal':proposal,'dispute':dispute,
        'metadata':ResponseMeta(origin='validation' if result.status=='error' else 'ai',prompt_id=prompt_id)}


async def dispatch_task(task: Task,state: Turn,ctx: RequestContext) -> TaskDispatch:
    """Bound the whole task and withhold expected provider/data failures without releasing its evidence."""
    agent=AGENT_FOR[task.domain]
    try:
        with span('task','agent',task_id=task.task_id,agent=agent,priority=task.priority):
            return await asyncio.wait_for(generate_task(task,state,ctx),timeout=settings.agent_timeout_s)
    except (llm_gateway.LLMError,TimeoutError,ValidationError,APIError,httpx.TransportError,PermissionError,
        customer_data.CustomerDataIntegrityError,kb.KnowledgeResponseError) as error:
        code=data_api.database_code(error) if isinstance(error,APIError) else None
        fields={'task_id':task.task_id,'agent':agent,'error_type':type(error).__name__,'db_code':code}
        log_event('agent_failed',**fields)
        audit.add('agent','agent.failed','failed',details=fields)
        text=fraud.URGENT+' '+WITHHELD if task.domain=='fraud' else WITHHELD
        return {'result':AgentResult(task_id=task.task_id,agent=agent,status='error',text=text),'evidence':[],
            'proposal':None,'dispute':None,'metadata':ResponseMeta(origin='validation',prompt_id=task_prompt_id(task,state['request']))}


async def dispatch(state: Turn, runtime: Runtime[PipelineContext]) -> DispatchUpdate:
    """Run reads/agents concurrently, preserving priority order and cancelling siblings on unexpected errors."""
    ctx=runtime.context.request
    require_permission(ctx.principal,'kb.search',None,ctx)
    async with asyncio.TaskGroup() as group:
        running=[group.create_task(dispatch_task(task,state,ctx)) for task in state['plan'].by_priority()]
    rows=[task.result() for task in running]
    return {'results':[row['result'] for row in rows],'evidence':[row['evidence'] for row in rows],
        'metadata':[row['metadata'] for row in rows],'proposals':[row['proposal'] for row in rows],'disputes':[row['dispute'] for row in rows]}


async def propose_actions(state: Turn,runtime: Runtime[PipelineContext]) -> ReviewUpdate:
    owned=runtime.context.workflow
    reviews: list[PendingReview]=[]
    cards: set[str]=set()
    disputes=state.get('disputes') or [None]*len(state['proposals'])
    for task,proposal,dispute in zip(state['plan'].by_priority(),state['proposals'],disputes,strict=True):
        if proposal is None or proposal.card_id in cards:continue
        if owned is None:raise ValueError('Personal proposals require an owned durable turn')
        ctx=runtime.context.request.model_copy(update={'agent':'fraud'})
        offers=[(task.task_id,'card.temporary_block',{'card_id':proposal.card_id})]
        # The dispute draft is its own decision in the same paused turn, under its own workflow task id.
        if dispute is not None and dispute.card_id==proposal.card_id:offers.append((task.task_id+DISPUTE,'card.dispute',dispute.model_dump(mode='json')))
        for task_id,kind,target in offers:
            command=ActionCommand(command_id=uuid5(owned.turn.case_id,task_id+':'+kind),type=kind,target=target,
                idempotency_key=f'{owned.turn.case_id}:{task_id}:{kind}')
            result=await actions.propose_for_workflow(command,task_id,owned,ctx)
            reviews.append(PendingReview(task_id=task_id,command_id=result.command.command_id))
        cards.add(proposal.card_id)
    return {'pending_reviews':reviews}


def base_task(task_id: str) -> str:
    return task_id.removesuffix('_decision').removesuffix(DISPUTE)


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
    """Fixed decision text per recorded outcome, then one staff handoff per reported charge with the deterministic case summary."""
    sections: list[AnswerSection]=[]
    ctx=runtime.context.request
    owned=runtime.context.workflow
    outcomes: dict[str,dict[str,ActionOutcome]]={}
    for review in state['pending_reviews']:
        with data_api.connect(ctx) as db:outcome=await action_store.load(review.command_id,ctx,db)
        if outcome.command.state in ('proposed','confirmed'):raise ValueError('Cannot compose a reply before a recorded decision')
        if owned is None:raise ValueError('Action settlement requires an owned turn')
        outcomes.setdefault(base_task(review.task_id),{})[outcome.command.type]=outcome
        receipt=outcome.receipt.outcome if outcome.receipt is not None else None
        text=(decision_text(outcome.command.state,receipt,outcome.command.target.last4) if outcome.command.type=='card.temporary_block'
            else dispute_text(outcome.command.state,receipt,outcome.command.target))
        sections.append(AnswerSection(task_id=review.task_id+'_decision',domain='fraud',priority='P0',answer_kind='banking',answer=text,
            sources=[Source(id='action-receipt-'+review.task_id,title='Recorded synthetic action decisions',url=None,version='v1',publisher='DemoBank action ledger'),
                Source.model_validate(source(INDEX['unrecognised-charge']))],
            needs_human=True,response_origin='safety',prompt_version=None))
    for task_id,decided in outcomes.items():
        block,dispute=decided.get('card.temporary_block'),decided.get('card.dispute')
        succeeded={kind for kind,item in decided.items() if item.receipt is not None and item.receipt.outcome=='succeeded'}
        if not succeeded or block is None or owned is None:continue
        # Staff verify a blocked card and review a confirmed dispute; both come from this one summary under one handoff.
        reason='card_dispute' if 'card.dispute' in succeeded else 'card_verification'
        handoff_id=task_id+'_handoff'
        command=ActionCommand(command_id=uuid5(owned.turn.case_id,handoff_id+':handoff.create'),type='handoff.create',
            target={'reason':reason,'priority':'P0','summary':await staff_summary(state,task_id,block,dispute,ctx)},
            idempotency_key=f'{owned.turn.case_id}:{handoff_id}:handoff.create')
        handoff=await actions.propose_for_workflow(command,handoff_id,owned,ctx.model_copy(update={'agent':'fraud'}))
        if handoff.receipt is None or handoff.receipt.outcome!='succeeded':raise ValueError('The synthetic support handoff did not produce a successful receipt')
    return {'decision_sections':sections}


async def staff_summary(state: Turn,task_id: str,block: ActionOutcome,dispute: ActionOutcome | None,ctx: RequestContext) -> str:
    """Case summary from records and recorded decisions; the other issues come from the plan and a fresh pending-transfer read."""
    tasks=state['plan'].by_priority()
    index=[task.task_id for task in tasks].index(task_id)
    proposal=state['proposals'][index]
    target=dispute.command.target if dispute is not None else None
    items=target.items if isinstance(target,StoredDisputeTarget) else []
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    transfer=[task for task in tasks if task.domain=='retail' and retail_checks.TRANSFER.search(str(task.entities.get('question','')))
        and 'linked' not in task.entities]
    pending=[]
    if transfer:
        read=await customer_data.get_transactions(ctx,today-timedelta(days=90),today,{'status':'pending'})
        pending=[row for row in TypeAdapter(list[CustomerTransaction]).validate_python(read.data) if row.card_id is None and row.amount<0]
    due=retail_checks.due_date(str(transfer[0].entities['question']),today) if transfer else None
    return case_summary.build(last4=block.command.target.last4,reported_id=proposal.transaction_id if proposal else '',items=items,
        block=block.command.state,dispute=dispute.command.state if dispute is not None else None,
        possession=dispute.command.confirmation_input.card_in_possession if dispute is not None and dispute.command.confirmation_input else None,
        transfers=pending,due=due,lockout=any(task.entities.get('linked')=='card_fraud' for task in tasks),customer_words=state['risk'].redacted_message)


def decision_text(state: str,receipt: str | None,last4: str) -> str:
    """Fixed wording for a recorded block outcome; "blocked" is said only with a succeeded receipt."""
    if receipt=='succeeded':return ('Your card ending '+last4+' is temporarily blocked. This did not reverse any transaction. '
        'Next steps: bank staff will verify your identity and decide on permanent cancellation; after cancellation the bank issues a '
        'replacement card with a new number to your registered address. A support handoff for identity verification and permanent '
        'cancellation is recorded with urgent priority. This demo has no staffed queue. Open Actions to inspect the recorded receipts.')
    if state=='rejected':return ('You chose not to block the card ending '+last4+'; it stays active. Urgent steps: contact your bank now '
        'through the official fraud line on the back of your card or its official app, check your other recent transactions, and never '
        'share your PIN, password, one-time code or full card number. You can still use Talk to a person.')
    if state=='expired':return 'The temporary-block proposal expired without changing your card.'
    return 'The temporary block failed because the card changed. Your card was not changed.'


def dispute_text(state: str,receipt: str | None,target: object) -> str:
    """Fixed wording for a recorded dispute draft; "recorded" is said only with a succeeded receipt."""
    count=len(target.items) if isinstance(target,StoredDisputeTarget) else 0
    last4=target.last4 if isinstance(target,StoredDisputeTarget) else ''
    if receipt=='succeeded':return (f'Your dispute draft for {count} charges on the card ending {last4} is recorded for bank staff review. '
        'Staff decide any refund or cancellation; nothing has been refunded. Your case summary goes to staff with the support handoff, '
        'so you do not need to repeat the story. Open Actions to inspect the receipt.')
    if state=='rejected':return 'You chose not to send the dispute draft. You can still report the charges through the official fraud line or app.'
    if state=='expired':return 'The dispute draft expired without being sent.'
    return 'The dispute draft was not recorded because a listed charge changed. Nothing was sent.'


async def escalate_validation(state: Turn,runtime: Runtime[PipelineContext]) -> SettlementUpdate:
    """Record one receipt-backed handoff for withheld sections in a linked customer's saved case.

    No rejected model text or customer records enter the summary. Guests and unsaved public
    chats have no customer-owned action case and keep the explicit official-support guidance.
    The existing action ledger deduplicates retries and commits audit/outbox with the handoff.
    """
    ctx=runtime.context.request
    failed=[task for task,result,meta in zip(state['plan'].by_priority(),state['results'],state['metadata'],strict=True)
        if result.status=='error' and meta.origin=='validation']
    sections=state['decision_sections']
    if not failed or ctx.principal.kind!='customer' or ctx.case_id is None:return {'decision_sections':sections}
    task=failed[0]
    task_id='validation_handoff'
    command=ActionCommand(command_id=uuid5(ctx.case_id,task_id+':handoff.create'),type='handoff.create',
        target={'reason':'general_support','priority':task.priority,
            'summary':'An assistant section was withheld because it could not be verified. Review the saved customer case; no rejected answer is included.'},
        idempotency_key=f'{ctx.case_id}:{task_id}:handoff.create')
    current=ctx.model_copy(update={'agent':None})
    owned=runtime.context.workflow
    if owned is not None:
        outcome=await actions.propose_for_workflow(command,task_id,owned,current)
        receipt=actions.receipt_for(outcome)
    else:receipt=await actions.run_action(command,current,False)
    if not isinstance(receipt,Receipt) or receipt.outcome!='succeeded':
        raise ActionIntegrityError('Validation handoff did not return a successful receipt for the owned customer case')
    section=AnswerSection(task_id=task_id,domain=task.domain,priority=task.priority,answer_kind='banking',
        answer='A support handoff is recorded for the withheld part of this answer in this demo. There is no staffed queue. Open Actions to inspect its recorded receipt. Use your bank’s official support channel if you need assistance.',
        sources=[Source(id='action-receipt-'+task_id,title='Recorded synthetic support handoff',url=None,version='v1',publisher='DemoBank action ledger')],
        needs_human=True,response_origin='safety',prompt_version=None)
    return {'decision_sections':[*sections,section]}


def answer_for(state: Turn,pending: dict[str,list[ActionRequiredEvent]]) -> AnswerEvent:
    """Sections in priority order; recorded decisions are written into their own task's section, block first."""
    tasks = state['plan'].by_priority()
    decisions: dict[str,list[AnswerSection]]={}
    for item in state.get('decision_sections',[]):decisions.setdefault(base_task(item.task_id),[]).append(item)
    sections: list[AnswerSection] = []
    for task,result,evidence,meta in zip(tasks,state['results'],state['evidence'],state['metadata'],strict=True):
        sources = {item.id:item.source for item in evidence}
        cited = [sources[c.source_id] for c in result.citations]
        recorded = decisions.pop(task.task_id,[])
        for decision in recorded:cited += [item for item in decision.sources if item.id not in {s.id for s in cited}]
        sections.append(AnswerSection(task_id=task.task_id, domain=task.domain, priority=task.priority,
            answer_kind=result.answer_kind, answer=result.text, sources=cited,
            needs_human=result.status in ('error','needs_human') or bool(recorded), response_origin=meta.origin,
            prompt_version=meta.prompt_id, pending_actions=pending.get(task.task_id,[]),
            decision=' '.join(item.answer for item in recorded) or None))
    sections.extend(item for items in decisions.values() for item in items)
    unique = {s.id:s for section in sections for s in section.sources}
    answer = AnswerEvent(answer='\n\n'.join(section.answer+('\n\n'+section.decision if section.decision else '') for section in sections), sources=list(unique.values()),
        privacy_warning=False, history_trimmed=len(state['history'])<state['history_count'],
        disclaimer=NOTICE if any(section.answer_kind=='banking' or section.decision for section in sections) else '',
        needs_human=any(section.needs_human for section in sections),
        source_check='rejected' if any(section.response_origin=='validation' for section in sections) else 'basic_checks_passed',
        prompt_version=None,
        response_origin='ai' if any(section.response_origin=='ai' for section in sections) else 'validation' if any(section.response_origin=='validation' for section in sections) else 'safety', sections=sections)
    return answer


def compose(state: Turn) -> ComposeUpdate:
    return {'answer':answer_for(state,{})}


def preview_answer(state: Turn,pending: dict[str,list[ActionRequiredEvent]]) -> AnswerEvent:
    """Every section while decisions are pending; the block and dispute offers sit on their own task's section."""
    return answer_for({**state,'decision_sections':[]},pending)


def paused_preview(state: Turn,outcomes: list[ActionOutcome],pending_ids: list[UUID]) -> tuple[list[ActionRequiredEvent],AnswerEvent]:
    pending: dict[str,list[ActionRequiredEvent]]={}
    for review,item in zip(state['pending_reviews'],outcomes,strict=True):
        if item.command.command_id not in pending_ids:continue
        target=item.command.target
        summary=(f'Review a dispute draft of {len(target.items)} charges on synthetic card ending {target.last4}'
            if isinstance(target,StoredDisputeTarget) else 'Review a temporary block for synthetic card ending '+target.last4)
        pending.setdefault(base_task(review.task_id),[]).append(ActionRequiredEvent(command_id=item.command.command_id,
            type=item.command.type,summary=summary,expires_at=item.command.expires_at))
    return [event for events in pending.values() for event in events],preview_answer(state,pending)


def build_graph(checkpointer: BaseCheckpointSaver[int] | None) -> CompiledStateGraph[Turn, PipelineContext, Turn, Turn]:
    graph = StateGraph(Turn,context_schema=PipelineContext)
    graph.add_node('plan',plan)
    graph.add_node('prioritise',prioritise)
    graph.add_node('dispatch',dispatch)
    graph.add_node('propose_actions',propose_actions)
    graph.add_node('review_actions',review_actions)
    graph.add_node('settle_actions',settle_actions)
    graph.add_node('escalate_validation',escalate_validation)
    graph.add_node('compose',compose)
    graph.add_edge(START,'plan')
    graph.add_edge('plan','prioritise')
    graph.add_edge('prioritise','dispatch')
    graph.add_edge('dispatch','propose_actions')
    graph.add_edge('propose_actions','review_actions')
    graph.add_edge('review_actions','settle_actions')
    graph.add_edge('settle_actions','escalate_validation')
    graph.add_edge('escalate_validation','compose')
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
    pending,preview=paused_preview(result,outcomes,status.pending_commands)
    return WorkflowPausedEvent(workflow=status,actions=pending,preview=preview)
