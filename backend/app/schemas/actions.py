"""Validated ledger records; only durable successful receipts establish an executed action."""
import re
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints,ValidationInfo,field_validator,model_validator
from app.schemas.contracts import Priority
from app.schemas.events import WorkflowStatus

ActionType=Literal['card.temporary_block','handoff.create']
ActionState=Literal['proposed','confirmed','succeeded','failed','expired','rejected']
CardId=Annotated[str,StringConstraints(strict=True,pattern=r'^card_[1-9][0-9]{0,14}$')]
TaskId=Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Za-z0-9_-]{1,40}$')]


class CardBlockTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: CardId


class StoredCardTarget(CardBlockTarget):
    last4: Annotated[str,StringConstraints(strict=True,pattern=r'^[0-9]{4}$')]


class HandoffTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    reason: Literal['card_verification','general_support']
    priority: Priority
    summary: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=1000,pattern=r'\S')]

    @field_validator('summary')
    @classmethod
    def no_credentials(cls,value: str) -> str:
        if re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+',value):
            raise ValueError('Remove credentials from the handoff summary')
        return value


class CardBlockResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: CardId
    status: Literal['temporarily_blocked']


class HandoffResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    handoff_id: UUID
    priority: Priority
    status: Literal['open','claimed']
    created: StrictBool


class ActionFailure(BaseModel):
    model_config=ConfigDict(extra='ignore')
    error_code: Literal['target_changed']


class ActionRecord(BaseModel):
    model_config=ConfigDict(extra='ignore')
    command_id: UUID
    customer_id: StrictInt
    case_id: UUID
    type: ActionType
    target: StoredCardTarget | HandoffTarget
    idempotency_key: str
    state: ActionState
    proposed_at: AwareDatetime
    expires_at: AwareDatetime
    confirmed_at: AwareDatetime | None

    @model_validator(mode='after')
    def consistent_target(self) -> Self:
        if (self.type=='card.temporary_block')!=isinstance(self.target,StoredCardTarget):raise ValueError('Action type and target disagree')
        if self.expires_at<=self.proposed_at:raise ValueError('Action expiry must follow its proposal time')
        if self.state=='succeeded' and self.type=='card.temporary_block' and self.confirmed_at is None:
            raise ValueError('A synthetic card block requires recorded confirmation')
        return self


class ActionReceipt(BaseModel):
    model_config=ConfigDict(extra='ignore')
    receipt_id: Annotated[str,StringConstraints(strict=True,pattern=r'^rcpt_[a-f0-9]{32}$')]
    command_id: UUID
    customer_id: StrictInt
    outcome: Literal['succeeded','failed']
    result: CardBlockResult | HandoffResult | ActionFailure
    audit_event_id: int=Field(strict=True,gt=0)
    created_at: AwareDatetime


class ActionOutcome(BaseModel):
    model_config=ConfigDict(extra='ignore')
    command: ActionRecord
    receipt: ActionReceipt | None
    workflow: WorkflowStatus | None=None

    @model_validator(mode='after')
    def consistent_receipt(self) -> Self:
        command,receipt=self.command,self.receipt
        if self.workflow is not None and (self.workflow.case_id!=command.case_id or self.workflow.customer_id!=command.customer_id):
            raise ValueError('Action and workflow belong to different scopes')
        if (command.state in ('succeeded','failed'))!=(receipt is not None):raise ValueError('Terminal execution requires a persisted receipt')
        if receipt is not None:
            if receipt.command_id!=command.command_id or receipt.customer_id!=command.customer_id or receipt.outcome!=command.state:
                raise ValueError('Receipt belongs to another command, customer or outcome')
            if receipt.outcome=='failed' and not isinstance(receipt.result,ActionFailure):raise ValueError('Failed receipts require an explicit reason')
            if receipt.outcome=='succeeded':
                if command.type=='card.temporary_block' and (not isinstance(receipt.result,CardBlockResult) or receipt.result.card_id!=command.target.card_id):
                    raise ValueError('Card receipt does not match its proposed target')
                if command.type=='handoff.create' and not isinstance(receipt.result,HandoffResult):raise ValueError('Handoff receipt requires a stored handoff')
        return self


class CreateAction(BaseModel):
    model_config=ConfigDict(extra='ignore')
    command_id: UUID
    case_id: UUID
    task_id: TaskId
    type: ActionType
    target: CardBlockTarget | HandoffTarget

    @field_validator('target',mode='before')
    @classmethod
    def target_for_type(cls,value: object,info: ValidationInfo) -> CardBlockTarget | HandoffTarget:
        if info.data.get('type')=='card.temporary_block':return CardBlockTarget.model_validate(value)
        if info.data.get('type')=='handoff.create':return HandoffTarget.model_validate(value)
        raise ValueError('A registered action type is required before its target')

    @model_validator(mode='after')
    def consistent_target(self) -> Self:
        if (self.type=='card.temporary_block')!=isinstance(self.target,CardBlockTarget):raise ValueError('Action type and target disagree')
        return self
