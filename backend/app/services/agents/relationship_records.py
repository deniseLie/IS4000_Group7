"""Load the caller's existing relationship services through the audited tools; runtime dates, no sample records or writes."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.schemas.contracts import RequestContext
from app.schemas.relationship import SlotsQuery
from app.schemas.relationship_records import RelationshipRecords
from app.services import tools


async def load(ctx: RequestContext) -> RelationshipRecords:
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    search=SlotsQuery(from_date=today,to_date=today+timedelta(days=13),limit=20)
    async def read(name: str,args: dict | None=None) -> dict:return tools.envelope(await tools.call(name,args or {},ctx),ctx)
    return RelationshipRecords.model_validate({'assignment':await read('my_rm'),'requests':await read('my_requests'),
        'appointments':await read('my_appointments'),'slots':await read('free_slots',search.model_dump(mode='json')),'search':search,
        'introduction':await read('my_introduction')})
