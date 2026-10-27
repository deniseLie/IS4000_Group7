"""Deterministic retail checks: today's date, pending transfers against a stated due day, balances after pending debits.

Code computes every figure here so the model never calculates one. The due-day reader is a bounded English rule,
not a semantic parser; business days are Monday to Friday without a public holiday calendar.
"""
import re
from datetime import date,timedelta
from decimal import Decimal
from app.schemas.agents import Evidence
from app.schemas.events import Source
from app.schemas.customer_data import CustomerTransaction
from app.schemas.retail import RetailRecords

DAYS={'monday':0,'mon':0,'tuesday':1,'tues':1,'tue':1,'wednesday':2,'wed':2,'thursday':3,'thurs':3,'thur':3,'thu':3,
    'friday':4,'fri':4,'saturday':5,'sat':5,'sunday':6,'sun':6}
MONTHS={name:i+1 for i,names in enumerate(['jan january','feb february','mar march','apr april','may','jun june','jul july',
    'aug august','sep sept september','oct october','nov november','dec december']) for name in names.split()}
CUE=re.compile(r"\b(?:due|by|before|deadline)\b",re.I)
MONEY=re.compile(r'\b(?:fees?|fall[- ]below|balance|minimum balance|enough money)\b',re.I)
TRANSFER=re.compile(r'\b(?:transfers?|pending|payments?|rent|landlord|payee|arrive|go through|make it)\b',re.I)
WEEKDAY=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']


def due_date(question: str,today: date) -> date | None:
    """The first date named within a few words after a due cue. Weekdays resolve to today or later; a day and month
    without a year is this year's date unless that is more than half a year ago."""
    for cue in CUE.finditer(question):
        text=question[cue.end():cue.end()+40].lower()
        if found:=re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b',text):
            try:return date(int(found[1]),int(found[2]),int(found[3]))
            except ValueError:return None
        if found:=re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b',text):
            if found[2] in MONTHS:
                try:value=date(today.year,MONTHS[found[2]],int(found[1]))
                except ValueError:return None
                return value if (today-value).days<=183 else value.replace(year=today.year+1)
        if re.match(r'\W*today\b',text):return today
        if re.match(r'\W*tomorrow\b',text):return today+timedelta(days=1)
        if found:=re.match(r"\W*(?:this\s+|next\s+|on\s+)?([a-z]+)",text):
            if found[1] in DAYS:return today+timedelta(days=(DAYS[found[1]]-today.weekday())%7)
    return None


def business_days(start: date,end: date) -> int:
    """Monday-to-Friday days after start, up to and including end."""
    return sum((start+timedelta(days=i)).weekday()<5 for i in range(1,(end-start).days+1))


def amount(value: float) -> str:
    return f'{Decimal(str(value)):.2f}'


def pending_debits(records: RetailRecords) -> list[CustomerTransaction]:
    return [row for row in records.transactions if row.card_id is None and row.status=='pending' and row.amount<0]


def relevant(records: RetailRecords,question: str,today: date) -> bool:
    """Only questions about a payment, a due day, fees or balances get computed checks."""
    return bool((pending_debits(records) and TRANSFER.search(question)) or due_date(question,today) is not None or MONEY.search(question))


def checks(records: RetailRecords,question: str,today: date,threshold: tuple[Decimal,str] | None) -> Evidence | None:
    """Evidence for pending debits, a stated due day and fall-below status; None when the question needs none of them."""
    if not relevant(records,question,today):return None
    pending=pending_debits(records)
    due=due_date(question,today)
    lines=[f'DemoBank rule checks computed by code for this request; they are not a prediction. Today in Singapore is {today.isoformat()} ({WEEKDAY[today.weekday()]}).']
    for row in pending:
        lines.append(f'Pending transfer: {row.description}, recorded amount {row.currency} {amount(row.amount)} ({row.currency} {amount(-row.amount)} out), '
            f'recorded as pending since {row.date.isoformat()} ({WEEKDAY[row.date.weekday()]}): {(today-row.date).days} calendar days and '
            f'{business_days(row.date,today)} business days ago (Monday to Friday; public holidays are not checked).')
    if due is not None:
        lines.append(f"The customer's stated due day is {due.isoformat()} ({WEEKDAY[due.weekday()]}), "
            +('today.' if due==today else f'{(due-today).days} days from today.' if due>today else 'already past.'))
    for account in records.accounts:
        out=sum((Decimal(str(-row.amount)) for row in pending if row.currency==account.currency),Decimal(0))
        same=[item for item in records.accounts if item.currency==account.currency]
        balance=Decimal(str(account.balance))
        text=f'{account.type.replace("_"," ").capitalize()} account {account.number}: recorded balance {account.currency} {balance:.2f}'
        figures=[balance]
        if out and len(same)==1:
            figures.append(balance-out)
            text+=f'; if the pending {account.currency} {out:.2f} out completes and is not already included, the balance would be {account.currency} {balance-out:.2f}'
        lines.append(text+'.')
        if account.type=='savings' and threshold is not None and threshold[1]==account.currency:
            limit=threshold[0]
            state='above' if all(item>=limit for item in figures) else 'below' if all(item<limit for item in figures) else 'on both sides of'
            lines.append(f'The savings fall-below fee applies when the average daily balance in a month is below {account.currency} {limit:.2f} (savings-fees). '
                f'{"This balance is" if len(figures)==1 else "Both balances are"} {state} that threshold; a balance on one day is not the monthly average.')
    for card in records.cards:
        lines.append(f'Card charges are billed to the credit card ending {card.last4} (outstanding {amount(card.outstanding)} of a '
            f'{amount(card.limit)} limit; card currency not recorded) and do not change a bank account balance.')
    return Evidence(id='retail-checks',text=' '.join(lines),publisher='DemoBank rule checks',
        scope='Deterministic checks over the authenticated records captured for this request; not a prediction or a monthly average.',
        source=Source(id='retail-checks',title='Rule checks: pending transfers, due day and balances',url=None,
            version=records.captured_at.isoformat(),publisher='DemoBank rule checks'))
