"""Rules that answer before the runner's model call (architecture §5.6.2; build plan phase 5).

The v4 rule answerers move here unchanged: card, account and transaction figures (Accounts), the charge review that
lists charges for the fraud team (Security), meetings, request status and RM handovers (Relationship), app guidance
(Guide). Compliance and Relationship still answer with their released v4 prompts, and the charge review with fraud v7,
so these reads run under the v4 agent names and allow-lists (registry.TOOLS) until their own runner prompts land.
A rule returns None to let the runner's model answer. Importing this module registers the rules in runner.RULES."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from pydantic import TypeAdapter
from app.schemas.agents import Evidence
from app.schemas.chat import Message, RecordSelection
from app.schemas.contracts import AgentOutput, AgentResult, AgentSpec, OfferProposal, PlanTask, RequestContext, Task
from app.schemas.customer_data import CustomerTransaction
from app.schemas.disputes import DisputeItem
from app.schemas.fraud import DisputeProposal, FraudProposal
from app.services import case_summary, client_intents, compliance_selection, customer_data, fraud_reports, rm_handover
from app.services.agents import (account_answers, card_answers, client_guidance, compliance, document, fraud, fraud_records,
    relationship, request_status_answers, retail, retail_checks, retail_records, runner, transaction_answers)
from app.services.agents.answer import WITHHELD

SGT = ZoneInfo('Asia/Singapore')
STATUS_UNAVAILABLE = ("I couldn't retrieve your request status right now. Please check My Requests "
    "or try again later. I can't confirm an approval without a bank-recorded decision.")


def shim(task: PlanTask, domain: str) -> Task:
    """The v4 Task the moved answerers take: the question as an entity, plus the planner's entities."""
    return Task(task_id=task.id, domain=domain, intent='answer_question', priority=task.priority,
        entities={**task.entities, 'question': task.question})


def as_v4(ctx: RequestContext, agent: str) -> RequestContext:
    return ctx.model_copy(update={'agent': agent})


async def knowledge(task: PlanTask, ctx: RequestContext) -> list[Evidence]:
    return await runner.read('knowledge_search', {'query': task.question, 'k': 4}, ctx, set())


def out(spec: AgentSpec, task: PlanTask, result: AgentResult, evidence: list[Evidence], prompt_id: str | None,
        origin: str | None = None, decision: str | None = None, proposals: list[OfferProposal] | None = None) -> AgentOutput:
    """An AgentOutput from a rule; an error shows only its fixed text and releases no evidence or offers."""
    status = result.status if result.status in {'ok', 'needs_clarification', 'needs_human'} else 'error'
    if status == 'error':
        text = result.text if result.text.endswith(WITHHELD) else WITHHELD
        return AgentOutput(task_id=task.id, agent=spec.name, status='error', result=result.model_copy(update={'text': text, 'citations': []}),
            origin='validation', prompt_id=prompt_id)
    return AgentOutput(task_id=task.id, agent=spec.name, status=status, result=result, evidence=runner.dumps(evidence),
        origin=origin or 'ai', prompt_id=prompt_id, decision=decision, proposals=proposals or [])


def conversation(task: PlanTask, agent: str, text: str, status: str = 'needs_human') -> AgentResult:
    return AgentResult(task_id=task.id, agent=agent, status=status, answer_kind='conversation', text=text)


# ---- Guide ----

async def guide_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str) -> AgentOutput | None:
    """Signed-in app guidance (personal-details form, statements, which loan, where to start) from fixed wording."""
    if ctx.principal.kind != 'customer':return None
    public = await knowledge(task, ctx)
    found = client_guidance.respond(task.id, task.question, public, ctx.received_at)
    return out(spec, task, found, public, runner.prompt_id(spec)) if found is not None else None


# ---- Accounts & Cards ----

async def accounts_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str) -> AgentOutput | None:
    """Figures and "which card?" straight from records; questions needing computed checks keep the retail prompt."""
    v4, current = shim(task, 'retail'), as_v4(ctx, 'retail')
    public = await knowledge(task, ctx)
    if (found := client_guidance.respond(task.id, task.question, public, ctx.received_at)) is not None:
        return out(spec, task, found, public, runner.prompt_id(spec))
    records = await retail_records.load(current)
    evidence = [*public, *retail.evidence_for(records)]
    latest = history[-1].content if history and history[-1].role == 'user' else task.question
    direct = (account_answers.respond(task.id, task.question, records, public) or card_answers.respond(task.id, task.question, history, records)
        or transaction_answers.respond(task.id, task.question, latest, records))
    if direct is not None:return out(spec, task, direct, evidence, runner.prompt_id(spec))
    if not retail_checks.relevant(records, task.question, ctx.received_at.astimezone(SGT).date()):return None
    result, evidence, prompt_id = await retail.model_answer(v4, task.question, history, language, current, records, evidence)
    return out(spec, task, result, evidence, prompt_id)


# ---- Security ----

async def other_issues(task: PlanTask, ctx: RequestContext) -> tuple[list[CustomerTransaction], object, bool]:
    """The other issues the planner saw in the same message: a pending transfer (and its due day) and a lockout."""
    today = ctx.received_at.astimezone(SGT).date()
    transfer = task.entities.get('transfer')
    pending: list[CustomerTransaction] = []
    if isinstance(transfer, str):
        read = await customer_data.get_transactions(ctx, today - timedelta(days=90), today, {'status': 'pending'})
        pending = [row for row in TypeAdapter(list[CustomerTransaction]).validate_python(read.data) if row.card_id is None and row.amount < 0]
    due = retail_checks.due_date(transfer, today) if isinstance(transfer, str) else None
    return pending, due, task.entities.get('lockout') is True


async def fraud_team_offer(task: PlanTask, proposal: FraudProposal, flagged: DisputeProposal | None, ctx: RequestContext) -> tuple[str, OfferProposal] | None:
    """The charge list for the client to confirm and the urgent fraud-team handoff it offers; the summary comes from records."""
    v4 = shim(task, 'fraud')
    text = await fraud_reports.ask_to_confirm(v4, proposal, flagged, ctx)
    if text is None:return None
    records = await fraud_records.load(ctx)
    card = next(item for item in records.cards if item.card_id == proposal.card_id)
    ids = set(flagged.transaction_ids if flagged is not None and flagged.card_id == proposal.card_id else [proposal.transaction_id])
    items = [DisputeItem(transaction_id=row.txn_id, date=row.date, merchant=row.description, amount=row.amount, currency=row.currency)
        for row in records.transactions if row.txn_id in ids and row.amount < 0]
    transfers, due, lockout = await other_issues(task, ctx)
    words = task.entities.get('message')
    summary = case_summary.build(last4=card.last4, reported_id=proposal.transaction_id, items=items, block=None, dispute=None, possession=None,
        transfers=transfers, due=due, lockout=lockout, customer_words=words if isinstance(words, str) else task.question)
    return text, OfferProposal(task_id=task.id, agent='security', type='handoff.create',
        target={'reason': 'card_verification', 'priority': 'P0', 'summary': summary},
        summary=f'Send the charges on your card ending {card.last4} to our fraud team as urgent')


async def security_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str) -> AgentOutput | None:
    """An unrecognised charge keeps the fraud v7 review; a single eligible card gives one fraud-team offer to confirm."""
    if task.intent != 'unrecognised_charge':return None
    current = as_v4(ctx, 'fraud')
    decision, evidence = await fraud.respond(shim(task, 'fraud'), history, language, current, await knowledge(task, ctx))
    offer = await fraud_team_offer(task, decision.proposal, decision.dispute, current) if decision.proposal is not None else None
    if offer is None:return out(spec, task, decision.result, evidence, decision.prompt_id)
    text, proposal = offer
    if decision.result.status == 'error':  # the urgent steps and the charge list from records still go out
        return AgentOutput(task_id=task.id, agent='security', status='needs_human', origin='safety', prompt_id=decision.prompt_id,
            result=conversation(task, 'fraud', decision.result.text), decision=text, proposals=[proposal])
    return out(spec, task, decision.result.model_copy(update={'status': 'needs_human'}), evidence, decision.prompt_id, decision=text, proposals=[proposal])


# ---- Compliance & Documents ----

async def compliance_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str) -> AgentOutput:
    """The released compliance v5 and document v5 answers, scoped to the record the client selected, if any."""
    raw = task.entities.get('record_context')
    chosen = RecordSelection.model_validate(raw) if isinstance(raw, dict) else None
    documents = (chosen.kind == 'document') if chosen is not None else task.intent == 'document_findings'
    agent = 'document' if documents else 'compliance'
    public = await knowledge(task, ctx)
    token = compliance_selection.selection.set(chosen)
    try:result, evidence, prompt_id = await (document.respond if documents else compliance.respond)(shim(task, agent), history, language, as_v4(ctx, agent), public)
    finally:compliance_selection.selection.reset(token)
    return out(spec, task, result, evidence, prompt_id)


# ---- Relationship & Service ----

async def relationship_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str) -> AgentOutput:
    """RM handovers (preview, then her choice), request status from her tickets, then the released relationship v5 answer."""
    question = task.question
    if task.entities.get('handover') == 'rm':
        current = ctx.model_copy(update={'agent': None})
        raw = task.entities.get('record_context')
        documents = [chosen.document_id] if isinstance(raw, dict) and (chosen := RecordSelection.model_validate(raw)).document_id else []
        choice = task.entities.get('handover_choice')
        if ctx.principal.kind != 'customer' or ctx.case_id is None:
            return out(spec, task, conversation(task, 'relationship', rm_handover.GUEST), [], None, origin='safety')
        key = rm_handover.task_key(task.id + '_rm', ctx.case_id)
        if isinstance(choice, str):
            reference = task.entities.get('handover_reference')
            text = await rm_handover.choose(question, choice, reference if isinstance(reference, str) and reference else None, current, None, key,
                history=history[:-1], document_ids=documents)
            return out(spec, task, conversation(task, 'relationship', 'Okay.' if choice == 'cancel' else 'Thank you.'), [], None, origin='safety', decision=text)
        if rm_handover.confirm_first():text = await rm_handover.offer(question, history, current)
        else:text = (await rm_handover.route(question, task.priority, current, None, key, history=history, document_ids=documents)).reply()
        return out(spec, task, conversation(task, 'relationship', rm_handover.SORRY), [], None, origin='safety', decision=text)
    if client_intents.request_status(question):
        try:found = await request_status_answers.respond(task.id, question, as_v4(ctx, 'retail'))
        except runner.FAILURES:
            return out(spec, task, conversation(task, 'relationship', STATUS_UNAVAILABLE, 'ok'), [], None, origin='safety')
        if found is not None:
            result, evidence = found
            return out(spec, task, result, evidence, None)
    result, evidence, prompt_id = await relationship.respond(shim(task, 'relationship'), history, language, as_v4(ctx, 'relationship'),
        await knowledge(task, ctx))
    return out(spec, task, result, evidence, prompt_id)


runner.RULES.update({'guide': (guide_rules,), 'accounts': (accounts_rules,), 'security': (security_rules,),
    'compliance': (compliance_rules,), 'relationship': (relationship_rules,)})
