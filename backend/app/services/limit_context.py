"""Conversation-local, read-only card-limit request context (CR11).

This module does not change limits, create tickets or infer bank approvals. It only
extracts *customer-provided* request details from a bounded chat history, using
an explicit topic boundary so another request cannot inherit those details.
There are no hidden mutable module globals: the saved/authorised conversation
history remains the source of truth for every turn.
"""
import re
from app.services.client_intents import explanation,request_status
from dataclasses import dataclass
from collections.abc import Sequence

START = re.compile(
    r"\b(?:increase|raise|higher|lower|reduce|change|adjust|temporary|temporarily|boost|up)\b[^.?!]{0,65}\b(?:credit\s+)?(?:card\s+)?limit\b"
    r"|\b(?:credit\s+)?(?:card\s+)?limit\b[^.?!]{0,55}\b(?:increase|increased|raise|raised|higher|change|adjust|to\s+(?:\$|sgd|\d))\b",re.I)
# Don't treat an unrelated new enquiry as a continuation of a limit request.
OTHER_TOPIC = re.compile(
    r"\b(?:account balance|card balance|outstanding balance|recent transactions?|statements?|"
    r"compliance|documents?|upload|mortgage|loans?|fraud|suspicious|lost card|"
    r"book (?:a |my )?meeting|appointments?|interest rate|annual fee|waiv(?:e|er)|"
    r"cancel (?:a |my )?card|block (?:a |my )?card|ticket status|request status|"
    r"(?:what(?:'s| is)|show|check|view) my (?:current )?(?:card |credit )?limit|"
    r"current (?:credit |card )?limit)\b",re.I)
# A continuation should be identifiable as such; do not hijack random chat messages.
CONTINUATION = re.compile(
    r"\b(?:actually|instead|correction|make (?:it|that)|change (?:it|that)|"
    r"i(?:'m| am) travell?ing|travel(?:ling)?|trip|holiday|vacation|overseas|"
    r"temporary|temporarily|permanent|until|through|end date|for my card|"
    r"travel card|signature card|the travel one|the signature one|signature|"
    r"card ending|ending \d{4}|which card|limit|request|amount|yes|no)\b"
    r"|(?:s?\$\s*\d|sgd\s*\d|\b\d{1,3}(?:,\d{3})+\b|\b\d{1,3}k\b|\b\d{4,7}\b)"
    r"|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b",re.I)
AMOUNT = re.compile(r"(?<![\w])(?P<currency>s?\$|sgd\s*)?\s*(?P<number>\d{1,3}(?:,\d{3})+|\d{4,7}|\d{1,3}(?=\s*k\b))\s*(?P<k>k\b)?",re.I)
TEMP = re.compile(r"\b(?:temporary|temporarily|until|through|only for (?:my |the )?trip)\b",re.I)
PERM = re.compile(r"\bpermanent(?:ly)?\b",re.I)
END_DATE = re.compile(r"\b(?:until|through|ending|end(?:s|ing)? (?:on|at))\s+(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b(?:\s+(20\d{2}))?",re.I)
DATE_ONLY = re.compile(r"\s*(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s*(20\d{2})?\s*[.!]?\s*$",re.I)
YEAR_ONLY = re.compile(r"\s*(20\d{2})\s*[.!]?\s*$")
TRAVEL = re.compile(r"\b(?:travell?ing|travel|trip|holiday|vacation|overseas)\b",re.I)

@dataclass(frozen=True)
class LimitContext:
    start: int
    amount: int | None = None
    amount_currency: str | None = None
    temporary: bool | None = None
    end_date: str | None = None
    end_year: str | None = None
    travel_reason: bool = False


def _parts(history: Sequence) -> list[tuple[str,str]]:
    return [(m.role,m.content) for m in history]


def _start(history: Sequence) -> int | None:
    messages=_parts(history)
    # Bounded memory: never carry an unrelated old request into a new one.
    candidates=[i for i,(role,content) in enumerate(messages) if role=='user' and START.search(content) and not explanation(content)]
    if not candidates:return None
    start=candidates[-1]
    if len(messages)-start>28 or sum(role=='user' for role,_ in messages[start:])>14:return None
    return start


def active(history: Sequence) -> bool:
    """Only a genuine card-limit follow-up gets the retail card workflow."""
    if not history or history[-1].role!='user':return False
    latest=history[-1].content
    if explanation(latest) or request_status(latest):return False
    if START.search(latest):return True
    start=_start(history)
    # A bare year is a continuation only if we explicitly asked for the date/year.
    pending_year=(YEAR_ONLY.fullmatch(latest) is not None and len(history)>1
        and history[-2].role=='assistant' and re.search(r'\b(?:date|year|end)\b',history[-2].content,re.I))
    if start is None or OTHER_TOPIC.search(latest) or not (CONTINUATION.search(latest) or pending_year):return False
    # A question switching topics in between closes the old request's context.
    for role,content in _parts(history)[start+1:-1]:
        if role=='user' and OTHER_TOPIC.search(content) and not START.search(content):return False
    return True


def _amount(text: str) -> tuple[int,str | None] | None:
    found=[]
    for match in AMOUNT.finditer(text):
        # An unqualified four-digit year must not become a requested credit limit.
        prefix=text[max(0,match.start()-18):match.start()].lower()
        currency=(match.group('currency') or '').lower().replace(' ','')
        has_hint=bool(currency or match.group('k') or ',' in match.group('number') or re.search(r'\b(?:limit|to|make it|make that|instead|request|amount)\s*$',prefix))
        if not has_hint:continue
        value=int(match.group('number').replace(',',''))*(1000 if match.group('k') else 1)
        if 500<=value<=1_000_000:
            found.append((value,'SGD' if currency=='sgd' or currency=='s$' else 'unspecified'))
    return found[-1] if found else None


def extract(history: Sequence) -> LimitContext | None:
    if not active(history):return None
    start=_start(history)
    if start is None:return None
    amount=None; currency=None; temporary=None; end_date=None;end_year=None;travel=False
    for index,(role,text) in enumerate(_parts(history)[start:], start):
        if role!='user':continue
        if (new:=_amount(text)) is not None:amount,currency=new
        if TEMP.search(text):temporary=True
        elif PERM.search(text):temporary=False;end_date=None;end_year=None
        found=END_DATE.search(text)
        if found is None and temporary and index>0 and history[index-1].role=='assistant' and re.search(r'\bend\b|\bdate\b',history[index-1].content,re.I):
            found=DATE_ONLY.fullmatch(text)
        if found:
            month=found.group(2).title()
            end_date=f'{int(found.group(1))} {month}'
            # A year may have been provided in a previous answer ("2028").
            end_year=found.group(3) or end_year
            temporary=True
        elif index>0 and history[index-1].role=='assistant' and re.search(r'\b(?:date|year|end)\b',history[index-1].content,re.I) and (year:=YEAR_ONLY.fullmatch(text)):
            # Never treat the year alone as a complete end date.
            end_year=year.group(1)
        # A short "travel" after a choice between cards means the Travel Card,
        # NOT necessarily that travelling was the reason for the request.
        if TRAVEL.search(re.sub(r'\btravel card\b','',text,flags=re.I)) and not re.fullmatch(
                r'\s*(?:the|my)?\s*travel(?:\s+(?:card|one))?\s*[.!]?\s*',text,re.I):
            travel=True
    return LimitContext(start,amount,currency,temporary,end_date,end_year,travel)
