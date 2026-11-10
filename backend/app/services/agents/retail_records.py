"""Own retail snapshots from the audited tools (tools.call: policy, caller JWT and RLS, MCP); no sample or cached customer data.
Whole records within the Accounts spec's tools and limits (whole=True): the answers match cards and transactions by id,
and the evidence for the model goes through the field views (retail.evidence_for)."""
from datetime import datetime,timezone
from app.schemas.contracts import RequestContext
from app.schemas.retail import RetailRecords
from app.services import tools


async def load(ctx: RequestContext) -> RetailRecords:
    """Her profile, accounts, cards, loans and the last 90 days of transactions (the latest 100), each one audited tool read."""
    profile=await tools.call('my_profile',{},ctx,whole=True)
    accounts=await tools.call('my_accounts',{},ctx,whole=True)
    cards=await tools.call('my_cards',{},ctx,whole=True)
    loans=await tools.call('my_loans',{},ctx,whole=True)
    transactions=await tools.call('my_transactions',{'days':90},ctx,whole=True)
    return RetailRecords.model_validate({'profile':profile['data'],'accounts':accounts['items'],'cards':cards['items'],
        'loans':loans['items'],'transactions':transactions['items'],'captured_at':datetime.now(timezone.utc)})
