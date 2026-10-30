"""Recent transactions, fees and "what was this charge?" answered from the client's own records (CR15).

Listing or finding recorded transactions needs no judgement, so rules do it: the figures are copied from the records,
the answer is never withheld, and typing like "wat did i spend recently" or "why was i charged $25 on 3 oct" works.
Disowned charges (fraud), pending or failed transfers (CR13) and statements keep their own flows.
"""
import re
from datetime import date
from app.schemas.contracts import AgentResult,Citation
from app.schemas.customer_data import CustomerTransaction
from app.schemas.retail import RetailRecords
from app.services.typos import normalise

MONTHS={m:i for i,m in enumerate(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'),1)}
MONTH=r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*'
DAY_MONTH=re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?'+MONTH+r'\b|\b'+MONTH+r'\s+(\d{1,2})(?:st|nd|rd|th)?\b',re.I)
AMOUNT=re.compile(r'(?:s?\$|sgd|usd|eur|gbp)?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(?:dollars?|bucks|sgd)?',re.I)
RECENT=re.compile(r'\b(?:recent|latest|last few|lately|history)\b[^.?!]{0,25}\b(?:transactions?|spending|payments?|purchases?|activity|charges?)\b'
    r'|\b(?:transactions?|activity|spending|charges?)\b[^.?!]{0,20}\b(?:recent|recently|lately|latest|history)\b'
    r'|\bwhat did i (?:spend|buy|pay)\b|\bspent recently\b|\bshow (?:me )?my (?:transactions?|spending|activity)\b|\bmy transactions\b',re.I)
CHARGE=re.compile(r'\b(?:charged?|charges|fee|fees|debit(?:ed)?|deducted|payment|paid|transaction|cost)\b',re.I)
FEES=re.compile(r'\bfees?\b',re.I)
ELSEWHERE=re.compile(r"\b(?:recognise|recognize|unauthori[sz]ed|suspicious|fraud|not mine|didn't make|did not make|stolen|lost|"
    r"pending|failed|declined|statement|statements|waive|refund|reverse|dispute|limit|annual)\b",re.I)
FEE_NOTES={'telegraphic transfer fee':'the bank’s charge for receiving a telegraphic (international wire) transfer into your account',
    'annual card fee':'the yearly fee for the card'}


def when(value: date) -> str:
    return f'{value.day} {value:%b %Y}'


def describe(row: CustomerTransaction) -> str:
    where=f'card ending {row.card_last4}' if row.card_last4 else 'account'
    verb='charged to your' if row.amount<0 else 'received in your'
    status={'pending':' (pending)','failed':' (failed'+(f': {row.failure_reason}' if row.failure_reason else '')+')'}.get(row.status,'')
    return f'{when(row.date)}: {row.description}, {row.currency} {abs(row.amount):,.2f} {verb} {where}{status}'


def dates_in(text: str,records: RetailRecords) -> list[tuple[int,int]]:
    found=[]
    for match in DAY_MONTH.finditer(text):
        day=match.group(1) or match.group(4); month=match.group(2) or match.group(3)
        found.append((MONTHS[month[:3].lower()],int(day)))
    return found


def amounts_in(text: str) -> list[float]:
    stripped=DAY_MONTH.sub(' ',text)
    stripped=re.sub(r'\bending\s+\d{4}\b|\b20\d\d\b',' ',stripped)
    return [float(whole.replace(',','')+('.'+cents if cents else '')) for whole,cents in AMOUNT.findall(stripped)
        if whole.replace(',','').isdigit() and float(whole.replace(',',''))>0]


def note(rows: list[CustomerTransaction]) -> str:
    notes=[f'“{row.description}” is {FEE_NOTES[row.description.lower()]}.' for row in rows if row.description.lower() in FEE_NOTES]
    if any('fee' in row.description.lower() for row in rows):
        notes.append('If you think a fee should not apply, say “waive my fee” and I’ll pass it to your relationship manager.')
    return ('\n'+' '.join(dict.fromkeys(notes))) if notes else ''


def answer(task_id: str,text: str,rows: list[CustomerTransaction],lead: str) -> AgentResult:
    lines='\n'.join('• '+describe(row) for row in rows)
    return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',
        text=f'{lead}\n{lines}'+note(rows),citations=[Citation(source_id='retail-transactions')])


def respond(task_id: str,question: str,latest: str,records: RetailRecords) -> AgentResult | None:
    text=normalise(latest or question)
    if ELSEWHERE.search(text):return None
    transactions=sorted(records.transactions,key=lambda row:(row.date,int(row.txn_id[4:])),reverse=True)
    if RECENT.search(text):
        if not transactions:
            return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',
                text='I can’t see any transactions in the last 90 days.',citations=[Citation(source_id='retail-transactions')])
        return answer(task_id,text,transactions[:8],'Here are your most recent transactions (Singapore dates):')
    if not CHARGE.search(text):return None
    days=dates_in(text,records)
    amounts=amounts_in(text)
    if days or amounts:
        matched=[row for row in transactions
            if (not days or (row.date.month,row.date.day) in days)
            and (not amounts or any(abs(abs(row.amount)-value)<0.005 for value in amounts))]
        if not matched:
            asked=' and '.join(filter(None,[', '.join(f'{value:,.2f}' for value in amounts),
                ', '.join(f'{day} {date(2000,month,1):%b}' for month,day in days)]))
            return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',
                text=f'I can’t find a transaction matching {asked} in your records for the last 90 days. '
                    'Ask me to show your recent transactions and I’ll list them.',citations=[Citation(source_id='retail-transactions')])
        return answer(task_id,text,matched[:5],'Here’s what I found in your records:')
    if FEES.search(text):
        fees=[row for row in transactions if 'fee' in row.description.lower() and row.amount<0]
        if not fees:
            return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',
                text='I can’t see any fees charged in the last 90 days.',citations=[Citation(source_id='retail-transactions')])
        return answer(task_id,text,fees[:8],'These are the fees in your records for the last 90 days:')
    return None
