"""Verified, read-only status answers for the customer's own request records.

Only an authorised customer ticket read can support a status statement. 'Resolved'
is not proof that a credit limit change was approved. This service creates nothing.
"""
import re
from datetime import datetime
from collections.abc import Sequence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,Citation
from app.schemas.agents import Evidence
from app.schemas.events import Source
from app.schemas.tickets import CustomerTicket
from app.schemas.contracts import RequestContext
from app.services import client_intents,customer_data

STATUS_LABELS={'submitted':'Submitted','in_review':'In review','waiting_on_client':'Waiting for your information',
    'resolved':'Resolved','closed':'Closed'}
CARD_REQUEST=re.compile(r'\b(?:card|credit)\s+limit\b|\blimit\s+(?:increase|change|request)\b',re.I)
REFERENCE=re.compile(r'\bTCK-\d{6,}\b',re.I)
APPROVAL=re.compile(r'\b(?:approved|rejected|approval|decision|accepted|gone through)\b',re.I)
FOLLOW_UP=re.compile(r"\b(?:one|handling|happens next|internally|internal|staff|told|update|status)\b",re.I)
NEW_REQUEST=re.compile(r'\b(?:raise|open|create|submit|start)\b',re.I)


def follow_up(question: str,history: Sequence[Message]) -> str:
    """Resolve only a contiguous ticket-status follow-up; a new request or topic starts afresh."""
    if client_intents.request_status(question) or NEW_REQUEST.search(question):return question
    if len(history)<3 or not FOLLOW_UP.search(question):return question
    selected=REFERENCE.findall(history[-2].content)
    if client_intents.STATEMENT.search(question) and (selected or client_intents.request_status(history[-3].content)):
        return 'Status of my statement request. '+question
    if len(set(selected))==1 and re.search(r'\brequest\b',history[-2].content,re.I):
        return 'Status of my request '+selected[0]+'. '+question
    previous=history[-3]
    if previous.role!='user' or not client_intents.request_status(previous.content):return question
    if client_intents.STATEMENT.search(previous.content):return 'Status of my statement request. '+question
    return question


def evidence_for(rows: Sequence[CustomerTicket],as_of: datetime) -> list[Evidence]:
    text='Customer-authorised request records as of '+as_of.isoformat()+': '+ '; '.join(
        f'{row.reference}: {row.category_label}, status {row.status}, owner {row.owner_team}' for row in rows[:5])
    return [Evidence(id='retail-tickets',text=text,publisher='DemoBank synthetic request records',
        scope='Customer-owned request status read. Workflow status is not a banking approval.',
        source=Source(id='retail-tickets',title='Your request records',url=None,version=as_of.isoformat(),publisher='DemoBank synthetic request records'))]


async def existing_statement_request(task_id: str,question: str,ctx: RequestContext) -> tuple[AgentResult,list[Evidence]] | None:
    """Show a matching open statement request before a client prepares another one; never create a ticket."""
    if not NEW_REQUEST.search(question) or not client_intents.STATEMENT.search(question):return None
    snapshot=await customer_data.get_ticket_records(ctx)
    periods=re.findall(r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december|20\d{2})\b',question,re.I)
    rows=[r for r in snapshot.data if r.category=='statement_request' and r.status in ('submitted','in_review','waiting_on_client')
        and all(period.lower() in (r.summary+' '+' '.join(r.details.values())).lower() for period in periods)]
    if not rows:return None
    refs=', '.join(row.reference for row in rows[:5])
    text=f'You already have an open statement copy request ({refs}). Would you like to follow that request, or prepare a separate one in My Requests? Nothing new has been created.'
    return AgentResult(task_id=task_id,agent='retail',status='needs_clarification',answer_kind='banking',text=text,
        question=text,citations=[Citation(source_id='retail-tickets')]),evidence_for(rows,snapshot.as_of)

async def respond(task_id: str,question: str,ctx):
    if not client_intents.request_status(question):return None
    result=await customer_data.get_ticket_records(ctx)
    rows=result.data
    # Rows are schema-validated CustomerTicket objects, filtered by the caller's
    # authenticated identity in SQL and checked again in customer_data.
    reference=REFERENCE.search(question)
    if reference:
        rows=[row for row in rows if row.reference==reference.group().upper()]
    elif CARD_REQUEST.search(question):
        rows=[row for row in rows if row.category=='card_limit_change']
    elif client_intents.STATEMENT.search(question):
        rows=[row for row in rows if row.category=='statement_request']
    rows=sorted(rows,key=lambda row:row.created_at,reverse=True)
    listing=bool(client_intents.LIST_REQUESTS.search(question))
    cancel=bool(client_intents.CANCEL_TICKET.search(question))
    if listing:
        rows=[row for row in rows if row.status in ('submitted','in_review','waiting_on_client')]
    if listing and not rows:
        return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='conversation',
            text='I cannot find any open service requests in your authorised My Requests records.'),[]
    if not rows:
        kind='card-limit change ' if CARD_REQUEST.search(question) else ''
        text=(f"I couldn't find a recorded {kind}request matching your question in your authorised My Requests records. "
              +('No request has been cancelled. ' if cancel else 'No approval is confirmed. ')
              +'Check My Requests or provide the reference number.')
        return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='conversation',text=text),[]
    if listing:
        lines=[f'{row.reference}: {row.category_label} — {STATUS_LABELS.get(row.status,"Status unavailable")}' for row in rows[:8]]
        text='Here are your open requests from the authorised records: '+ '; '.join(lines)+'. For full details and updates, open My Requests.'
    elif cancel and len(rows)>1 and not reference:
        text='Which request would you like to cancel? Please give its ticket reference. '+ '; '.join(
            f'{row.reference}: {row.category_label}' for row in rows[:5])+'. Nothing has been cancelled.'
    elif cancel:
        text=('I found your request '+rows[0].reference+'. ' if rows else '')+(
            'This chatbot does not currently support cancellation of submitted service tickets. '
            'Please check My Requests or contact the responsible team through the official support channel. '
            'No ticket has been cancelled.')
    elif len(rows)>1 and not reference:
        lines=[f'{row.reference}: {row.category_label} — {STATUS_LABELS.get(row.status,"Status unavailable")}' for row in rows[:5]]
        text='I found more than one matching request. Which reference do you mean? '+ '; '.join(lines)+'. These are recorded workflow statuses, not approval decisions.'
    else:
        row=rows[0]
        text=f'Your {row.category_label} request {row.reference} is recorded as {STATUS_LABELS.get(row.status,"Status unavailable")}. The responsible team is {row.owner_team}.'
        if row.status=='waiting_on_client':text+=' The bank has requested additional information; check My Requests for the recorded next step.'
        if APPROVAL.search(question) or row.category=='card_limit_change':
            text+=' This workflow status alone does not confirm that the requested credit limit has been approved or changed. Only a bank-confirmed decision can establish that.'
        text+=' You can check My Requests for the latest recorded update.'
    if re.search(r'\binternal(?:ly)?\b|\bstaff\b',question,re.I):
        text+=' Internal staff notes are not available in the client view; My Requests shows the updates shared with you.'
    return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',text=text,
        citations=[Citation(source_id='retail-tickets')]),evidence_for(rows,result.as_of)
