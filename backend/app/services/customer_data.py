"""Customer Data Service (architecture §5.7.2): read-only queries over the hosted banking, compliance and rm schemas.
Every query runs with the signed-in user's JWT so RLS applies, AND filters by customer_id in code so a policy
regression cannot leak another customer's rows. customer_id comes from RequestContext, never from model arguments.
The profile row is looked up again from Principal.user_id (auth user) through banking.customer_profiles, so this
service does not depend on the gateway's cached Principal.customer_id."""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from app.core import auth
from app.core.data_reads import read
from app.core.observability import log_event
from app.schemas.contracts import RequestContext, ToolResult
from app.services.authorization import require_permission

STUB = False
logger = logging.getLogger(__name__)
SGT = timezone(timedelta(hours=8))  # dates are reported in the bank's local day
TXN_LIMIT = 200


class CustomerDataIntegrityError(ValueError):
    """Returned customer records cannot establish a unique, complete relationship assignment."""


def _client(jwt: str):
    return auth.user_db(jwt)


def _session(ctx: RequestContext, tool: str):
    """Policy check, then the user's own DB client and their bigint customer_id."""
    p = ctx.principal
    require_permission(p, tool, {'customer_id': p.customer_id}, ctx)
    if p.kind != 'customer' or not p.user_id or not p.jwt:
        raise PermissionError(tool)
    db = _client(p.jwt.get_secret_value())
    rows = db.schema('banking').table('customer_profiles').select('customer_id,full_name,preferred_language,employment_status') \
        .eq('auth_user_id', str(p.user_id)).limit(2).execute().data
    if len(rows) != 1:
        logger.warning('customer_profile_unresolved', extra={'tool': tool, 'count': len(rows)})
        raise PermissionError(tool)
    return db, rows[0]


def _own(rows: list[dict], customer_id: int) -> list[dict]:
    return [r for r in rows if r.get('customer_id') == customer_id]  # second check if RLS ever regresses


def _rows(db, schema: str, table: str, cols: str, customer_id: int, order: str | None = None, **eq) -> list[dict]:
    q = db.schema(schema).table(table).select(cols).eq('customer_id', customer_id)
    for k, v in eq.items():
        q = q.eq(k, v)
    if order:
        q = q.order(order, desc=True)
    return _own(q.limit(TXN_LIMIT).execute().data, customer_id)


def _day(ts: str) -> str:
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(SGT).date().isoformat()


def _num(v):
    return None if v is None else float(v)


async def get_profile(ctx: RequestContext) -> ToolResult:
    _, p = _session(ctx, 'customer.get_profile')
    return ToolResult(data={'name': p['full_name'], 'preferred_language': p['preferred_language'], 'employment_status': p['employment_status']})


async def get_accounts(ctx: RequestContext) -> ToolResult:
    db, p = _session(ctx, 'customer.get_accounts')
    rows = _rows(db, 'banking', 'bank_accounts', 'customer_id,account_id,account_type,masked_account_number,balance,currency,status', p['customer_id'])
    return ToolResult(data=[{'account_id': f"acc_{r['account_id']}", 'type': r['account_type'], 'number': '****' + r['masked_account_number'][-4:],
                             'currency': r['currency'], 'balance': _num(r['balance']), 'status': r['status']} for r in rows])


async def get_transactions(ctx: RequestContext, from_: date | None = None, to: date | None = None, filters: dict[str, Any] | None = None) -> ToolResult:
    db, p = _session(ctx, 'customer.get_transactions')
    cid = p['customer_id']
    last4 = {c['card_id']: c['last4'] for c in _rows(db, 'banking', 'credit_cards', 'customer_id,card_id,last4', cid)}
    q = db.schema('banking').table('transactions').select('customer_id,transaction_id,card_id,txn_date,merchant,amount,currency,status,failure_reason,is_overseas').eq('customer_id', cid)
    if from_:
        q = q.gte('txn_date', f'{from_.isoformat()}T00:00:00+08:00')
    if to:
        q = q.lt('txn_date', f'{(to + timedelta(days=1)).isoformat()}T00:00:00+08:00')
    rows = [{'txn_id': f"txn_{r['transaction_id']}", 'date': _day(r['txn_date']), 'description': r['merchant'], 'amount': _num(r['amount']),
             'currency': r['currency'], 'status': r['status'], 'card_last4': last4.get(r['card_id']), 'is_overseas': r['is_overseas'],
             'failure_reason': r['failure_reason']} for r in _own(q.order('txn_date', desc=True).limit(TXN_LIMIT).execute().data, cid)]
    rows = [t for t in rows if (not from_ or t['date'] >= from_.isoformat()) and (not to or t['date'] <= to.isoformat())]
    return ToolResult(data=[t for t in rows if all(t.get(k) == v for k, v in (filters or {}).items())])


async def get_credit_cards(ctx: RequestContext) -> ToolResult:
    db, p = _session(ctx, 'customer.get_credit_cards')
    rows = _rows(db, 'banking', 'credit_cards', 'customer_id,card_id,card_name,network,last4,status,credit_limit,outstanding_balance,minimum_payment_due,payment_due_date', p['customer_id'])
    return ToolResult(data=[{'card_id': f"card_{r['card_id']}", 'name': r['card_name'], 'network': r['network'], 'last4': str(r['last4'])[-4:],
                             'status': r['status'], 'limit': _num(r['credit_limit']), 'outstanding': _num(r['outstanding_balance']),
                             'minimum_payment': _num(r['minimum_payment_due']), 'due_date': r['payment_due_date']} for r in rows])


async def get_loans(ctx: RequestContext) -> ToolResult:
    db, p = _session(ctx, 'customer.get_loans')
    rows = _rows(db, 'banking', 'loans', 'customer_id,loan_id,loan_type,original_amount,outstanding_amount,monthly_repayment,next_due_date,status', p['customer_id'])
    return ToolResult(data=[{'loan_id': f"loan_{r['loan_id']}", 'type': r['loan_type'], 'principal': _num(r['original_amount']),
                             'outstanding': _num(r['outstanding_amount']), 'next_payment': r['next_due_date'], 'next_amount': _num(r['monthly_repayment']),
                             'status': r['status']} for r in rows])


async def get_compliance_case(ctx: RequestContext) -> ToolResult:
    """compliance.get_case: the latest client-visible case. compliance.case_internal is never read."""
    db, p = _session(ctx, 'compliance.get_case')
    cid = p['customer_id']
    cases = _rows(db, 'compliance', 'cases', 'customer_id,case_id,case_type,title,notice_text,notice_date,due_date,status,status_updated_at', cid, order='notice_date')
    if not cases:
        return ToolResult(data=None)
    case = {k: v for k, v in cases[0].items() if k != 'customer_id'}
    by_case = {'case_id': case['case_id']}
    items = _rows(db, 'compliance', 'requested_items', 'customer_id,item_id,label,instructions,submission_status,client_mark,client_marked_at,sort_order,updated_at', cid, **by_case)
    history = _rows(db, 'compliance', 'status_history', 'customer_id,status,note,changed_at', cid, order='changed_at', **by_case)
    requests = _rows(db, 'compliance', 'info_requests', 'customer_id,request_id,question,instructions,due_date,status,responded_at,created_at', cid, **by_case)
    drop = lambda rows: [{k: v for k, v in r.items() if k != 'customer_id'} for r in rows]
    return ToolResult(data={**case, 'requested_items': sorted(drop(items), key=lambda i: i['sort_order']), 'status_history': drop(history), 'info_requests': drop(requests)})


async def get_rm_assignment(ctx: RequestContext) -> ToolResult:
    """rm.get_assignment: current RM (and the previous one, for transition questions). Advisors are visible only via RLS on assignment."""
    return await read(lambda:_rm_assignment(ctx),'rm.get_assignment')


def _rm_assignment(ctx: RequestContext) -> ToolResult:
    db, p = _session(ctx, 'rm.get_assignment')
    rows = _rows(db, 'rm', 'assignments', 'customer_id,advisor_id,started_on,ended_on,transition_reason_code', p['customer_id'], order='started_on')
    if not rows:
        return ToolResult(data=None)
    active = [row for row in rows if row['ended_on'] is None]
    if len(active)>1:
        log_event('customer_data_integrity',logging.WARNING,tool='rm.get_assignment',active_count=len(active))
        raise CustomerDataIntegrityError(f'Expected at most one current relationship-manager assignment, received {len(active)}')
    ids = [r['advisor_id'] for r in rows]
    advisors = {a['advisor_id']: a for a in db.schema('rm').table('advisors').select('advisor_id,full_name,role,team_name,email,phone,timezone').in_('advisor_id', ids).execute().data if a['advisor_id'] in ids}

    def rm(r):
        a = advisors.get(r['advisor_id'])
        if a is None:raise CustomerDataIntegrityError('An assigned relationship-manager record was not returned under the customer scope')
        return {'rm_id': r['advisor_id'], 'name': a.get('full_name'), 'role': a.get('role'), 'team': a.get('team_name'),
                'contact': {'email': a.get('email'), 'phone': a.get('phone')}, 'timezone': a.get('timezone'),
                'started_on': r['started_on'], 'ended_on': r['ended_on'], 'transition_reason_code': r['transition_reason_code']}
    current = active[0] if active else None
    previous = next((r for r in rows if r['ended_on']), None)
    return ToolResult(data={'current': rm(current) if current else None, 'previous': rm(previous) if previous else None})
