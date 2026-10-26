"""Separate audited relationship snapshots; suggestions never constitute reservations."""
from typing import Annotated,Self
from zoneinfo import ZoneInfo
from pydantic import BaseModel,ConfigDict,Field,model_validator
from app.schemas.appointments import AppointmentRecord
from app.schemas.customer_data import CustomerEnvelope,RelationshipAssignment
from app.schemas.relationship import AvailableSlot,RelationshipRequest,SlotsQuery
from app.schemas.relationship_intro import Introduction


class RelationshipRecords(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignment: CustomerEnvelope[RelationshipAssignment | None]
    requests: CustomerEnvelope[Annotated[list[RelationshipRequest],Field(max_length=200)]]
    appointments: CustomerEnvelope[Annotated[list[AppointmentRecord],Field(max_length=200)]]
    slots: CustomerEnvelope[Annotated[list[AvailableSlot],Field(max_length=20)]]
    search: SlotsQuery
    introduction: CustomerEnvelope[Introduction] | None=None

    @model_validator(mode='after')
    def consistent_scope(self) -> Self:
        cid=self.assignment.customer_id
        if any(snapshot.customer_id!=cid for snapshot in [self.requests,self.appointments,self.slots,*([self.introduction] if self.introduction else [])]):
            raise ValueError('Relationship snapshots must belong to one authenticated customer')
        if any(row.customer_id!=cid for row in [*self.requests.data,*self.slots.data]):
            raise ValueError('Relationship rows do not match their customer snapshot')
        identifiers=[[row.request_id for row in self.requests.data],[row.appointment_id for row in self.appointments.data],
            [(row.advisor_id,row.starts_at) for row in self.slots.data]]
        if any(len(values)!=len(set(values)) for values in identifiers):raise ValueError('Relationship snapshots contain duplicate records')
        if len(self.slots.data)>self.search.limit:raise ValueError('Available slots exceed their requested limit')
        if any(not self.search.from_date<=row.starts_at.astimezone(ZoneInfo('Asia/Singapore')).date()<=self.search.to_date for row in self.slots.data):
            raise ValueError('Available slots fall outside the recorded Singapore-date search')
        current=self.assignment.data.current if self.assignment.data else None
        if any(current is None or row.advisor_id!=current.rm_id for row in self.slots.data):
            raise ValueError('Available slots do not match the current relationship manager snapshot; reload the records')
        return self
