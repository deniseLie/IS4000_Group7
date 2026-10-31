"""Verified, read-only status answers for the customer's own request records.

Only an authorised customer ticket read can support a status statement. 'Resolved'
is not proof that a credit limit change was approved. This service creates nothing.
"""
import re
from app.schemas.contracts import AgentResult,Citation
from app.schemas.agents import Evidence
from app.schemas.events import Source
from app.services import client_intents,customer_data

STATUS_LABELS={'submitted':'Submitted','in_review':'In review','waiting_on_client':'Waiting for your information',
    'resolved':'Resolved','closed':'Closed'}
CARD_REQUEST=re.compile(r'\b(?:card|credit)\s+limit\b|\blimit\s+(?:increase|change|request)\b',re.I)
REFERENCE=re.compile(r'\bTCK-\d{6,}\b',re.I)
APPROVAL=re.compile(r'\b(?:approved|rejected|approval|decision|accepted|gone through)\b',re.I)

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
    evidence_text='Customer-authorised request records as of '+result.as_of.isoformat()+': '+ '; '.join(
        f'{row.reference}: {row.category_label}, status {row.status}, owner {row.owner_team}' for row in rows[:5])
    evidence=[Evidence(id='retail-tickets',text=evidence_text,
        publisher='DemoBank synthetic request records',scope='Customer-owned request status read. Workflow status is not a banking approval.',
        source=Source(id='retail-tickets',title='Your request records',url=None,version=result.as_of.isoformat(),publisher='DemoBank synthetic request records'))]
    return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking',text=text,
        citations=[Citation(source_id='retail-tickets')]),evidence
