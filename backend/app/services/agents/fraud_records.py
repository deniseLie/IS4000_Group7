"""Read only the masked card/security records allowed for the Fraud agent and bound candidate actions."""
import re
from datetime import datetime,timedelta,timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo
from decimal import Decimal
from pydantic import TypeAdapter
from app.schemas.fraud import DisputeProposal,FraudProposal,SecurityCard,SecurityRecords,SecurityTransaction
from app.schemas.contracts import RequestContext
from app.services import customer_data
from app.services.source_checks import unit_matches,unit_values

TRAVEL_HOURS=4
SGT=ZoneInfo('Asia/Singapore')


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


def scoped_proposals(question: str,latest: str,records: SecurityRecords) -> list[FraudProposal]:
    """The planner's question first; the customer's own latest message narrows it when the question names no single pair,
    including when it matches charges on several cards (an empty list)."""
    pairs=eligible_proposals(question,records)
    if len(pairs)!=1 and len(narrowed:=eligible_proposals(latest,records))==1:return narrowed
    return pairs


class TravelFlag(NamedTuple):
    anchor: SecurityTransaction
    charge: SecurityTransaction
    gap: timedelta


def travel_flags(records: SecurityRecords) -> list[TravelFlag]:
    """Card charges made in another country than an earlier charge on the same card less than TRAVEL_HOURS before it.

    A bounded "impossible travel" rule: the later charge is flagged against the latest earlier charge from another country.
    It cannot tell which country the cardholder was in and is not a fraud decision; nearby countries can trip it.
    """
    charges=sorted((item for item in records.transactions if item.card_id is not None and item.txn_at is not None
        and item.merchant_country is not None and item.amount<0 and item.status in ('posted','pending')),
        key=lambda item:(item.txn_at,int(item.txn_id[4:])))
    flags=[]
    for index,charge in enumerate(charges):
        anchor=next((item for item in reversed(charges[:index]) if item.card_id==charge.card_id
            and item.merchant_country!=charge.merchant_country),None)
        if anchor is not None and (gap:=charge.txn_at-anchor.txn_at)<timedelta(hours=TRAVEL_HOURS):flags.append(TravelFlag(anchor,charge,gap))
    return flags


def flagged_with(records: SecurityRecords,txn_id: str) -> list[TravelFlag]:
    """Every flag sharing the reported charge's anchor, in time order; empty when the reported charge is not flagged."""
    flags=travel_flags(records)
    reported=next((flag for flag in flags if flag.charge.txn_id==txn_id),None)
    return [flag for flag in flags if reported is not None and flag.anchor.txn_id==reported.anchor.txn_id]


def dispute_for(records: SecurityRecords,proposal: FraudProposal) -> DisputeProposal:
    """The reported charge plus the other charges the travel rule flagged with it, including ones the customer did not mention."""
    ids={proposal.transaction_id,*(flag.charge.txn_id for flag in flagged_with(records,proposal.transaction_id))}
    return DisputeProposal(card_id=proposal.card_id,transaction_ids=sorted(ids,key=lambda item:int(item[4:])))
