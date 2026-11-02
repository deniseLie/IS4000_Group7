"""Relationship manager workspace (Persona 2, RM1-RM12, RM16): request bodies for the RM API and the RM assistant.

The RM assistant's reply is a structured object (sections of items, each item labelled as a recorded fact, something the
client stated, an AI suggestion or a gap, with the record ids it came from). Writes (documentation, tasks, service
requests, client messages) are separate requests the RM sends only after approving the exact content.
"""
from datetime import date,datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field,field_validator

Intent=Literal['ticket_inbox','priorities','reviews','schedule','client_overview','meeting_history','meeting_brief',
    'ticket_summary','draft_message','document_interaction','actions_from_note','handover_brief','help']
BOOK_WIDE: frozenset[str]=frozenset({'ticket_inbox','priorities','reviews','help'})
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


class ConversationSave(Model):
    """A saved RM assistant conversation (0091). Turns keep the client they were about; full source records are not stored."""
    title: str=Field(min_length=1,max_length=120)
    messages: list[dict]=Field(max_length=80)
    context: dict={}


class Dismissal(Model):
    item_key: str=Field(pattern=r'^[a-z_]{1,30}:[A-Za-z0-9_.:-]{1,100}$')
