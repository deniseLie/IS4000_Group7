"""STUB Customer Data Service (architecture §5.7.2): fixed synthetic records for Alex, already masked.
customer_id always comes from RequestContext, never from model arguments. The real version queries Supabase
with the user's JWT (core/auth.py) so RLS applies."""
from datetime import date
from typing import Any
from app.schemas.contracts import RequestContext, ToolResult
from app.services.policy import decide

STUB = True

_PROFILE = {'name': 'Alex Tan', 'segment': 'retail', 'preferred_language': 'English'}
_ACCOUNTS = [{'account_id': 'acc_1', 'type': 'savings', 'number': '****6012', 'currency': 'SGD', 'balance': 5230.45}]
_TRANSACTIONS = [
    {'txn_id': 'txn_1', 'date': '2026-10-17', 'description': 'ONLINE MERCHANT 8831', 'amount': -2400.00, 'currency': 'SGD', 'status': 'posted', 'card_last4': '4821'},
    {'txn_id': 'txn_2', 'date': '2026-10-17', 'description': 'Transfer to landlord', 'amount': -1800.00, 'currency': 'SGD', 'status': 'pending', 'card_last4': None},
    {'txn_id': 'txn_3', 'date': '2026-10-15', 'description': 'Supermarket', 'amount': -64.20, 'currency': 'SGD', 'status': 'posted', 'card_last4': '4821'},
]
_CARDS = [{'card_id': 'card_1', 'last4': '4821', 'status': 'active', 'limit': 8000.00, 'outstanding': 2464.20, 'minimum_payment': 50.00, 'due_date': '2026-11-05'}]
_LOANS = [{'loan_id': 'loan_1', 'type': 'personal', 'principal': 10000.00, 'outstanding': 6200.00, 'rate_pct': 5.5, 'next_payment': '2026-11-01', 'next_amount': 320.00}]


def _read(ctx: RequestContext, tool: str, data: Any) -> ToolResult:
    if not decide(ctx.principal, tool, {'customer_id': ctx.principal.customer_id}, ctx).allow:
        raise PermissionError(tool)
    return ToolResult(data=data)


async def get_profile(ctx: RequestContext) -> ToolResult:
    return _read(ctx, 'customer.get_profile', dict(_PROFILE))


async def get_accounts(ctx: RequestContext) -> ToolResult:
    return _read(ctx, 'customer.get_accounts', [dict(a) for a in _ACCOUNTS])


async def get_transactions(ctx: RequestContext, from_: date | None = None, to: date | None = None, filters: dict[str, Any] | None = None) -> ToolResult:
    rows = [t for t in _TRANSACTIONS if (not from_ or t['date'] >= from_.isoformat()) and (not to or t['date'] <= to.isoformat())]
    rows = [t for t in rows if all(t.get(k) == v for k, v in (filters or {}).items())]
    return _read(ctx, 'customer.get_transactions', [dict(t) for t in rows])


async def get_credit_cards(ctx: RequestContext) -> ToolResult:
    return _read(ctx, 'customer.get_credit_cards', [dict(c) for c in _CARDS])


async def get_loans(ctx: RequestContext) -> ToolResult:
    return _read(ctx, 'customer.get_loans', [dict(l) for l in _LOANS])
