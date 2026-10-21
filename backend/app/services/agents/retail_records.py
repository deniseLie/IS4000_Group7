"""Own retail snapshots from permission-first JWT/RLS readers; no sample or cached customer data."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from app.schemas.contracts import RequestContext
from app.schemas.retail import RetailRecords
from app.services import customer_data


async def load(ctx: RequestContext) -> RetailRecords:
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    profile=await customer_data.get_profile(ctx)
    accounts=await customer_data.get_accounts(ctx)
    cards=await customer_data.get_credit_cards(ctx)
    loans=await customer_data.get_loans(ctx)
    transactions=await customer_data.get_transactions(ctx,today-timedelta(days=90),today,None)
    return RetailRecords.model_validate({'profile':profile.data,'accounts':accounts.data,'cards':cards.data,
        'loans':loans.data,'transactions':transactions.data,'captured_at':datetime.now(timezone.utc)})
