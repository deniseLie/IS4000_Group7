"""Rules that answer before the runner's model call (architecture §5.6.2; build plan phase 5).

The v4 rule answerers move here unchanged: card, account and transaction figures (Accounts), meetings, request status
and RM handovers (Relationship), app guidance (Guide). Compliance and Relationship still answer with their released v4
prompts, so these reads run under the v4 agent names and allow-lists (registry.TOOLS) until their own runner prompts
land. Urgent card help (a lost card, a charge she doesn't recognise) is not a rule here: the orchestrator answers it
with fixed steps and an urgent RM request (build plan 2b).
A rule returns None to let the runner's model answer. Importing this module registers the rules in runner.RULES."""
import re
from zoneinfo import ZoneInfo
from app.schemas.agents import Evidence
from app.schemas.chat import Message, RecordSelection
from app.schemas.contracts import AgentOutput, AgentResult, AgentSpec, OfferProposal, PlanTask, RequestContext, Task
from app.services import client_intents, compliance_selection, prompts, rm_handover
from app.services.agents import (account_answers, card_answers, client_guidance, compliance, document, meeting_answers,
    relationship, relationship_records, request_status_answers, retail, retail_checks, retail_records, runner, transaction_answers)
from app.services.agents.answer import WITHHELD

SGT = ZoneInfo('Asia/Singapore')
# A fee waiver or refund is a person's decision: Accounts hands the question to Relationship once (§14.6 handoff).
WAIVER = re.compile(r'\b(?:waive[ds]?|waiving|waiver|refund|reverse)\b[^.?!]{0,40}\b(?:fees?|interest|late charges?)\b'
    r'|\b(?:fees?|interest)\b[^.?!]{0,30}\b(?:waived?|waiver|refunded|reversed)\b', re.I)
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
    if WAIVER.search(task.question) and not task.rerouted:
        return AgentOutput(task_id=task.id, agent=spec.name, status='out_of_scope', suggested='relationship')
    if task.intent == 'trip_due_dates':return None  # computed by code (agents/derived.py), answered by the accounts prompt
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
    """RM handovers (preview, then her choice), request status from her tickets, a meeting request drafted from the records
    (one meeting.request offer, 2a), then the released relationship v6 answer."""
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
    public, current = await knowledge(task, ctx), as_v4(ctx, 'relationship')
    records = await relationship_records.load(current)
    if (meeting := await meeting_answers.respond(task.id, question, history, records, current, task.intent)) is not None:
        evidence = [*public, *relationship.evidence_for(records)]
        return out(spec, task, meeting.result, evidence, prompts.load('relationship', relationship.PROMPT)['id'],
            proposals=[meeting.proposal] if meeting.proposal else [])
    result, evidence, prompt_id = await relationship.respond(shim(task, 'relationship'), history, language, current, public, records)
    return out(spec, task, result, evidence, prompt_id)


runner.RULES.update({'guide': (guide_rules,), 'accounts': (accounts_rules,),
    'compliance': (compliance_rules,), 'relationship': (relationship_rules,)})
