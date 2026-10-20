"""Validated ledger records; only durable successful receipts establish an executed action."""
import re
from typing import Annotated,Literal,Self,TypeAlias
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints,ValidationInfo,field_validator,model_validator
from app.schemas.contracts import Priority
from app.schemas.events import WorkflowStatus
from app.schemas.appointments import AppointmentResult,AppointmentTarget,SlotTarget,StoredAppointmentTarget
from app.schemas.agendas import AgendaSaveResult,AgendaSaveTarget,StoredAgendaSaveTarget
from app.schemas.sharing import AgendaShareResult,AgendaShareTarget,StoredAgendaShareTarget
from app.schemas.checklist import ChecklistResult,ChecklistTarget,StoredChecklistTarget
from app.schemas.request_actions import RequestResult,RequestTarget,StoredRequestTarget
from app.schemas.consent_actions import (ConsentGrantResult,ConsentGrantTarget,ConsentRequestResult,ConsentRequestTarget,
    ConsentRevokeResult,ConsentRevokeTarget,StoredConsentGrantTarget,StoredConsentRequestTarget,StoredConsentRevokeTarget)

CustomerActionType=Literal['card.temporary_block','handoff.create','appointment.hold','appointment.confirm','appointment.cancel',
    'consent.request','consent.grant','consent.revoke','agenda.save','rm.share_summary','compliance.update_checklist_item','requests.create']
StaffActionType=Literal['card.unblock','card.cancel_permanently','handoff.claim','handoff.close']
ActionType=CustomerActionType | StaffActionType
ActionState=Literal['proposed','confirmed','succeeded','failed','expired','rejected']
CardId=Annotated[str,StringConstraints(strict=True,pattern=r'^card_[1-9][0-9]{0,14}$')]
TaskId=Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Za-z0-9_-]{1,40}$')]


class CardBlockTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: CardId


class StoredCardTarget(CardBlockTarget):
    last4: Annotated[str,StringConstraints(strict=True,pattern=r'^[0-9]{4}$')]


class StaffHandoffTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    handoff_id: UUID


class StaffCardTarget(StoredCardTarget):
    handoff_id: UUID


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


class StaffCardResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    card_id: CardId
    status: Literal['active','cancelled']


class StaffHandoffResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    handoff_id: UUID
    status: Literal['claimed','closed']


class HandoffResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    handoff_id: UUID
    priority: Priority
    status: Literal['open','claimed']
    created: StrictBool


class ActionFailure(BaseModel):
    model_config=ConfigDict(extra='ignore')
    error_code: Literal['target_changed']


StoredTarget: TypeAlias=(StoredCardTarget | HandoffTarget | StaffCardTarget | StaffHandoffTarget | SlotTarget | StoredAppointmentTarget
    | StoredConsentRequestTarget | StoredConsentGrantTarget | StoredConsentRevokeTarget | StoredAgendaSaveTarget | StoredAgendaShareTarget | StoredChecklistTarget | StoredRequestTarget)
InputTarget: TypeAlias=(CardBlockTarget | HandoffTarget | SlotTarget | AppointmentTarget
    | ConsentRequestTarget | ConsentGrantTarget | ConsentRevokeTarget | AgendaSaveTarget | AgendaShareTarget | ChecklistTarget | RequestTarget)


class ActionRecord(BaseModel):
    model_config=ConfigDict(extra='ignore')
    command_id: UUID
    customer_id: StrictInt
    case_id: UUID
    type: ActionType
    target: StoredTarget
    idempotency_key: str
    state: ActionState
    proposed_at: AwareDatetime
    expires_at: AwareDatetime
    confirmed_at: AwareDatetime | None

    @field_validator('target',mode='before')
    @classmethod
    def target_for_type(cls,value: object,info: ValidationInfo) -> StoredTarget:
        action_type=info.data.get('type')
        if action_type=='card.temporary_block':return StoredCardTarget.model_validate(value)
        if action_type=='handoff.create':return HandoffTarget.model_validate(value)
        if action_type in ('card.unblock','card.cancel_permanently'):return StaffCardTarget.model_validate(value)
        if action_type in ('handoff.claim','handoff.close'):return StaffHandoffTarget.model_validate(value)
        if action_type=='appointment.hold':return SlotTarget.model_validate(value)
        if action_type in ('appointment.confirm','appointment.cancel'):return StoredAppointmentTarget.model_validate(value)
        if action_type=='consent.request':return StoredConsentRequestTarget.model_validate(value)
        if action_type=='consent.grant':return StoredConsentGrantTarget.model_validate(value)
        if action_type=='consent.revoke':return StoredConsentRevokeTarget.model_validate(value)
        if action_type=='agenda.save':return StoredAgendaSaveTarget.model_validate(value)
        if action_type=='rm.share_summary':return StoredAgendaShareTarget.model_validate(value)
        if action_type=='compliance.update_checklist_item':return StoredChecklistTarget.model_validate(value)
        if action_type=='requests.create':return StoredRequestTarget.model_validate(value)
        raise ValueError('A registered operation must identify its stored target')

    @model_validator(mode='after')
    def consistent_target(self) -> Self:
        if self.expires_at<=self.proposed_at:raise ValueError('Action expiry must follow its proposal time')
        if self.state=='succeeded' and self.type=='card.temporary_block' and self.confirmed_at is None:
            raise ValueError('A synthetic card block requires recorded confirmation')
        if self.state=='succeeded' and self.type in ('appointment.confirm','appointment.cancel') and self.confirmed_at is None:
            raise ValueError('Appointment confirmation/cancellation requires recorded customer confirmation')
        if self.state=='succeeded' and self.type in ('consent.grant','consent.revoke') and self.confirmed_at is None:
            raise ValueError('Consent changes require recorded customer confirmation')
        if self.state=='succeeded' and self.type=='requests.create' and self.confirmed_at is None:
            raise ValueError('Request intake requires recorded customer confirmation')
        if self.state=='succeeded' and self.type=='rm.share_summary' and self.confirmed_at is None:
            raise ValueError('Agenda sharing requires recorded customer confirmation')
        if self.type in ('card.unblock','card.cancel_permanently','handoff.claim','handoff.close') and (self.state not in ('succeeded','failed') or self.confirmed_at is None):
            raise ValueError('Staff operations require an immediately recorded terminal result')
        return self


class ActionReceipt(BaseModel):
    model_config=ConfigDict(extra='ignore')
    receipt_id: Annotated[str,StringConstraints(strict=True,pattern=r'^rcpt_[a-f0-9]{32}$')]
    command_id: UUID
    customer_id: StrictInt
    outcome: Literal['succeeded','failed']
    result: CardBlockResult | HandoffResult | StaffCardResult | StaffHandoffResult | ActionFailure | AppointmentResult | ConsentRequestResult | ConsentGrantResult | ConsentRevokeResult | AgendaSaveResult | AgendaShareResult | ChecklistResult | RequestResult
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
                if command.type in ('card.unblock','card.cancel_permanently'):
                    status='active' if command.type=='card.unblock' else 'cancelled'
                    if not isinstance(receipt.result,StaffCardResult) or receipt.result.card_id!=command.target.card_id or receipt.result.status!=status:
                        raise ValueError('Staff card receipt disagrees with its recorded target or operation')
                if command.type in ('handoff.claim','handoff.close'):
                    status='claimed' if command.type=='handoff.claim' else 'closed'
                    if not isinstance(receipt.result,StaffHandoffResult) or receipt.result.handoff_id!=command.target.handoff_id or receipt.result.status!=status:
                        raise ValueError('Staff handoff receipt disagrees with its recorded target or operation')
                if command.type.startswith('appointment.'):
                    expected={'appointment.hold':'held','appointment.confirm':'confirmed','appointment.cancel':'cancelled'}[command.type]
                    if not isinstance(receipt.result,AppointmentResult) or receipt.result.status!=expected:
                        raise ValueError('Appointment receipt disagrees with its operation')
                    target=command.target.model_dump()
                    if any(receipt.result.model_dump()[key]!=value for key,value in target.items()):
                        raise ValueError('Appointment receipt disagrees with its recorded target')
                if command.type.startswith('consent.'):
                    expected={'consent.request':ConsentRequestResult,'consent.grant':ConsentGrantResult,'consent.revoke':ConsentRevokeResult}[command.type]
                    if not isinstance(receipt.result,expected):raise ValueError('Consent receipt disagrees with its operation')
                    if any(receipt.result.model_dump()[key]!=value for key,value in command.target.model_dump().items()):
                        raise ValueError('Consent receipt disagrees with the reviewed scope')
                if command.type=='requests.create':
                    if not isinstance(receipt.result,RequestResult) or any(receipt.result.model_dump()[key]!=value for key,value in command.target.model_dump().items()):
                        raise ValueError('Request receipt disagrees with the reviewed intake')
                if command.type=='compliance.update_checklist_item':
                    if not isinstance(receipt.result,ChecklistResult) or any(receipt.result.model_dump()[key]!=value for key,value in command.target.model_dump().items()):
                        raise ValueError('Checklist receipt disagrees with the reviewed preparation mark')
                if command.type=='agenda.save':
                    if not isinstance(receipt.result,AgendaSaveResult) or any(receipt.result.model_dump()[key]!=value for key,value in command.target.model_dump().items()):
                        raise ValueError('Agenda receipt disagrees with the saved draft')
                if command.type=='rm.share_summary':
                    if not isinstance(receipt.result,AgendaShareResult) or any(receipt.result.model_dump()[key]!=value for key,value in command.target.model_dump().items()):
                        raise ValueError('Sharing receipt disagrees with the reviewed saved snapshot')
                    if not set(receipt.result.categories)<=set(receipt.result.consent_categories):
                        raise ValueError('A sharing receipt requires one consent grant covering every category')
                    if receipt.result.shared_at<command.confirmed_at:
                        raise ValueError('Sharing cannot precede its recorded customer confirmation')
                    if receipt.result.grant_expires_at is not None and receipt.result.shared_at>=receipt.result.grant_expires_at:
                        raise ValueError('Sharing must occur before the reviewed consent expiry')
        return self


class CreateAction(BaseModel):
    model_config=ConfigDict(extra='ignore')
    command_id: UUID
    case_id: UUID
    task_id: TaskId
    type: CustomerActionType
    target: InputTarget

    @field_validator('target',mode='before')
    @classmethod
    def target_for_type(cls,value: object,info: ValidationInfo) -> InputTarget:
        if info.data.get('type')=='card.temporary_block':return CardBlockTarget.model_validate(value)
        if info.data.get('type')=='handoff.create':return HandoffTarget.model_validate(value)
        if info.data.get('type')=='appointment.hold':return SlotTarget.model_validate(value)
        if info.data.get('type') in ('appointment.confirm','appointment.cancel'):return AppointmentTarget.model_validate(value)
        if info.data.get('type')=='consent.request':return ConsentRequestTarget.model_validate(value)
        if info.data.get('type')=='consent.grant':return ConsentGrantTarget.model_validate(value)
        if info.data.get('type')=='consent.revoke':return ConsentRevokeTarget.model_validate(value)
        if info.data.get('type')=='agenda.save':return AgendaSaveTarget.model_validate(value)
        if info.data.get('type')=='rm.share_summary':return AgendaShareTarget.model_validate(value)
        if info.data.get('type')=='compliance.update_checklist_item':return ChecklistTarget.model_validate(value)
        if info.data.get('type')=='requests.create':return RequestTarget.model_validate(value)
        raise ValueError('A registered action type is required before its target')

    @model_validator(mode='after')
    def consistent_target(self) -> Self:
        if (self.type=='card.temporary_block')!=isinstance(self.target,CardBlockTarget):raise ValueError('Action type and target disagree')
        return self


class CreateActionCase(BaseModel):
    model_config=ConfigDict(extra='ignore')
    session_id: UUID
    case_id: UUID


class ActionCase(CreateActionCase):
    customer_id: StrictInt
