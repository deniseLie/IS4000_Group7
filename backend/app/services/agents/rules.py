"""Rules that answer before the runner's model call (architecture §5.6.2; build plan phase 5).

The v4 rule answerers move here unchanged: card, account and transaction figures (Accounts), meetings, request status
and RM handovers (Relationship), app guidance (Guide). Compliance and Relationship, and Accounts' computed checks, still
answer with their released v4 prompts (compliance, document, relationship, retail), but every read runs as the v5 agent
under its spec: its tools and per-call limits, whole records for the rule code (tools.call whole=True), and only the
field views in what reaches a model (retail.evidence_for, relationship.evidence_for use tools.visible).
Urgent card help (a lost card, a charge she doesn't recognise) is not a rule here: the orchestrator answers it with
fixed steps and an urgent RM request (build plan 2b).
A rule returns None to let the runner's model answer. Importing this module registers the rules in runner.RULES."""
import re
from zoneinfo import ZoneInfo
from app.schemas.agents import Evidence
from app.schemas.chat import Message, RecordSelection
from app.schemas.contracts import AgentOutput, AgentResult, AgentSpec, Export, OfferProposal, PlanTask, RequestContext, Task
from app.services import client_intents, compliance_selection, prompts, rm_handover
from app.services.agents import (account_answers, card_answers, client_guidance, compliance, consent_answers, derived, document, meeting_answers,
    relationship, relationship_records, request_status_answers, retail, retail_checks, retail_records, runner, transaction_answers)
from app.services.agents.answer import WITHHELD

SGT = ZoneInfo('Asia/Singapore')
# A fee waiver or refund is a person's decision: Accounts hands the question to Relationship once (§14.6 handoff).
# "the annual fee on my travel card and can you get it waived?" (C28) has the fee 38 characters before "waived".
WAIVER = re.compile(r'\b(?:waive[ds]?|waiving|waiver|refund|reverse)\b[^.?!]{0,40}\b(?:fees?|interest|late charges?)\b'
    r'|\b(?:fees?|interest)\b[^.?!]{0,60}\b(?:waived?|waiving|waiver|refunded|reversed)\b'
    r'|\b(?:get|have|getting|having)\s+(?:it|that|this|them)\s+waived\b', re.I)
# The orchestrator sets this entity on Accounts tasks when Relationship has a task in the same plan (orchestrator.checked).
WITH_RELATIONSHIP = 'with_relationship'
# Accounts intents whose values code computes from the default reads (agents/derived.HOOKS): the runner answers them.
COMPUTED = frozenset({'trip_due_dates', 'savings_after_cards'})
STATUS_UNAVAILABLE = ("I couldn't retrieve your request status right now. Please check My Requests "
    "or try again later. I can't confirm an approval without a bank-recorded decision.")


def shim(task: PlanTask, domain: str) -> Task:
    """The v4 Task the moved answerers take: the question as an entity, plus the planner's entities."""
    return Task(task_id=task.id, domain=domain, intent='answer_question', priority=task.priority,
        entities={**task.entities, 'question': task.question})


async def knowledge(task: PlanTask, ctx: RequestContext) -> list[Evidence]:
    return await runner.read('knowledge_search', {'query': task.question, 'k': 4}, ctx, set())


def out(spec: AgentSpec, task: PlanTask, result: AgentResult, evidence: list[Evidence], prompt_id: str | None,
        origin: str | None = None, decision: str | None = None, proposals: list[OfferProposal] | None = None,
        exports: dict[str, Export] | None = None) -> AgentOutput:
    """An AgentOutput from a rule; an error shows only its fixed text and releases no evidence or offers. Exports (computed
    by code, agents/derived.py) go out only with an `ok` answer and only the names the spec declares, as on the model path."""
    status = result.status if result.status in {'ok', 'needs_clarification', 'needs_human'} else 'error'
    if status == 'error':
        text = result.text if result.text.endswith(WITHHELD) else WITHHELD
        return AgentOutput(task_id=task.id, agent=spec.name, status='error', result=result.model_copy(update={'text': text, 'citations': []}),
            origin='validation', prompt_id=prompt_id)
    released = {k: v for k, v in (exports or {}).items() if k in spec.exports} if status == 'ok' else {}
    return AgentOutput(task_id=task.id, agent=spec.name, status=status, result=result, evidence=runner.dumps(evidence),
        origin=origin or 'ai', prompt_id=prompt_id, decision=decision, proposals=proposals or [], exports=released)


def conversation(task: PlanTask, agent: str, text: str, status: str = 'needs_human') -> AgentResult:
    return AgentResult(task_id=task.id, agent=agent, status=status, answer_kind='conversation', text=text)


# ---- Guide ----

async def guide_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str,
        inputs: dict[str, Export] | None = None) -> AgentOutput | None:
    """Signed-in app guidance (personal-details form, statements, which loan, where to start) from fixed wording."""
    if ctx.principal.kind != 'customer':return None
    public = await knowledge(task, ctx)
    found = client_guidance.respond(task.id, task.question, public, ctx.received_at)
    return out(spec, task, found, public, runner.prompt_id(spec)) if found is not None else None


# ---- Accounts & Cards ----

async def accounts_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str,
        inputs: dict[str, Export] | None = None) -> AgentOutput | None:
    """Figures and "which card?" straight from records; questions needing computed checks keep the retail prompt. Every
    `ok` answer from her records also exports her account list (account_refs, no balances) for a dependent task."""
    if WAIVER.search(task.question) and not task.rerouted:
        return AgentOutput(task_id=task.id, agent=spec.name, status='out_of_scope', suggested='relationship')
    if task.intent in COMPUTED:return None  # computed by code (agents/derived.py), answered by the accounts prompt
    v4 = shim(task, 'retail')
    public = await knowledge(task, ctx)
    if (found := client_guidance.respond(task.id, task.question, public, ctx.received_at)) is not None:
        return out(spec, task, found, public, runner.prompt_id(spec))
    records = await retail_records.load(ctx)
    evidence = [*public, *retail.evidence_for(records)]
    refs = derived.account_refs_from(records.accounts)
    latest = history[-1].content if history and history[-1].role == 'user' else task.question
    direct = (account_answers.respond(task.id, task.question, records, public)
        or card_answers.respond(task.id, task.question, history, records, waiver_hint=not task.entities.get(WITH_RELATIONSHIP))
        or transaction_answers.respond(task.id, task.question, latest, records))
    if direct is not None:return out(spec, task, direct, evidence, runner.prompt_id(spec), exports=refs)
    if not retail_checks.relevant(records, task.question, ctx.received_at.astimezone(SGT).date()):return None
    result, evidence, prompt_id = await retail.model_answer(v4, task.question, history, language, ctx, records, evidence)
    return out(spec, task, result, evidence, prompt_id, exports=refs)


# ---- Compliance & Documents ----

async def compliance_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str,
        inputs: dict[str, Export] | None = None) -> AgentOutput:
    """The released compliance and document prompts' answers, scoped to the record the client selected, if any. Imported
    values (her account list from Accounts, P05) are citable evidence; whether the document she asked about failed its
    preliminary check is exported (check_failed, P07), computed by code from the check rows the answer used."""
    raw = task.entities.get('record_context')
    chosen = RecordSelection.model_validate(raw) if isinstance(raw, dict) else None
    documents = (chosen.kind == 'document') if chosen is not None else task.intent == 'document_findings'
    agent = 'document' if documents else 'compliance'
    public = [*runner.input_evidence(inputs or {}), *await knowledge(task, ctx)]
    token = compliance_selection.selection.set(chosen)
    try:result, evidence, prompt_id = await (document.respond if documents else compliance.respond)(shim(task, agent), history, language, ctx, public)
    finally:compliance_selection.selection.reset(token)
    return out(spec, task, result, evidence, prompt_id, exports=derived.check_failed(evidence, task.question))


# ---- Relationship & Service ----

async def relationship_rules(spec: AgentSpec, task: PlanTask, ctx: RequestContext, history: list[Message], language: str,
        inputs: dict[str, Export] | None = None) -> AgentOutput:
    """RM handovers (preview, then her choice), request status from her tickets, a consent change from her records (6e),
    a meeting request drafted from the records (one meeting.request offer, 2a), then the released relationship prompt's answer."""
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
        try:found = await request_status_answers.respond(task.id, question, ctx)
        except runner.FAILURES:
            return out(spec, task, conversation(task, 'relationship', STATUS_UNAVAILABLE, 'ok'), [], None, origin='safety')
        if found is not None:
            result, evidence = found
            return out(spec, task, result, evidence, None)
    public = await knowledge(task, ctx)
    if ctx.principal.kind == 'customer' and (change := await consent_answers.respond(task.id, question, ctx)) is not None:
        return out(spec, task, change.result, change.evidence, None, proposals=change.proposals)
    records = await relationship_records.load(ctx)
    if (meeting := await meeting_answers.respond(task.id, question, history, records, ctx, task.intent)) is not None:
        evidence = [*public, *relationship.evidence_for(records)]
        shares = await consent_answers.meeting_shares(task.id, ctx) if meeting.proposal else []
        return out(spec, task, meeting.result, evidence, prompts.current('relationship')['id'],
            proposals=[meeting.proposal, *shares] if meeting.proposal else [])
    result, evidence, prompt_id = await relationship.respond(shim(task, 'relationship'), history, language, ctx, public, records)
    return out(spec, task, result, evidence, prompt_id)


runner.RULES.update({'guide': (guide_rules,), 'accounts': (accounts_rules,),
    'compliance': (compliance_rules,), 'relationship': (relationship_rules,)})
