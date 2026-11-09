"""Own retail snapshots from the audited tools (tools.call: policy, caller JWT and RLS, MCP); no sample or cached customer data."""
from datetime import datetime,timezone
from app.schemas.contracts import RequestContext
from app.schemas.retail import RetailRecords
from app.services import tools


async def load(ctx: RequestContext) -> RetailRecords:
    """Her profile, accounts, cards, loans and the last 90 days of transactions, each one audited tool read."""
    profile=await tools.call('my_profile',{},ctx)
    accounts=await tools.call('my_accounts',{},ctx)
    cards=await tools.call('my_cards',{},ctx)
    loans=await tools.call('my_loans',{},ctx)
    transactions=await tools.call('my_transactions',{'days':90},ctx)
    return RetailRecords.model_validate({'profile':profile['data'],'accounts':accounts['items'],'cards':cards['items'],
        'loans':loans['items'],'transactions':transactions['items'],'captured_at':datetime.now(timezone.utc)})
