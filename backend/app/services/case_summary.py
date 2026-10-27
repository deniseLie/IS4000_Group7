"""One staff case summary for a reported card charge, so the customer need not repeat the story.

Built only from owned records, rule checks, recorded decisions and the customer's own redacted words; never from
model text, which may have been withheld. It is stored in a support handoff (not in logs or audit details).
"""
from datetime import date
from decimal import Decimal
from app.schemas.customer_data import CustomerTransaction
from app.schemas.disputes import DisputeItem

LIMIT=1000
WORDS=160
STATES={'succeeded':'confirmed','rejected':'declined','expired':'expired','failed':'not completed (a record changed)'}


def money(currency: str,value: float) -> str:
    return f'{currency} {Decimal(str(value)):.2f}'


def charge(item: DisputeItem) -> str:
    return f'{item.date.isoformat()} {item.merchant} {money(item.currency,item.amount)}'


def words(text: str) -> str:
    text=' '.join(text.split())
    return text if len(text)<=WORDS else text[:WORDS].rsplit(' ',1)[0]+'…'


def build(*,last4: str,reported_id: str,items: list[DisputeItem],block: str | None,dispute: str | None,possession: str | None,
        transfers: list[CustomerTransaction],due: date | None,lockout: bool,customer_words: str) -> str:
    """Deterministic sentences in a fixed order; the customer's words are trimmed first if the summary is too long."""
    reported=next((item for item in items if item.transaction_id==reported_id),None)
    related=[item for item in items if item.transaction_id!=reported_id]
    parts=[f'Card ending {last4}.']
    parts.append(f'Reported charge: {charge(reported)}.' if reported else 'Reported charge: see the recorded card decision.')
    if related:
        parts.append(f'Rule check (a pattern flag, not a fraud decision): {len(related)} smaller earlier charges from the same merchant: '
            +'; '.join(charge(item) for item in related)+'.')
    if block:parts.append(f'Temporary block: {STATES[block]}.')
    if dispute:
        parts.append(f'Dispute draft for {len(items)} charges: {STATES[dispute]}'
            +(f'; card in possession: {possession}.' if dispute=='succeeded' and possession else '.'))
    also=[f'pending transfer {row.description} {money(row.currency,row.amount)} since {row.date.isoformat()}'
        +(f', customer-stated due date {due.isoformat()}' if due else '') for row in transfers]
    if lockout:also.append('app lockout, answered as a security step')
    if also:parts.append('Also raised: '+'; '.join(also)+'.')
    tail='Staff decide refunds and cancellation.'
    quoted=words(customer_words)
    summary=' '.join([*parts,f'Customer’s words: "{quoted}"',tail]) if quoted else ' '.join([*parts,tail])
    while len(summary)>LIMIT and quoted:
        quoted=quoted[:max(0,len(quoted)-(len(summary)-LIMIT)-1)].rsplit(' ',1)[0]
        summary=' '.join([*parts,f'Customer’s words: "{quoted}…"',tail]) if quoted else ' '.join([*parts,tail])
    return summary[:LIMIT]
