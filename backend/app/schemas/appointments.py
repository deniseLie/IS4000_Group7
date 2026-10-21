"""Thirty-minute synthetic appointment targets, immutable receipt snapshots and current records."""
from datetime import timedelta
from typing import Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,model_validator

AppointmentFormat=Literal['video','phone','in_person']


class SlotTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    advisor_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    format: AppointmentFormat

    @model_validator(mode='after')
    def duration(self) -> Self:
        if self.ends_at-self.starts_at!=timedelta(minutes=30):raise ValueError('An appointment must last thirty minutes')
        return self


class AppointmentTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    appointment_id: UUID


class StoredAppointmentTarget(SlotTarget,AppointmentTarget):
    hold_expires_at: AwareDatetime | None


class AppointmentResult(StoredAppointmentTarget):
    status: Literal['held','confirmed','cancelled']

    @model_validator(mode='after')
    def held_expiry(self) -> Self:
        if self.status=='held' and self.hold_expires_at is None:raise ValueError('A held slot requires its server expiry')
        return self


class AppointmentRecord(StoredAppointmentTarget):
    status: Literal['held','confirmed','cancelled','expired']
    case_id: UUID | None

    @model_validator(mode='after')
    def held_expiry(self) -> Self:
        if self.status=='held' and self.hold_expires_at is None:raise ValueError('A held appointment requires its server expiry')
        return self


class HoldAppointment(BaseModel):
    model_config=ConfigDict(extra='ignore')
    command_id: UUID
    session_id: UUID
    case_id: UUID
    target: SlotTarget
