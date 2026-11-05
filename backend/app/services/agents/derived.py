"""Values code computes from an agent's reads (build plan phase 7; architecture §14.6): exports for a dependent task,
evidence the answer cites, and offers built from records. Never written by the model.

The runner calls compute() after the default reads and before the answer call. Exports reach the output only when the
answer passes validation, so a withheld section exports nothing and its dependants are skipped ("unknown means no").
A value that cannot be read (the trip window, which card) is a fixed clarifying question, never a guess.
"""
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from app.schemas.agents import Evidence
from app.schemas.contracts import AgentSpec, Export, OfferProposal, PlanTask, RequestContext
from app.schemas.events import Source
from app.services import tools
from app.services.agents import trip_checks

Reader = Callable[[str, dict[str, Any]], Awaitable[list[Evidence]]]  # one logged read in the runner, as evidence
COUNTRY = 'SG'
TRIP_SOURCE = 'trip-checks'


class Clarify(Exception):
    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


@dataclass
class Derived:
    evidence: list[Evidence] = field(default_factory=list)
    exports: dict[str, Export] = field(default_factory=dict)
    proposals: list[OfferProposal] = field(default_factory=list)


def records(evidence: list[Evidence], tool: str) -> tuple[list[dict], str | None]:
    """The rows of one read (runner.records_evidence ids start with the tool name); a note or a missing read is (None)."""
    for item in evidence:
        if item.id == tool or item.id.startswith(tool + '-') and item.id[len(tool)+1:].isdigit():
            rows = json.loads(item.text).get('records')
            return (rows if isinstance(rows, list) else [rows]), item.id
    return [], None


def refs_of(rows: list, source: str | None) -> dict[str, Export]:
    """Type, currency and the last 4 digits of each account; no balances (for a tax self-certification)."""
    digits = lambda r: ''.join(ch for ch in str(r.get('number', '')) if ch.isdigit())
    refs = [{'type': r.get('type'), 'currency': r.get('currency'), 'last4': digits(r)[-4:]} for r in rows
        if isinstance(r, dict) and len(digits(r)) >= 4]
    return {'account_refs': Export(value=refs, source_ids=[source])} if source and refs else {}


def account_refs(evidence: list[Evidence]) -> dict[str, Export]:
    return refs_of(*records(evidence, 'my_accounts'))


def account_refs_from(accounts: list, source: str = 'retail-accounts') -> dict[str, Export]:
    """The same export from the records an Accounts rule answered from (retail_records; evidence `retail-accounts`), so a
    question a rule answers still passes her account list on (P05: Accounts, then Compliance's tax self-certification)."""
    return refs_of([a.model_dump(mode='json') if hasattr(a, 'model_dump') else a for a in accounts], source)


# ---- Savings after paying the card balances (P06) ----

SAVINGS_SOURCE = 'savings-checks'
ASK_THRESHOLD = 'What amount would you like your savings to stay at or above after paying your card balances?'
AMOUNT = re.compile(r'^(?:s?\$|sgd)?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(k|m)?$', re.I)
STATED = re.compile(r'\b(?:at least|above|over|below|under|more than|less than|minimum of|no less than)\s+((?:s?\$|sgd)?\s*\d[\d,]*(?:\.\d{1,2})?\s*[km]?)\b', re.I)


def amount(value: Any) -> Decimal | None:
    """An amount she named as a whole: "5000", "5,000", "5k", "SGD 5000", "$5.5k". Anything else is None (never guessed)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):return None
    match = AMOUNT.match(str(value).strip())
    if match is None:return None
    whole, cents, scale = match.groups()
    found = Decimal(whole.replace(',', '') + '.' + (cents or '0')) * {None: 1, 'k': 1000, 'm': 1000000}[scale.lower() if scale else None]
    return found.quantize(Decimal('0.01')) if Decimal('0') < found <= Decimal('100000000') else None


def threshold(task: PlanTask) -> Decimal | None:
    """The planner's `threshold` entity, else one amount the question states after "at least", "below" and the like."""
    if (named := amount(task.entities.get('threshold'))) is not None:return named
    stated = {amount(m.group(1)) for m in STATED.finditer(task.question)} - {None}
    return stated.pop() if len(stated) == 1 else None


def figure(value: Decimal) -> float:
    return float(value.quantize(Decimal('0.01')))


async def savings_after_cards(spec: AgentSpec, task: PlanTask, ctx: RequestContext, evidence: list[Evidence], read: Reader) -> Derived:
    """Her active savings minus what she owes on her cards, against the amount she named: `below_threshold` is True when
    paying every card balance in full would leave less than that amount. Only SGD records are added up; another or an
    unrecorded currency is reported as not checked and exports nothing (unknown means no)."""
    limit = threshold(task)
    if limit is None:raise Clarify(ASK_THRESHOLD)
    accounts, account_source = records(evidence, 'my_accounts')
    cards, card_source = records(evidence, 'my_cards')
    money = lambda v: Decimal(str(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    savings = [a for a in accounts if isinstance(a, dict) and a.get('type') == 'savings' and a.get('status') == 'active']
    owed = [c for c in cards if isinstance(c, dict) and (money(c.get('outstanding')) or 0) > 0]
    digits = lambda r, key: ''.join(ch for ch in str(r.get(key, '')) if ch.isdigit())[-4:]
    why = ('accounts_unavailable' if account_source is None else 'cards_unavailable' if card_source is None
        else 'no_savings_account' if not savings else 'other_currency' if {r.get('currency') for r in [*savings, *owed]} != {'SGD'}
        else 'unreadable_amount' if any(money(a.get('balance')) is None for a in savings) else None)
    sgd = lambda value: {'currency': 'SGD', 'amount': figure(value)}  # source_checks reads a figure's currency from its own record
    body: dict[str, Any] = {'threshold': sgd(limit), 'checked': why is None}
    if why is None:
        kept = sum((money(a['balance']) for a in savings), Decimal('0'))
        paid = sum((money(c['outstanding']) for c in owed), Decimal('0'))
        left = kept - paid
        body.update(savings_accounts=[{'last4': digits(a, 'number'), 'currency': 'SGD', 'balance': figure(money(a['balance']))} for a in savings],
            card_balances=[{'last4': digits(c, 'last4'), 'currency': 'SGD', 'outstanding': figure(money(c['outstanding']))} for c in owed],
            savings_total=sgd(kept), card_balances_total=sgd(paid), savings_after_paying=sgd(left), below_threshold=left < limit)
    else:body['not_checked'] = why
    evidence.append(Evidence(id=SAVINGS_SOURCE, text=json.dumps(body, ensure_ascii=False), publisher='DemoBank',
        scope='Computed by code from the customer\'s own active savings accounts and card balances: savings minus every card\'s '
            'outstanding balance, against the amount she named. A plan, not a payment; balances change.',
        source=Source(id=SAVINGS_SOURCE, title='Your savings after paying your cards', url=None, version=tools.today(ctx).isoformat(), publisher='DemoBank')))
    if why is not None:return Derived()
    return Derived(exports={'below_threshold': Export(value=body['below_threshold'], source_ids=[account_source, card_source, SAVINGS_SOURCE])})


# ---- A document's preliminary check (P07) ----

FILLER = frozenset({'proof', 'form', 'forms', 'document', 'documents', 'copy', 'certification', 'self', 'your', 'the', 'and',
    'for', 'latest', 'recent', 'demo', 'statement', 'declaration'})
FAILED = {'issues_found': True, 'unable_to_assess': True, 'no_issues': False}  # unable to assess: it did not pass


def words(text: str) -> set[str]:
    return {w[:-1] if w.endswith('s') and len(w) > 4 else w for w in re.findall(r'[a-z]{3,}', text.lower())}


def check_failed(evidence: list[Evidence], question: str) -> dict[str, Export]:
    """Whether the one document the question names (or the only one shown) did not pass its current preliminary check,
    from the `document-prechecks` rows (fresh checks only; document.evidence_for). Several matches, no match among several
    items, or no current check is unknown: nothing is exported and a dependant is skipped."""
    rows, source = records(evidence, 'document-prechecks')
    asked = words(question)
    named = [r for r in rows if isinstance(r, dict) and (words(str(r.get('item', ''))) - FILLER) & asked]
    found = named if named else [r for r in rows if isinstance(r, dict)]
    if source is None or len(found) != 1 or not isinstance(report := found[0].get('precheck'), dict):return {}
    failed = FAILED.get(report.get('status'))
    return {} if failed is None else {'check_failed': Export(value=failed, source_ids=[source])}


def trip_evidence(body: dict, ctx: RequestContext) -> Evidence:
    return Evidence(id=TRIP_SOURCE, text=json.dumps(body, ensure_ascii=False), publisher='DemoBank',
        scope='Dates computed by code from the customer\'s own card and loan records and the public holiday calendar; '
            f'today in Singapore is {tools.today(ctx).isoformat()}.',
        source=Source(id=TRIP_SOURCE, title='Your due dates during your trip', url=None, version=tools.today(ctx).isoformat(), publisher='DemoBank'))


async def trip_due_dates(spec: AgentSpec, task: PlanTask, ctx: RequestContext, evidence: list[Evidence], read: Reader) -> Derived:
    """Which card and loan payments fall due while she is away, after weekend and public-holiday shifts."""
    today = tools.today(ctx)
    if (trip := trip_checks.window(task.entities, task.question, today)) is None:raise Clarify(trip_checks.ASK_WINDOW)
    start, end = trip
    cards, card_source = records(evidence, 'my_cards')
    loans, loan_source = records(evidence, 'my_loans')
    holidays, sources, checked = set(), [s for s in (card_source, loan_source) if s], True
    for year in sorted({start.year, end.year}):
        found = await read('public_holidays', {'country': COUNTRY, 'year': year})
        rows, source = records(found, 'public_holidays')
        evidence.extend(found)
        if source is None:checked = False;continue
        sources.append(source)
        holidays |= {trip_checks.as_date(r.get('date'), today) for r in rows if isinstance(r, dict) and r.get('national', True)} - {None}
    items = [(f'Card ending {c["last4"]} payment', trip_checks.as_date(c.get('due_date'), today)) for c in cards if isinstance(c, dict) and c.get('last4')]
    items += [(f'{l.get("type") or "Loan"} instalment', trip_checks.as_date(l.get('next_payment'), today)) for l in loans if isinstance(l, dict)]
    due = trip_checks.dues([(label, day) for label, day in items if day is not None], start, end, holidays)
    evidence.append(trip_evidence({'trip_from': start.isoformat(), 'trip_to': end.isoformat(), 'holidays_checked': checked,
        'due_dates': due}, ctx))
    if not checked or not sources:return Derived()  # unknown means no: a dependant is skipped, the answer says what was not checked
    inside = sorted({d['pays_on'] for d in due if d['in_trip']})
    provenance = [*sources, TRIP_SOURCE]
    return Derived(exports={'due_in_window': Export(value=bool(inside), source_ids=provenance),
        'due_dates': Export(value=inside, source_ids=provenance)})


HOOKS: dict[tuple[str, str], Callable[..., Awaitable[Derived]]] = {
    ('accounts', 'trip_due_dates'): trip_due_dates,
    ('accounts', 'savings_after_cards'): savings_after_cards,
}


async def compute(spec: AgentSpec, task: PlanTask, ctx: RequestContext, evidence: list[Evidence], read: Reader) -> Derived:
    """Raises Clarify for a value that cannot be read; otherwise the hook's values plus the always-on account_refs."""
    hook = HOOKS.get((spec.name, task.intent))
    derived = await hook(spec, task, ctx, evidence, read) if hook else Derived()
    if 'account_refs' in spec.exports:derived.exports.update(account_refs(evidence))
    derived.exports = {k: v for k, v in derived.exports.items() if k in spec.exports}  # only what the spec declares
    return derived
