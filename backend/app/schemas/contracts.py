"""Shared data contracts between pipeline modules (docs/architecture.md §4, v5 additions §14.8).

Keep changes backwards-compatible within a build phase and note them in docs/architecture.md."""
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, model_validator

Priority = Literal['P0', 'P1', 'P2', 'P3']  # P0 fraud > P1 security > P2 regulatory deadline > P3 everything else
Domain = Literal['fraud', 'retail', 'compliance', 'document', 'relationship', 'smalltalk']
AgentName = Literal['retail', 'fraud', 'compliance', 'document', 'relationship']
# v5 agents (architecture §5.6.1): five client agents and two RM agents (phase 9). `compliance` and `relationship` keep
# their v4 names; the v4 agents use registry.TOOLS; agents/rules.py still calls the v4 responders under these names.
TargetAgent = Literal['guide', 'accounts', 'security', 'compliance', 'relationship', 'rm_client_insight', 'rm_service_desk']
TaskStatus = Literal['pending', 'running', 'awaiting_confirmation', 'done', 'failed']
AgentStatus = Literal['ok', 'needs_clarification', 'needs_confirmation', 'needs_human', 'error']
AnswerKind = Literal['banking', 'conversation']  # same values as the LLM output contract in services/llm.py
CommandState = Literal['proposed', 'confirmed', 'succeeded', 'failed', 'expired']
RiskFlag = Literal['fraud_suspected', 'lost_card', 'secret_shared', 'human_requested']
RedactionType = Literal['card_number', 'password', 'pin', 'otp', 'cvv', 'nric', 'email']
AuditCategory = Literal['auth', 'chat', 'agent', 'data', 'policy', 'action', 'row', 'consent', 'notification', 'handoff', 'document', 'admin']


def now() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


# RequestContext: created by the gateway, passed to every module
class Principal(Contract):
    kind: Literal['guest', 'customer', 'system']
    user_id: UUID | None = None
    customer_id: int | None = None  # banking.customer_profiles.customer_id (bigint), resolved by the gateway from the JWT sub
    auth_session_id: UUID | None = None
    aal: Literal['aal1', 'aal2'] = 'aal1'
    jwt: SecretStr | None = Field(default=None, exclude=True, repr=False)  # never logged or serialised


class RequestContext(Contract):
    request_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    case_id: UUID | None = None
    principal: Principal
    agent: AgentName | TargetAgent | None = None  # set only by backend dispatch; never read from tool arguments
    locale: str = 'en-SG'
    received_at: datetime = Field(default_factory=now)


# RiskAssessment: Input Guardrail -> Orchestrator; fixed_response set means the orchestrator is skipped
class Redaction(Contract):
    type: RedactionType
    count: int = Field(ge=1)


class FixedResponse(Contract):
    code: str
    needs_human: bool = True


class RiskAssessment(Contract):
    redacted_message: str
    redactions: list[Redaction] = []
    risk_flags: list[RiskFlag] = []
    priority: Priority = 'P3'
    fixed_response: FixedResponse | None = None


# TaskPlan: Orchestrator, saved in the Conversation Store
class Task(Contract):
    task_id: str
    domain: Domain
    intent: str
    priority: Priority = 'P3'
    entities: dict[str, Any] = {}
    needs_auth: bool = False
    status: TaskStatus = 'pending'


class TaskPlan(Contract):
    plan_id: UUID = Field(default_factory=uuid4)
    tasks: list[Task] = []

    def by_priority(self) -> list[Task]:
        return sorted(self.tasks, key=lambda t: t.priority)  # stable sort: ties keep message order


# AgentResult: Agent -> Orchestrator
class Citation(Contract):
    source_id: str  # e.g. kb:card-dispute@v3 or kb:card-dispute@v3#anchor


class ToolCallRef(Contract):
    call_id: UUID
    tool: str


class PendingAction(Contract):
    command_id: UUID
    type: str
    summary: str


class ReceiptRef(Contract):
    receipt_id: str
    command_id: UUID


class AgentResult(Contract):
    task_id: str
    agent: str
    status: AgentStatus
    answer_kind: AnswerKind = 'banking'
    text: str = ''
    question: str | None = None
    citations: list[Citation] = []
    tool_calls: list[ToolCallRef] = []
    pending_action: PendingAction | None = None
    receipts: list[ReceiptRef] = []

    @model_validator(mode='after')
    def _clarification(self):
        if self.status == 'needs_clarification' and not (self.question and self.question.strip()):
            raise ValueError('needs_clarification requires a question')
        if self.status != 'needs_clarification' and self.question is not None:
            raise ValueError('question is only valid for needs_clarification')
        return self


# ActionCommand / Receipt: Action Service; the executor comes from `type` in the registry, never from the model
class ActionCommand(Contract):
    command_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str  # case_id:task_id:type
    type: str
    target: dict[str, Any] = {}
    state: CommandState = 'proposed'
    expires_at: datetime | None = None


class ProposedCommand(Contract):  # run_action() result when the type needs customer confirmation
    command_id: UUID
    expires_at: datetime


class Receipt(Contract):
    receipt_id: str
    command_id: UUID
    outcome: Literal['succeeded', 'failed']
    audit_event_id: int | None = None


# v5 agent catalogue and plan (architecture §14.5, §14.8); /chat runs on these since phase 5.
OPEN_INTENT = 'open'  # the planner found no listed intent: the agent chooses its reads in the bounded tool loop
QUESTION = 'question'  # pseudo-entity: the task's own question, e.g. as a knowledge_search query
ToolName = str  # a model-facing tool in services/tools.TOOLS
# Export types (§14.6, phase 7): small values computed by code, never raw rows, balances or free text.
EXPORT_TYPES = frozenset({'bool', 'date', 'reference', 'label', 'list[date]', 'list[account_ref]', 'list[client_ref]'})  # client_ref: RM side, code only


class ToolView(Contract):
    """What one agent receives from one tool: dotted record fields (lists are transparent) and per-call limits."""
    fields: frozenset[str] | None = None  # None: the whole record
    max_days: int | None = Field(default=None, gt=0)  # oldest record, counted back from today
    max_rows: int | None = Field(default=None, gt=0)
    free_text: bool = False  # returns text written by other people (ticket messages, notes): a default read only


class Read(Contract):
    """One default read: arguments come from the task's entities (or fixed values), never from the model."""
    tool: ToolName
    entities: dict[str, str] = {}  # tool argument -> entity name; a missing entity leaves the tool's default
    fixed: dict[str, JsonValue] = {}

    @model_validator(mode='after')
    def _one_source(self):
        if set(self.entities) & set(self.fixed):raise ValueError('an argument comes from an entity or a fixed value, not both')
        return self


class AgentSpec(Contract):
    name: TargetAgent
    role: Literal['public', 'client', 'rm']
    scope: Literal['public', 'own_customer', 'rm_book']
    answers: str  # what it handles; builds the planner prompt
    tools: dict[ToolName, ToolView]
    intents: dict[str, tuple[Read, ...]]  # intent -> default reads (option B, docs/planning/agent-decisions.md)
    loop_tools: frozenset[ToolName] = frozenset()  # tools an `open` task may choose
    external: frozenset[str] = frozenset()  # external tools (phase 2): public_holidays, fx_rate; usable in reads and loop_tools
    exports: dict[str, str] = {}  # name -> EXPORT_TYPES name, computed by code (agents/derived.py), never by the model
    sort_by: dict[str, str] = {}  # list export of objects -> the field a fan-out sorts on (a list of dates sorts by value)
    imports: frozenset[str] = frozenset()
    hands_to: frozenset[TargetAgent] = frozenset()
    offers: frozenset[str] = frozenset()  # action types it may propose; every one needs confirmation
    prompt: str

    @model_validator(mode='after')
    def _consistent(self):
        reads = {read.tool for reads in self.intents.values() for read in reads}
        if missing := (reads | self.loop_tools) - set(self.tools) - self.external:raise ValueError(f'{self.name}: tools not in its spec: {sorted(missing)}')
        if text := sorted(t for t in self.loop_tools if t in self.tools and self.tools[t].free_text):raise ValueError(f'{self.name}: free-text loop tools {text}')
        if OPEN_INTENT in self.intents:raise ValueError(f'{self.name}: {OPEN_INTENT!r} is not a listed intent')
        if self.scope == 'public' and self.offers:raise ValueError(f'{self.name}: a public agent makes no offers')
        if bad := sorted(n for n, t in self.exports.items() if t not in EXPORT_TYPES):raise ValueError(f'{self.name}: unknown export types {bad}')
        if bad := sorted(n for n, t in self.exports.items() if t.startswith('list[') and t != 'list[date]' and n not in self.sort_by):
            raise ValueError(f'{self.name}: list exports without a sort field {bad}')
        return self


class PlanTask(Contract):
    id: str
    agent: TargetAgent
    intent: str  # one of the agent's intents, or OPEN_INTENT
    question: str
    entities: dict[str, JsonValue] = {}
    priority: Priority = 'P3'
    # Phase 7 (§14.6): wave 2 runs after `depends_on`; `condition` names a bool export, `for_each` a list export of it.
    depends_on: str | None = None
    condition: str | None = None
    for_each: str | None = None
    rerouted: bool = False  # handed to another agent once already (question only)

    @model_validator(mode='after')
    def _dependency(self):
        if (self.condition or self.for_each) and not self.depends_on:raise ValueError('condition and for_each need depends_on')
        if self.depends_on == self.id:raise ValueError('a task cannot depend on itself')
        return self


class Export(Contract):
    value: JsonValue
    source_ids: list[str] = Field(min_length=1)  # provenance, so a receiving agent's validator accepts it


class Offer(Contract):
    offer_id: UUID
    type: str
    target: dict[str, Any] = {}
    summary: str
    expires_at: datetime
    after_offer: UUID | None = None


class OfferProposal(Contract):
    """One action an agent offers, before services/offers.save gives it an offer_id. The target comes from code-read
    records, never from the model; nothing runs until the client confirms."""
    task_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    agent: TargetAgent
    type: str
    target: dict[str, JsonValue] = {}
    summary: str = Field(min_length=1, max_length=300)
    chained: bool = False  # runs after the offer before it in this reply and receives its receipt (after_receipt)


class AgentOutput(Contract):
    task_id: str
    agent: TargetAgent
    status: Literal['ok', 'needs_clarification', 'needs_human', 'out_of_scope', 'error']
    result: AgentResult | None = None  # the section; None when nothing may be shown
    evidence: list[dict[str, JsonValue]] = []  # Evidence dumps with their sources, for the section's citations
    reads: list[dict[str, JsonValue]] = []  # what was read: tool, args, evidence ids (runner)
    exports: dict[str, Export] = {}
    decision: str | None = None  # fixed text from code shown after the answer (e.g. the charge list to confirm)
    proposals: list[OfferProposal] = []  # saved as offers by compose
    offers: list[Offer] = []
    origin: Literal['ai', 'safety', 'validation'] = 'ai'
    prompt_id: str | None = None
    suggested: TargetAgent | None = None  # out_of_scope only: the agent to hand the question to
    reply: dict[str, JsonValue] | None = None  # RM agents (phase 9): the structured RM workspace reply, composed by services/rm/graph.py

    @model_validator(mode='after')
    def _handoff(self):
        if self.suggested is not None and self.status != 'out_of_scope':raise ValueError('suggested is only valid for out_of_scope')
        return self



# AuditEvent: one event as the API sends it to public.audit_record_batch (§6.3, migration 0005). event_id, occurred_at,
# customer, hashes and (unless an agent is named) the actor are filled in by the database from the caller's JWT.
class AuditEvent(Contract):
    category: AuditCategory
    action: str = Field(pattern=r'^[a-z_]+\.[a-z_]+$')  # <category>.<verb>, e.g. chat.reply_sent
    outcome: Literal['success', 'denied', 'failed'] = 'success'
    resource_type: str | None = Field(default=None, pattern=r'^[a-z_.]{1,64}$')
    resource_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.:-]{1,128}$')
    details: dict[str, Any] = {}  # ids, enums and counts only (services/audit.is_safe)
    case_id: UUID | None = None
    session_id: UUID | None = None
    request_id: UUID | None = None
    actor_type: Literal['agent'] | None = None
    actor_id: str | None = Field(default=None, pattern=r'^[a-z0-9_.:-]{1,64}$')

    @model_validator(mode='after')
    def _consistent(self):
        if self.action.split('.')[0] != self.category:
            raise ValueError('action must start with its category')
        if (self.actor_type is None) != (self.actor_id is None):
            raise ValueError('actor_type and actor_id go together')
        return self

# Supporting service results used by the stubs (§5.7)
class Decision(Contract):  # decide() in Authorisation & Consent
    allow: bool
    reasons: list[str] = []
    obligations: list[str] = []  # e.g. mask:pan, require:confirmation, require:consent:rm_brief


class ToolResult(Contract):  # every Customer Data read
    call_id: UUID = Field(default_factory=uuid4)
    data: Any
    as_of: datetime = Field(default_factory=now)


class KbChunk(Contract):  # kb.search() result
    cite: str  # article_id@vN#anchor
    title: str
    heading: str
    body: str
    score: float


