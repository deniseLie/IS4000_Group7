"""Values code computes from an agent's reads (build plan phase 7; architecture §14.6): exports for a dependent task,
evidence the answer cites, and offers built from records. Never written by the model.

The runner calls compute() after the default reads and before the answer call. Exports reach the output only when the
answer passes validation, so a withheld section exports nothing and its dependants are skipped ("unknown means no").
A value that cannot be read (the trip window, which card) is a fixed clarifying question, never a guess.
"""
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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


def account_refs(evidence: list[Evidence]) -> dict[str, Export]:
    """Type, currency and the last 4 digits of each account; no balances (C24 tax self-certification)."""
    rows, source = records(evidence, 'my_accounts')
    digits = lambda r: ''.join(ch for ch in str(r.get('number', '')) if ch.isdigit())
    refs = [{'type': r.get('type'), 'currency': r.get('currency'), 'last4': digits(r)[-4:]} for r in rows
        if isinstance(r, dict) and len(digits(r)) >= 4]
    return {'account_refs': Export(value=refs, source_ids=[source])} if source and refs else {}


def trip_evidence(body: dict, ctx: RequestContext) -> Evidence:
    return Evidence(id=TRIP_SOURCE, text=json.dumps(body, ensure_ascii=False), publisher='DemoBank',
        scope='Dates computed by code from the customer\'s own card and loan records and the public holiday calendar; '
            f'today in Singapore is {tools.today(ctx).isoformat()}.',
        source=Source(id=TRIP_SOURCE, title='Your due dates during your trip', url=None, version=tools.today(ctx).isoformat(), publisher='DemoBank'))


async def trip_due_dates(spec: AgentSpec, task: PlanTask, ctx: RequestContext, evidence: list[Evidence], read: Reader) -> Derived:
    """C23: which card and loan payments fall due while she is away, after weekend and public-holiday shifts."""
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


async def travel_notice(spec: AgentSpec, task: PlanTask, ctx: RequestContext, evidence: list[Evidence], read: Reader) -> Derived:
    """Travel readiness with dates: one travel notice for one active card, offered for her confirmation (migration 0101)."""
    today = tools.today(ctx)
    if (trip := trip_checks.window(task.entities, task.question, today)) is None:return Derived()  # general readiness only
    cards = [c for c in records(evidence, 'my_cards')[0] if isinstance(c, dict) and c.get('last4') and c.get('status') == 'active']
    named = str(task.entities.get('card') or '')[-4:]
    chosen = [c for c in cards if c['last4'] == named] or (cards if len(cards) == 1 else [])
    if not chosen:
        if not cards:return Derived()
        raise Clarify('Which card will you use on the trip: ' + ' or '.join(f'the card ending {c["last4"]}' for c in cards[:4]) + '?')
    card, (start, end) = chosen[0]['last4'], trip
    start = max(start, today)  # a trip already under way is noticed from today (the ticket executor takes no past date)
    details = {'card': card, 'from_date': start.isoformat(), 'to_date': end.isoformat()}
    if isinstance(where := task.entities.get('destination'), str) and where.strip():details['destination'] = where.strip()[:80]
    return Derived(proposals=[OfferProposal(task_id=task.id, agent=spec.name, type='ticket.create',
        summary=f'Send a travel notice for your card ending {card}, {start.day} {start:%b} to {end.day} {end:%b}',
        target={'category': 'travel_notice', 'urgency': 'normal', 'details': details,
            'summary': f'Travel notice: card ending {card}, {start.isoformat()} to {end.isoformat()}.'})])


HOOKS: dict[tuple[str, str], Callable[..., Awaitable[Derived]]] = {
    ('accounts', 'trip_due_dates'): trip_due_dates,
    ('security', 'travel_readiness'): travel_notice,
}


async def compute(spec: AgentSpec, task: PlanTask, ctx: RequestContext, evidence: list[Evidence], read: Reader) -> Derived:
    """Raises Clarify for a value that cannot be read; otherwise the hook's values plus the always-on account_refs."""
    hook = HOOKS.get((spec.name, task.intent))
    derived = await hook(spec, task, ctx, evidence, read) if hook else Derived()
    if 'account_refs' in spec.exports:derived.exports.update(account_refs(evidence))
    derived.exports = {k: v for k, v in derived.exports.items() if k in spec.exports}  # only what the spec declares
    return derived
