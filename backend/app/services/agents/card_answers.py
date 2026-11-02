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
from app.services import limit_context
from app.services.client_intents import explanation,request_status

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


FOLLOW=re.compile(r"^\s*(?:and|also|what about|how about|wat about|then|ok|okay)\b|\b(?:that|this|same|the same) card\b|\bits\b|\bfor it\b",re.I)
CARD_ANSWER=re.compile(r'\b(?:outstanding balance|credit limit|minimum payment|due on|annual fee)\b')


def remembered(history: Sequence[Message],cards: Sequence[CreditCard]) -> list[CreditCard]:
    """CR14: the card(s) our previous card answer was about, so "what about the due date?" stays on that card.
    Only the immediately previous reply counts, so a change of topic never carries a card into an unrelated question."""
    if len(history)<2 or history[-2].role!='assistant' or not CARD_ANSWER.search(history[-2].content):return []
    if not FOLLOW.search(normalise(history[-1].content)):return []  # "whats my balance" is a new question (the account)
    if ASKED.search(history[-2].content):return []
    named=set(re.findall(r'ending ([0-9]{4})',history[-2].content))
    return [card for card in cards if card.last4 in named]


LIMIT_CHANGE=re.compile(r"\b(?:increase|raise|higher|bigger|lower|reduce|change|adjust|temporar\w*|up)\b[^.?!]{0,40}\blimit\b"
    r"|\blimit\b[^.?!]{0,25}\b(?:increase|raise|higher|change|to)\b[^.?!]{0,20}\d",re.I)
# A request to DO something is different from asking HOW a product works.
# The latter gets approved guidance, the former starts clarification/intake.
REQUEST_ACTION=re.compile(
    r"^\s*(?:i\s+(?:(?:want|wan|need|would\s+like|wish)\b|(?:am|\x27m)\s+asking\b)|"
    r"please\s+(?:help\s+me\s+)?|can\s+you\s+(?:please\s+)?(?:help\s+me\s+)?|"
    r"could\s+you\s+(?:please\s+)?(?:help\s+me\s+)?)",re.I)

LIMIT_AMOUNT=re.compile(r'(?:s?\$|sgd\s*)?(\d{1,3}(?:,\d{3})+|\d{4,7}|\d{1,4}(?=\s*k\b))\s*(k\b)?',re.I)


def limit_change(task_id: str,text: str) -> AgentResult:
    """Standalone general guidance, without pretending a limit was changed."""
    return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='conversation',text=(
        'You can request a higher or lower card limit. Tap “Raise a card limit request” to provide the card, '
        'requested limit, temporary or permanent change, and reason. For a temporary change, include an end date. '
        'The cards and credit team decides, so approval isn’t guaranteed; submitting a request does not change your limit.'))


def _limit_card(text: str,cards: Sequence[CreditCard]) -> CreditCard | None:
    """Select a card only when the customer names it as a card, not merely a trip.

    'I'm travelling abroad' is a reason, not an instruction to use the Travel Card.
    """
    normalized=normalise(text)
    named=cards_named(normalized,cards)
    if not named or len(named)!=1:return None
    if re.search(r'\b(?:card|ending\s*\d{4}|signature)\b',normalized,re.I):return named[0]
    if re.fullmatch(r'\s*(?:my|the)?\s*(?:travel|signature)(?:\s+one)?\s*[.!]?\s*',normalized,re.I):return named[0]
    return None


def limit_conversation(task_id: str,history: Sequence[Message],cards: Sequence[CreditCard]) -> AgentResult | None:
    """Gather customer-requested details in several turns using owned card records.

    No model-generated figure is needed for a card selection or amount correction,
    and no card limit is changed or submitted by this function. The UI's request
    form remains the only submission entry point, with explicit customer review.
    """
    context=limit_context.extract(history)
    if context is None:return None
    turns=[(idx,item.content) for idx,item in enumerate(history[context.start:],context.start) if item.role=='user']
    chosen=None
    for idx,text in turns:
        selected=_limit_card(text,cards)
        if selected is None:continue
        is_short_card=re.fullmatch(r'\s*(?:my|the)?\s*(?:travel|signature)(?:\s+(?:one|card))?\s*[.!]?\s*',text,re.I)
        previous=history[idx-1].content if idx>0 and history[idx-1].role=='assistant' else ''
        # A one-word reply names a card only when we have asked for a card.
        if is_short_card and not re.search(r'\b(?:which card|card is this for|card should|choose a card)\b',previous,re.I):
            continue
        chosen=selected
    # One card in the authorised records removes the ambiguity, but not the approval step.
    if len(cards)==1:chosen=cards[0]
    amount=(f'{chosen.currency} {context.amount:,}' if context.amount is not None and chosen is not None and chosen.currency else
        f'SGD {context.amount:,}' if context.amount is not None and context.amount_currency=='SGD' else
        f'{context.amount:,} (currency to confirm)' if context.amount is not None else None)
    prefix=f'Your requested new limit is {amount}. ' if amount is not None else ''
    if chosen is not None:prefix+=f'You selected the {name_of(chosen)}. '
    if context.travel_reason:prefix+='You mentioned travel as the reason. '
    if amount is None:
        question=('A temporary change can be requested. What new credit limit would you like?'
            if context.temporary else 'What new credit limit would you like to request?')
    elif context.temporary is None:
        question='Should this increase be temporary or permanent?'
    elif chosen is None:
        names=', '.join(name_of(card) for card in cards)
        question=f'Which card is this for: {names}?'
    elif context.temporary and context.end_date is None:
        question=(f'You mentioned {context.end_year} as the year. What day and month should the temporary change end?'
            if context.end_year else 'What date should the temporary change end? Please include the day, month and year.')
    elif context.temporary and context.end_year is None:
        question=f'You said {context.end_date}. Which year should the temporary change end?'
    else:
        kind='temporary' if context.temporary else 'permanent'
        end=f' until {context.end_date} {context.end_year}' if context.temporary else ''
        reason=' for your travel' if context.travel_reason else ''
        text=(f'Request ready for your review: {kind} change to {amount} on your {name_of(chosen)}{end}{reason}. '
              'Tap “Raise a card limit request” to review any remaining details and submit it yourself. '
              'The cards and credit team decides; approval isn’t guaranteed and no limit has been changed.')
        return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='conversation',text=text)
    # Clarify one field at a time, keeping the verified previous details in the next turn.
    text=prefix+question
    return AgentResult(task_id=task_id,agent='retail',status='needs_clarification',answer_kind='conversation',
        text=text,question=text)


def respond(task_id: str,question: str,history: Sequence[Message],records: RetailRecords) -> AgentResult | None:
    """A rules answer for a plain card-figure question, a which-card question, or None to let the model answer."""
    cards=[card for card in records.cards if card.status!='cancelled'] or list(records.cards)
    if not cards:return None
    latest=history[-1].content if history and history[-1].role=='user' else question
    text=normalise(latest)
    # A definition or comparison is not a request for a customer's particular card.
    if explanation(text) or request_status(text):return None
    intent=awaiting_card(history)
    if intent is None and limit_context.active(history) and not re.search(r'\b(?:transfer|withdrawal|atm|daily)\b',text) and not re.search(r'\b(?:and|also|plus)\b[^.?!]{0,80}\b(?:balance|statement|transaction|fraud|meeting|loan)\b',text):
        # General 'how do I...' remains guidance; an actual request becomes multi-turn intake.
        if len(history)==1 and limit_context.extract(history).amount is None and not re.search(r'\b(?:my trip|temporary|permanent)\b',text) and not REQUEST_ACTION.search(text):
            return limit_change(task_id,text)
        return limit_conversation(task_id,history,cards)
    if intent is None:
        if ELSEWHERE.search(text):return None
        intent=intent_of(text)
        named=cards_named(text,cards) or remembered(history,cards)
        # An annual fee is always a card fee; other figures need the word card, a named card or a card we just discussed.
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
