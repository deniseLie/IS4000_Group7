"""Own retail snapshots from permission-first JWT/RLS readers, through the tool layer; no sample or cached customer data."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from app.schemas.contracts import RequestContext
from app.schemas.retail import RetailRecords
from app.services import tools


async def load(ctx: RequestContext) -> RetailRecords:
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    profile=await tools.read('my_profile',{},ctx)
    accounts=await tools.read('my_accounts',{},ctx)
    cards=await tools.read('my_cards',{},ctx)
    loans=await tools.read('my_loans',{},ctx)
    transactions=await tools.read('my_transactions',{'from_date':(today-timedelta(days=90)).isoformat(),'to_date':today.isoformat()},ctx)
    return RetailRecords.model_validate({'profile':profile.data,'accounts':accounts.data,'cards':cards.data,
        'loans':loans.data,'transactions':transactions.data,'captured_at':datetime.now(timezone.utc)})
