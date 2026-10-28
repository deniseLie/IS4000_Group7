"""Shared data contracts between pipeline modules (docs/architecture/system-architecture.md §4). Change only by PR reviewed by all developers."""
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Priority = Literal['P0', 'P1', 'P2', 'P3']  # P0 fraud > P1 security > P2 regulatory deadline > P3 everything else
Domain = Literal['fraud', 'retail', 'compliance', 'document', 'relationship', 'smalltalk']
AgentName = Literal['retail', 'fraud', 'compliance', 'document', 'relationship']
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
    agent: AgentName | None = None  # set only by backend dispatch; never read from tool arguments
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


class Fact(Contract):  # kb.get_fact() result
    value: float | str
    unit: str
    source_id: str
