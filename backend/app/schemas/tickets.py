"""Ticket Service (F19) and request categories (F20): customer-raised tickets go through the Action Service.

The chatbot guides and records; the owner team decides. Status changes come only from staff or linked
handoffs/RM requests (migration 0071). Customer reads never include internal notes.
"""
import re
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints,field_validator

TicketStatus=Literal['submitted','in_review','waiting_on_client','resolved','closed']
CategoryKey=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z_]{3,40}$')]
Summary=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=500,pattern=r'\S')]
Reference=Annotated[str,StringConstraints(strict=True,pattern=r'^TCK-[0-9]{6,}$')]
CREDENTIALS=re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+')


class TicketTarget(BaseModel):
    """What the customer asks for. SQL checks the category's required fields, formats and card ownership."""
    model_config=ConfigDict(extra='ignore')
    category: CategoryKey
    summary: Summary
    urgency: Literal['low','normal','high']
    details: dict[Annotated[str,StringConstraints(pattern=r'^[a-z_]{2,30}$')],Annotated[str,StringConstraints(max_length=300)]]=Field(default_factory=dict,max_length=10)

    @field_validator('summary')
    @classmethod
    def no_credentials(cls,value: str) -> str:
        if CREDENTIALS.search(value):raise ValueError('Remove credentials from the request')
        return value


class StoredTicketTarget(TicketTarget):
    category_label: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=80)]
    owner_team: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=80)]
    decided_by: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=120)]
    target_hours: StrictInt=Field(ge=1,le=720)


class TicketResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    ticket_id: UUID
    reference: Reference
    category: CategoryKey
    status: TicketStatus
    owner_team: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=80)]
    target_at: AwareDatetime
    created: StrictBool


class CategoryField(BaseModel):
    model_config=ConfigDict(extra='ignore')
    key: Annotated[str,StringConstraints(pattern=r'^[a-z_]{2,30}$')]
    label: str
    kind: Literal['text','amount','date','date_past','choice','card']
    required: StrictBool
    choices: list[str] | None=None


class RequestCategory(BaseModel):
    model_config=ConfigDict(extra='ignore')
    category: CategoryKey
    version: StrictInt
    label: str
    description: str
    owner_team: str
    decided_by: str
    target_hours: StrictInt
    fields: list[CategoryField]=Field(max_length=10)
    documents: list[str]
    kb_article: str | None
    customer_raisable: StrictBool


class TicketUpdate(BaseModel):
    model_config=ConfigDict(extra='ignore')
    at: AwareDatetime
    status: TicketStatus
    note: str | None


class CustomerTicket(BaseModel):
    model_config=ConfigDict(extra='ignore')
    ticket_id: UUID
    reference: Reference
    category: CategoryKey
    category_label: str
    summary: str
    details: dict[str,str]
    urgency: Literal['low','normal','high','urgent']
    status: TicketStatus
    owner_team: str
    decided_by: str
    source: Literal['form','chat','handoff','rm_request','seed']
    handoff_id: UUID | None=None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    target_at: AwareDatetime
    overdue: StrictBool
    last_update: TicketUpdate | None
