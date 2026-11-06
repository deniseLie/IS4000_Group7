"""Relationship manager workspace (Persona 2, RM1-RM12, RM16): request bodies for the RM API and the RM assistant.

The RM assistant's reply is a structured object (sections of items, each item labelled as a recorded fact, something the
client stated, an AI suggestion or a gap, with the record ids it came from). Writes (documentation, tasks, service
requests, client messages) are separate requests the RM sends only after approving the exact content.
"""
from datetime import date,datetime
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,field_validator,model_validator

Intent=Literal['ticket_inbox','priorities','reviews','schedule','client_overview','meeting_history','meeting_brief',
    'ticket_summary','draft_message','document_interaction','actions_from_note','handover_brief','book_question','help',
    'meeting_requests','decide_request','availability_change',  # build plan 3a: the Calendar agent's intents
    'today']  # build plan 3b: her day, ranked by services/rm/priority.py
BOOK_WIDE: frozenset[str]=frozenset({'ticket_inbox','priorities','reviews','book_question','help','meeting_requests','availability_change','today'})
NEEDS_CLIENT: frozenset[str]=frozenset({'client_overview','meeting_history','meeting_brief','ticket_summary','draft_message',
    'document_interaction','actions_from_note','handover_brief'})
TICKET_REF=r'^TCK-[0-9]{6,}$'


class Model(BaseModel):
    model_config=ConfigDict(extra='forbid')


class RmTurn(Model):
    role: Literal['user','assistant']
    content: str=Field(max_length=6000)
    client_id: int|None=Field(default=None,gt=0)  # the active client when the turn happened; other clients' turns are dropped


class DraftState(Model):
    """The draft message currently on screen (RM9). Revisions never count as approval."""
    customer_id: int=Field(gt=0)
    reference: str|None=Field(default=None,pattern=TICKET_REF)
    text: str=Field(min_length=1,max_length=500)
    revision: int=Field(default=1,ge=1,le=50)


class DocumentationState(Model):
    """The interaction documentation in progress (RM5): the RM's own notes, her corrections and the current draft."""
    customer_id: int=Field(gt=0)
    source_notes: str=Field(min_length=1,max_length=8000)
    corrections: list[str]=Field(default=[],max_length=20)
    draft: dict|None=None
    revision: int=Field(default=1,ge=1,le=50)
    note_id: UUID|None=None  # set once the RM has approved and saved it
    appointment_id: UUID|None=None  # the meeting being documented ("Document this meeting"), checked in SQL when saved


class RmContext(Model):
    client_id: int|None=Field(default=None,gt=0)
    ticket_ref: str|None=Field(default=None,pattern=TICKET_REF)
    appointment_id: UUID|None=None
    note_id: UUID|None=None
    draft: DraftState|None=None
    documentation: DocumentationState|None=None


class RmAssistantRequest(Model):
    message: str=Field(min_length=1,max_length=8000)
    history: list[RmTurn]=Field(default=[],max_length=40)
    context: RmContext=RmContext()
    intent: Intent|None=None  # set by a workspace button (e.g. "Brief me" on an appointment); otherwise the planner decides


class InteractionApproval(Model):
    """RM5: the reviewed summary the RM approves. Saved only through this request."""
    request_key: UUID
    customer_id: int=Field(gt=0)
    met_on: date
    format: Literal['video','phone','in_person']
    title: str=Field(default='',max_length=160)
    summary: str=Field(min_length=1,max_length=2000)
    commitments: list[str]=Field(default=[],max_length=10)
    decisions: list[str]=Field(default=[],max_length=10)
    client_requests: list[str]=Field(default=[],max_length=10)
    source_notes: str=Field(min_length=1,max_length=8000)
    structured: dict={}
    revisions: list[dict]=Field(default=[],max_length=50)


class TaskDraft(Model):
    request_key: UUID
    title: str=Field(min_length=1,max_length=200)
    details: str=Field(default='',max_length=500)
    owner: str=Field(default='',max_length=80)
    due_on: date|None=None


class TasksApproval(Model):
    customer_id: int=Field(gt=0)
    note_id: UUID|None=None
    tasks: list[TaskDraft]=Field(min_length=1,max_length=10)


class TaskStatus(Model):
    status: Literal['done','cancelled','open']


class ServiceRequestDraft(Model):
    customer_id: int=Field(gt=0)
    category: str=Field(pattern=r'^[a-z_]{3,40}$')
    summary: str=Field(default='',max_length=500)
    details: dict[str,str]=Field(default={},max_length=10)
    urgency: Literal['low','normal','high']='normal'

    @field_validator('details')
    @classmethod
    def _short(cls,value: dict[str,str]) -> dict[str,str]:
        if any(len(v)>300 or len(k)>30 for k,v in value.items()):raise ValueError('details must be short text values')
        return {k:v.strip() for k,v in value.items() if v.strip()}


class ServiceRequestSubmit(ServiceRequestDraft):
    request_key: UUID
    client_verified: bool
    note_id: UUID|None=None


class ClientMessage(Model):
    """RM9: the exact text the RM approved. Only this version is sent; earlier drafts are not approved."""
    message_id: UUID
    customer_id: int=Field(gt=0)
    reference: str|None=Field(default=None,pattern=TICKET_REF)
    body: str=Field(min_length=1,max_length=500)
    status: Literal['in_review','waiting_on_client','resolved']|None=None
    draft_revision: int=Field(default=1,ge=1,le=50)
    edited: bool=False


class WorkingHours(Model):
    weekday: int=Field(ge=1,le=7)
    starts: str|None=Field(default=None,pattern=r'^\d\d:\d\d$')
    ends: str|None=Field(default=None,pattern=r'^\d\d:\d\d$')


class Block(Model):
    starts_at: datetime
    ends_at: datetime
    reason: str=Field(default='',max_length=80)


class MeetingDecision(Model):
    """Her answer to a client's meeting request (0106 rm_meeting_request_decide). The page makes decision_id once per open
    request card, so a double click or a retry records one decision."""
    decision_id: UUID
    decision: Literal['accept','counter','decline']
    starts_at: AwareDatetime|None=None  # accept: one of the client's preferred times; counter: another free time
    reason: Literal['answered_by_message','redirected','not_needed']|None=None  # decline only

    @model_validator(mode='after')
    def _fits(self):
        decline=self.decision=='decline'
        if decline and (self.reason is None or self.starts_at) or not decline and (self.starts_at is None or self.reason):
            raise ValueError('accept and counter need starts_at; decline needs a reason and no time')
        return self


class ConsentAsk(Model):
    """Ask the client to share a category she has not shared (0113 rm_ask_consent), approved on the copilot's card. The
    reason is a code with fixed wording for her; request_key is made once per card, so a retry records one ask."""
    request_key: UUID
    customer_id: int=Field(gt=0)
    category: Literal['transaction_insights','chat_topics']
    reason: Literal['meeting_prep','review','advice']
    meeting_id: UUID|None=None

    @model_validator(mode='after')
    def _meeting(self):
        if self.reason=='meeting_prep' and self.meeting_id is None:raise ValueError('meeting_prep needs the meeting')
        return self


class MeetingMove(Model):
    """Move an upcoming confirmed meeting (0106 rm_meeting_reschedule); change_id makes a retry record once."""
    change_id: UUID
    starts_at: AwareDatetime
    reason: Literal['client_asked','rm_unavailable','other']


class ConversationSave(Model):
    """A saved RM assistant conversation (0091). Turns keep the client they were about; full source records are not stored."""
    title: str=Field(min_length=1,max_length=120)
    messages: list[dict]=Field(max_length=80)
    context: dict={}


class Dismissal(Model):
    item_key: str=Field(pattern=r'^[a-z_]{1,30}:[A-Za-z0-9_.:-]{1,100}$')


class BriefPrepare(Model):
    """Brief Writer (0118): write a new version even when the saved one is current."""
    regenerate: bool=False


class BriefApproval(Model):
    """The RM approves the exact brief version she read (0118)."""
    brief_id: UUID
    version: int=Field(ge=1)
    command_id: UUID
