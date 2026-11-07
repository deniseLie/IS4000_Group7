"""Client-safe proposal input and versioned documents. Financial terms are copied from SQL catalogue rows by code."""
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime,Field,model_validator
from app.schemas.meeting_lifecycle import Command,Hash,Text
from app.schemas.meeting_preparation import HiddenInput

AgendaCode=Literal['goals','status','documents','options','next_steps','questions']
PackQuestion=Literal['clarify_objective','confirm_documents','review_options','confirm_next_steps','ask_product_risks','confirm_timeframe']

class StatedAmount(HiddenInput):
    label: Text=Field(max_length=80)
    currency: str=Field(pattern=r'^[A-Z]{3}$')
    amount: Decimal=Field(ge=0,max_digits=16,decimal_places=2)

class PackRequest(Command):
    amounts: tuple[StatedAmount,...]=Field(max_length=5)

class CatalogueTerm(HiddenInput):
    item_id: UUID
    code: Text
    name: Text
    topic: Text
    version: int=Field(strict=True,gt=0)
    currency: str=Field(pattern=r'^[A-Z]{3}$')
    annual_rate: Decimal|None
    minimum_amount: Decimal|None
    fee: Decimal|None
    term_months: int|None
    effective_from: date
    effective_to: date
    status: Literal['approved','retired']

class PackInputs(HiddenInput):
    meeting_id: UUID
    revision: int=Field(strict=True,gt=0)
    purpose: str|None
    topics: tuple[str,...]
    catalogue: tuple[CatalogueTerm,...]=Field(max_length=100)
    amounts: tuple[StatedAmount,...]=Field(max_length=5)
    source_hash: Hash
    as_of: AwareDatetime

class ProductChoice(HiddenInput):
    item_id: UUID
    name: str
    topic: str
    currency: str

class ProposalContext(HiddenInput):
    purpose: str|None
    topics: tuple[str,...]
    catalogue: tuple[ProductChoice,...]
    amount_currencies: tuple[str,...]

class PackPlan(HiddenInput):
    agenda: tuple[AgendaCode,...]=Field(min_length=1,max_length=6)
    questions: tuple[PackQuestion,...]=Field(max_length=6)
    product_ids: tuple[UUID,...]=Field(max_length=5)

    @model_validator(mode='after')
    def distinct(self) -> 'PackPlan':
        if any(len(values)!=len(set(values)) for values in (self.agenda,self.questions,self.product_ids)):
            raise ValueError('Pack agenda, questions and catalogue items must be distinct')
        return self

class PackContent(HiddenInput):
    kind: Literal['meeting_pack']
    title: Text
    purpose: str|None
    topics: tuple[str,...]
    agenda: tuple[AgendaCode,...]
    questions: tuple[PackQuestion,...]
    products: tuple[CatalogueTerm,...]
    stated_amounts: tuple[StatedAmount,...]
    as_of: AwareDatetime

class StoredPack(HiddenInput):
    document_id: UUID
    meeting_id: UUID
    version: int=Field(strict=True,gt=0)
    revision: int=Field(strict=True,gt=0)
    source_hash: Hash
    content_hash: Hash
    plan: PackPlan
    content: PackContent
    amounts: tuple[StatedAmount,...]
    status: Literal['draft','approved','shared']
    approved_at: AwareDatetime|None
    shared_at: AwareDatetime|None
    created_at: AwareDatetime

class PackView(StoredPack):
    sources_changed: bool

class PackApproval(Command):
    document_id: UUID
    version: int=Field(strict=True,gt=0)
    revision: int=Field(strict=True,gt=0)
    content_hash: Hash

class PackGraphState(HiddenInput):
    meeting_id: UUID
    plan: PackPlan|None
    document_id: UUID|None
