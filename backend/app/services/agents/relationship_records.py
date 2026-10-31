"""Load the caller's existing relationship services; runtime dates, no sample records or writes."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.relationship import SlotsQuery
from app.schemas.relationship_records import RelationshipRecords
from app.services import tools


def owned(result: ToolResult,ctx: RequestContext,rows: bool=False) -> dict:
    """A tool result as a customer snapshot. The tool checked the rows' scope; its output omits their customer id."""
    cid=ctx.principal.customer_id
    return {'customer_id':cid,'call_id':result.call_id,'as_of':result.as_of,'data':[{**row,'customer_id':cid} for row in result.data] if rows else result.data}


async def load(ctx: RequestContext) -> RelationshipRecords:
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    search=SlotsQuery(from_date=today,to_date=today+timedelta(days=13),limit=20)
    assignment=await tools.read('my_relationship_manager',{},ctx)
    requests=await tools.read('my_requests',{},ctx)
    appointments=await tools.read('my_appointments',{},ctx)
    slots=await tools.read('free_slots',{'from_date':search.from_date.isoformat(),'to_date':search.to_date.isoformat(),'limit':search.limit},ctx)
    introduction=await tools.read('my_rm_introduction',{},ctx)
    return RelationshipRecords.model_validate({'assignment':owned(assignment,ctx),'requests':owned(requests,ctx,rows=True),
        'appointments':owned(appointments,ctx),'slots':owned(slots,ctx,rows=True),'search':search,'introduction':owned(introduction,ctx)})
