"""Card balance, limit, minimum payment, due date and annual fee answered from the client's own records (CR14, CR15).

These are the commonest personal questions and the answer is a recorded figure, so rules answer them instead of the
model: the figures cannot be mistyped and the answer is never withheld (which used to hand a simple balance question to
the relationship manager). With more than one card and no card named, the client is asked which card she means, with
one-tap replies; her reply ("travel", "the one ending 5592", "both") is matched back to the question she asked.
Anything else about cards (limit changes, blocks, disputes, statements, spending) still goes to the model or its own flow.
Matching uses the typo-tolerant copy of her words, so "wats my crad bal" works.
"""
import re
from collections.abc import Sequence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,Citation
from app.schemas.customer_data import CreditCard,CustomerTransaction
from app.schemas.retail import RetailRecords
from app.services.typos import normalise

LABELS={'balance':'outstanding balance','limit':'credit limit','minimum':'minimum payment','due':'payment due date','annual_fee':'annual fee'}
INTENTS=(
    ('annual_fee',re.compile(r"\b(?:annual|card)\s+fees?\b|\bfees?\b[^.?!]{0,30}\b(?:annual|every year|each year)\b",re.I)),
    ('minimum',re.compile(r"\bminimum\b|\bleast i (?:can|have to|need to|must) pay\b",re.I)),
    ('due',re.compile(r"\bdue\b|\bwhen\b[^.?!]{0,30}\b(?:pay|payment)\b|\bpayment date\b|\bdeadline\b",re.I)),
    ('limit',re.compile(r"\blimit\b",re.I)),
    ('balance',re.compile(r"\bbalance\b|\boutstanding\b|\bhow much\b[^.?!]{0,30}\b(?:owe|owing|pay|spent on|left to pay)\b|\bdo i owe\b|\bamount owed\b",re.I)),
)
CARD_WORD=re.compile(r"\b(?:cards?|credit|visa|mastercard)\b",re.I)
# Requests and other card flows keep their own handling: limit changes (CR11), fraud, blocks, statements, spending.
ELSEWHERE=re.compile(r"\b(?:increase|raise|higher|lower|reduce|change|changing|adjust|temporary|temporarily|block|unblock|cancel|"
    r"replace|replacement|lost|stolen|fraud|dispute|recognise|recognize|unauthori[sz]ed|suspicious|statement|statements|spent|spending|"
    r"transactions?|purchases?|waive|refund|reverse)\b",re.I)
WHY=re.compile(r"\bwhy\b",re.I)
ALL=re.compile(r"\b(?:both|all|each|every|everything|all of them)\b|\b(?:2|two) cards\b",re.I)
ASKED=re.compile(r"Which card['’]s (outstanding balance|credit limit|minimum payment|payment due date|annual fee) do you mean:")
ANNUAL=re.compile(r'\bannual\b',re.I)


def money(card: CreditCard,value: float) -> str:
    return f'{card.currency} {value:,.2f}' if card.currency else f'{value:,.2f} (currency not recorded)'


def when(value) -> str:
    return f'{value.day} {value:%b %Y}'


def name_of(card: CreditCard) -> str:
    return f'{card.name} ending {card.last4}'


def intent_of(text: str) -> str | None:
    return next((key for key,pattern in INTENTS if pattern.search(text)),None)


def cards_named(text: str,cards: Sequence[CreditCard]) -> list[CreditCard]:
    """Cards the client named by last four digits, by a word of the card's name or by a network only one card has."""
    words=set(re.findall(r"[a-z0-9]+",text.lower()))
    named=[card for card in cards if card.last4 in words
        or any(word in words for word in re.findall(r'[a-z]+',card.name.lower()) if word not in {'card','credit','the','my'})]
    if not named:
        named=[card for card in cards if card.network in words and sum(other.network==card.network for other in cards)==1]
    return named


def awaiting_card(history: Sequence[Message]) -> str | None:
    """The detail we asked about when our last reply was "which card do you mean?" and she has just answered."""
    if len(history)<2 or history[-1].role!='user' or history[-2].role!='assistant' or len(history[-1].content)>120:return None
    found=ASKED.search(history[-2].content)
    return next((key for key,label in LABELS.items() if found and label==found.group(1)),None)


def clarify(intent: str,cards: Sequence[CreditCard]) -> str:
    options=[f'your {name_of(card)}' for card in cards]
    listed=', '.join(options[:-1])+' or '+options[-1] if len(options)>1 else options[0]
    return f'Which card’s {LABELS[intent]} do you mean: {listed}? You can also say “both”.'


def line(intent: str,card: CreditCard,fees: Sequence[CustomerTransaction]) -> str:
    status='' if card.status=='active' else f' (the card is {card.status.replace("_"," ")})'
    if intent=='balance':return f'Your {name_of(card)}{status} has an outstanding balance of {money(card,card.outstanding)}.'
    if intent=='limit':return f'Your {name_of(card)}{status} has a credit limit of {money(card,card.limit)}.'
    if intent in ('minimum','due'):
        due=f'due on {when(card.due_date)}' if card.due_date else 'with no due date recorded'
        return (f'The minimum payment on your {name_of(card)}{status} is {money(card,card.minimum_payment)}, {due}. '
            f'The outstanding balance is {money(card,card.outstanding)}.')
    charged=[row for row in fees if row.card_id==card.card_id]
    if not charged:return f'I can’t see an annual fee charged on your {name_of(card)} in the last 90 days.'
    return ' '.join(f'Your {name_of(card)} was charged an annual fee of {row.currency} {abs(row.amount):,.2f} on {when(row.date)}.'
        for row in charged)+(' If you would like to ask for it to be waived, say “waive my annual fee” and I’ll pass it to your '
        'relationship manager.')


def respond(task_id: str,question: str,history: Sequence[Message],records: RetailRecords) -> AgentResult | None:
    """A rules answer for a plain card-figure question, a which-card question, or None to let the model answer."""
    cards=[card for card in records.cards if card.status!='cancelled'] or list(records.cards)
    if not cards:return None
    latest=history[-1].content if history and history[-1].role=='user' else question
    text=normalise(latest)
    intent=awaiting_card(history)
    if intent is None:
        if ELSEWHERE.search(text):return None
        intent=intent_of(text)
        named=cards_named(text,cards)
        # An annual fee is always a card fee; other figures need the word card or a named card ("my balance" is the account).
        if intent is None or (intent!='annual_fee' and not (CARD_WORD.search(text) or named)):return None
        if WHY.search(text) and intent!='annual_fee':return None
    else:
        named=cards_named(text,cards)
    fees=[row for row in records.transactions if row.card_id is not None and row.amount<0 and ANNUAL.search(row.description)]
    if ALL.search(text):chosen=cards
    elif named:chosen=named
    elif len(cards)==1:chosen=cards
    elif intent=='annual_fee' and len({row.card_id for row in fees})<=1:
        # Only one card (or none) has an annual fee charge, so there is nothing to choose between.
        chosen=[card for card in cards if any(row.card_id==card.card_id for row in fees)]
        if not chosen:
            return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',
                text='I can’t see an annual fee charged on any of your cards in the last 90 days.',
                citations=[Citation(source_id='retail-cards'),Citation(source_id='retail-transactions')])
    else:
        if awaiting_card(history) is not None:return None  # she answered but named no card we know: let the model ask
        question_text=clarify(intent,cards)
        return AgentResult(task_id=task_id,agent='retail',status='needs_clarification',answer_kind='conversation',
            text=question_text,question=question_text)
    sources=[Citation(source_id='retail-cards')]
    if intent=='annual_fee':sources.append(Citation(source_id='retail-transactions'))
    return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',
        text=' '.join(line(intent,card,fees) for card in chosen),citations=sources)
