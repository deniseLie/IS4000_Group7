"""Read only the masked card/security records allowed for the Fraud agent and bound candidate actions."""
import re
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from pydantic import TypeAdapter
from app.schemas.fraud import FraudProposal,SecurityCard,SecurityRecords,SecurityTransaction
from app.schemas.contracts import RequestContext
from app.services import customer_data
from app.services.source_checks import unit_matches,unit_values


async def load(ctx: RequestContext) -> SecurityRecords:
    today=ctx.received_at.astimezone(ZoneInfo('Asia/Singapore')).date()
    cards=await customer_data.get_credit_cards(ctx)
    transactions=await customer_data.get_transactions(ctx,today-timedelta(days=90),today,None)
    return SecurityRecords(cards=TypeAdapter(list[SecurityCard]).validate_python(cards.data),
        transactions=TypeAdapter(list[SecurityTransaction]).validate_python(transactions.data),captured_at=datetime.now(timezone.utc))


def eligible_proposals(question: str,records: SecurityRecords) -> list[FraudProposal]:
    """Require one unambiguous active card. Known transaction ids, suffixes and explicit amounts narrow the scope.

    These rules bound tool targets; they do not prove that a model correctly understood the customer.
    Multiple card matches require clarification, never a guessed block.
    """
    transactions=records.transactions
    ids=set(re.findall(r'\btxn_[1-9][0-9]*\b',question))
    suffixes=set(re.findall(r'\b(?:ending|last four|last 4)\s+(?:in\s+)?([0-9]{4})\b',question,re.I))
    amounts=unit_values(question)
    if ids and not ids<={item.txn_id for item in transactions}:return []
    if suffixes and not suffixes<={card.last4 for card in records.cards}:return []
    if ids:transactions=[item for item in transactions if item.txn_id in ids]
    if suffixes:transactions=[item for item in transactions if item.card_last4 in suffixes]
    if amounts:transactions=[item for item in transactions if any(unit_matches(amount,(item.currency,value)) for amount in amounts
        for value in (abs(Decimal(str(item.amount))),Decimal(str(item.amount))))]
    active={card.card_id for card in records.cards if card.status=='active'}
    candidates=[FraudProposal(card_id=item.card_id,transaction_id=item.txn_id) for item in transactions if item.card_id in active]
    return candidates if len({item.card_id for item in candidates})==1 else []
