"""Relationship request reads and available thirty-minute appointment suggestions."""
from datetime import date,timedelta
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,model_validator

Nonblank=Annotated[str,StringConstraints(strict=True,min_length=1,pattern=r'\S')]


class RelationshipRequest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    customer_id: Annotated[StrictInt,Field(gt=0)]
    request_id: UUID
    advisor_id: UUID | None
    who: Nonblank
    what: Nonblank
    urgency: Literal['low','normal','high']
    deadline: date | None
    country: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{2}$')] | None
    currency: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{3}$')] | None
    status: Literal['sent','seen','answered','closed']
    created_at: AwareDatetime
    updated_at: AwareDatetime


class SlotsQuery(BaseModel):
    model_config=ConfigDict(extra='ignore')
    from_date: date
    to_date: date
    limit: Annotated[StrictInt,Field(ge=1,le=20)]

    @model_validator(mode='after')
    def bounded_range(self) -> Self:
        if not timedelta(0)<=self.to_date-self.from_date<=timedelta(days=13):
            raise ValueError('Appointment dates must be ordered and cover at most 14 days')
        return self


class AvailableSlot(BaseModel):
    model_config=ConfigDict(extra='ignore')
    customer_id: Annotated[StrictInt,Field(gt=0)]
    advisor_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode='after')
    def duration(self) -> Self:
        if self.ends_at-self.starts_at!=timedelta(minutes=30):raise ValueError('Appointment suggestions must last thirty minutes')
        return self
