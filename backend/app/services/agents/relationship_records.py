"""Load the caller's existing relationship services; runtime dates, no sample records or writes."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.schemas.appointments import AppointmentRecord
from app.schemas.contracts import RequestContext
from app.schemas.customer_data import CustomerEnvelope,RelationshipAssignment
from app.schemas.relationship import SlotsQuery
from app.schemas.relationship_records import RelationshipRecords
from app.services import customer_data


async def load(ctx: RequestContext) -> RelationshipRecords:
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    search=SlotsQuery(from_date=today,to_date=today+timedelta(days=13),limit=20)
    assignment=await customer_data.get_rm_assignment(ctx)
    requests=await customer_data.get_request_records(ctx)
    appointments=await customer_data.get_appointments(ctx)
    slots=await customer_data.get_available_slot_records(search,ctx)
    introduction=await customer_data.get_introduction_record(ctx)
    return RelationshipRecords(assignment=CustomerEnvelope[RelationshipAssignment | None](customer_id=ctx.principal.customer_id,
        call_id=assignment.call_id,as_of=assignment.as_of,data=assignment.data),requests=requests,
        appointments=CustomerEnvelope[list[AppointmentRecord]](customer_id=ctx.principal.customer_id,
            call_id=appointments.call_id,as_of=appointments.as_of,data=appointments.data),slots=slots,search=search,introduction=introduction)
