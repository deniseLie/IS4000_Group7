"""Customer Data tool payloads. Required fields are validated; unrelated database fields never reach callers."""
from datetime import date
from typing import Annotated,Generic,Literal,Self,TypeVar
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictFloat,StrictInt,StringConstraints,model_validator
from app.schemas.actions import CardId
from app.schemas.fraud import Last4,TransactionId

Text=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000)]
Money=Annotated[StrictFloat,Field(allow_inf_nan=False)]
Currency=Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{3}$')]
T=TypeVar('T')


class CustomerEnvelope(BaseModel,Generic[T]):
    model_config=ConfigDict(extra='ignore')
    customer_id: Annotated[StrictInt,Field(gt=0)]
    call_id: UUID
    as_of: AwareDatetime
    data: T


class CustomerProfile(BaseModel):
    model_config=ConfigDict(extra='ignore')
    name: Text
    preferred_language: Text
    employment_status: Literal['employed','self_employed','student','retired','unemployed']


class BankAccount(BaseModel):
    model_config=ConfigDict(extra='ignore')
    account_id: Annotated[str,StringConstraints(strict=True,pattern=r'^acc_[1-9][0-9]{0,17}$')]
    type: Literal['savings','current','multi_currency']
    number: Annotated[str,StringConstraints(strict=True,pattern=r'^\*{4}[0-9]{4}$')]
    currency: Currency
    balance: Money
    status: Literal['active','dormant','closed']


class CreditCard(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: CardId
    name: Text
    network: Literal['visa','mastercard']
    last4: Last4
    status: Literal['active','temporarily_blocked','cancelled','blocked','expired']
    limit: Money
    outstanding: Money
    minimum_payment: Money
    due_date: date | None


class CustomerLoan(BaseModel):
    model_config=ConfigDict(extra='ignore')
    loan_id: Annotated[str,StringConstraints(strict=True,pattern=r'^loan_[1-9][0-9]{0,17}$')]
    type: Literal['home','car','personal','securities_backed']
    principal: Money
    outstanding: Money
    next_payment: date | None
    next_amount: Money
    status: Literal['active','paid_off']


class CustomerTransaction(BaseModel):
    model_config=ConfigDict(extra='ignore')
    txn_id: TransactionId
    date: date
    description: Text
    amount: Money
    currency: Currency
    status: Literal['posted','pending','failed']
    card_id: CardId | None
    card_last4: Last4 | None
    is_overseas: StrictBool
    failure_reason: Text | None

    @model_validator(mode='after')
    def card_reference(self) -> Self:
        if (self.card_id is None)!=(self.card_last4 is None):raise ValueError('Transaction card id and masked suffix must appear together')
        return self


class TransactionFilters(BaseModel):
    model_config=ConfigDict(extra='ignore')
    status: Literal['posted','pending','failed'] | None=None
    card_id: CardId | None=None
    txn_id: TransactionId | None=None
    currency: Currency | None=None
    is_overseas: StrictBool | None=None


class TransactionQuery(BaseModel):
    model_config=ConfigDict(extra='ignore')
    from_date: date | None
    to_date: date | None
    filters: TransactionFilters | None

    @model_validator(mode='after')
    def ordered_dates(self) -> Self:
        if self.from_date is not None and self.to_date is not None and self.from_date>self.to_date:
            raise ValueError('Transaction start date must not follow the end date')
        return self


ReviewStatus=Literal['review_opened','documents_requested','documents_partially_received','awaiting_client_documents','under_review','information_requested','completed','closed']


class RequestedItem(BaseModel):
    model_config=ConfigDict(extra='ignore')
    item_id: UUID
    label: Text
    instructions: Text | None
    check_guidance: Text | None=None
    submission_status: Literal['not_submitted','submitted_awaiting_review','accepted','rejected','unknown']
    client_mark: Literal['prepared','not_prepared']
    client_marked_at: AwareDatetime | None
    sort_order: StrictInt
    updated_at: AwareDatetime


class ReviewHistory(BaseModel):
    model_config=ConfigDict(extra='ignore')
    status: ReviewStatus
    note: Text | None
    changed_at: AwareDatetime


class InformationRequest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    request_id: UUID
    question: Text
    instructions: Text | None
    due_date: date | None
    status: Literal['open','responded','closed']
    responded_at: AwareDatetime | None
    created_at: AwareDatetime


class ComplianceCase(BaseModel):
    model_config=ConfigDict(extra='ignore')
    case_id: UUID
    case_type: Literal['periodic_review','additional_review']
    title: Text
    notice_text: Text
    notice_date: date
    due_date: date | None
    status: ReviewStatus
    status_updated_at: AwareDatetime
    requested_items: list[RequestedItem]=Field(max_length=200)
    status_history: list[ReviewHistory]=Field(max_length=200)
    info_requests: list[InformationRequest]=Field(max_length=200)


class AdvisorContact(BaseModel):
    model_config=ConfigDict(extra='ignore')
    email: Text | None
    phone: Text | None


class AdvisorAssignment(BaseModel):
    model_config=ConfigDict(extra='ignore')
    rm_id: UUID
    name: Text
    role: Text
    team: Text
    contact: AdvisorContact
    timezone: Text
    started_on: date
    ended_on: date | None
    transition_reason_code: Text | None


class RelationshipAssignment(BaseModel):
    model_config=ConfigDict(extra='ignore')
    current: AdvisorAssignment | None
    previous: AdvisorAssignment | None
