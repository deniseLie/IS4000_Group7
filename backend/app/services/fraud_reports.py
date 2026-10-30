"""Urgent reports to the fraud team (CR12, migrations 0081 and 0082).

Charges the client does not recognise: the assistant first lists the charges it found in her records and asks her to
confirm them ("Yes, report it"); only then does the next turn record the P0 handoff and quote its ticket reference.
The list and the confirmation are rules over her records and her own reply, never model output. The reference to each
charge in the list is the transaction's own number, so the confirmation turn re-reads exactly those charges.

A lost or stolen card reported by a signed-in client in a saved chat is passed to the fraud team straight away as an
urgent ticket (handoff reason card_lost), because there is nothing to confirm and every minute counts. An open lost-card
ticket from another chat is reused instead of opening a second one.

The assistant never blocks, cancels or replaces a card and never decides anything; the fraud team does.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid5
import httpx
from postgrest.exceptions import APIError
from pydantic import TypeAdapter,ValidationError
from app.core import data_api
from app.core.observability import log_event
from app.schemas.actions import HandoffResult
from app.schemas.chat import Message
from app.schemas.contracts import ActionCommand,AgentResult,Receipt,RequestContext,Task
from app.schemas.events import Source
from app.schemas.fraud import DisputeProposal,FraudProposal,SecurityRecords,SecurityTransaction
from app.schemas.tickets import CustomerTicket
from app.services import actions,customer_data
from app.services.actions import store as action_store
from app.services.actions.store import ActionIntegrityError
from app.services.agents import fraud_records
from app.services.privacy import redact
from app.services.rm_handover import OPEN,ticket_reference

CONFIRM_REPLY='Yes, report it'
ASK_HEADER=re.compile(r'charges you don[’\']t recognise on your card ending ([0-9]{4}):')
REFERENCE=re.compile(r'\(ref\. ([1-9][0-9]{0,17})\)')
AFFIRM=re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|confirm(?:ed)?|that(?:['’]?s| is) (?:right|correct|it|them|the one|the ones)"
    r"|(?:please\s+)?(?:report|send) (?:it|them)|go ahead|ok(?:ay)?,? (?:report|send))\b",re.I)
NEGATION=re.compile(r"\b(?:no|not|don['’]?t|isn['’]?t|aren['’]?t|wrong|wait|only|except|but)\b",re.I)
LOST_TITLE='Card reported lost or stolen'
HANDOFF_ERRORS=(ActionIntegrityError,APIError,httpx.TransportError,ValidationError,PermissionError,
    customer_data.CustomerDataIntegrityError,ValueError)


@dataclass(frozen=True)
class Confirmation:
    last4: str
    transaction_ids: tuple[str,...]

    def encoded(self) -> str:
        return self.last4+':'+','.join(self.transaction_ids)

    @classmethod
    def decode(cls,value: str) -> 'Confirmation':
        last4,_,ids=value.partition(':')
        found=tuple(item for item in ids.split(',') if re.fullmatch(r'txn_[1-9][0-9]{0,17}',item))
        if not re.fullmatch(r'[0-9]{4}',last4) or not found:raise ValueError('Malformed fraud confirmation')
        return cls(last4=last4,transaction_ids=found)


def charge_line(item: SecurityTransaction) -> str:
    return f'• {item.date:%d %b %Y}, {item.description}, {item.currency} {abs(item.amount):,.2f} (ref. {item.txn_id[4:]})'


def ask_text(last4: str,charges: Sequence[SecurityTransaction]) -> str:
    """Fixed wording; the header and references are what the next turn reads back (pending())."""
    lines='\n'.join(charge_line(item) for item in charges)
    return (f'Before I send this to our fraud team, please check these are the charges you don’t recognise on your card ending {last4}:\n'
        f'{lines}\n'
        f'If they are, reply “{CONFIRM_REPLY}” and I’ll pass them to the fraud team as urgent. If something is different, tell me '
        'which charge you mean. Don’t wait for me if your card is at risk: call the fraud line on the back of your card or use '
        'the report function in the official app now.')


def pending(history: Sequence[Message]) -> list[Confirmation]:
    """The cards and charges the client just confirmed: her latest message is a plain yes to our previous ask."""
    if len(history)<2 or history[-1].role!='user' or history[-2].role!='assistant':return []
    reply=history[-1].content.strip()
    if len(reply)>120 or not AFFIRM.search(reply) or NEGATION.search(reply):return []
    asked=history[-2].content
    if CONFIRM_REPLY not in asked:return []
    headers=list(ASK_HEADER.finditer(asked))
    found: list[Confirmation]=[]
    for index,header in enumerate(headers):
        block=asked[header.end():headers[index+1].start() if index+1<len(headers) else len(asked)]
        ids=tuple(dict.fromkeys('txn_'+number for number in REFERENCE.findall(block)))
        if ids:found.append(Confirmation(last4=header.group(1),transaction_ids=ids))
    return found


def charges_for(records: SecurityRecords,card_id: str,ids: Sequence[str]) -> list[SecurityTransaction]:
    wanted=set(ids)
    return sorted((row for row in records.transactions if row.txn_id in wanted and row.card_id==card_id and row.amount<0),
        key=lambda row:int(row.txn_id[4:]))


async def confirmed(task: Task,ctx: RequestContext) -> tuple[AgentResult,FraudProposal | None,DisputeProposal | None]:
    """No model call: re-read exactly the confirmed charges from her records and hand them to report_to_fraud_team."""
    choice=Confirmation.decode(str(task.entities['fraud_confirmed']))
    records=await fraud_records.load(ctx.model_copy(update={'agent':'fraud'}))
    card=next((item for item in records.cards if item.last4==choice.last4),None)
    rows=charges_for(records,card.card_id,choice.transaction_ids) if card is not None else []
    if card is None or len(rows)!=len(choice.transaction_ids):
        return AgentResult(task_id=task.task_id,agent='fraud',status='needs_human',answer_kind='conversation',
            text=('I couldn’t match those charges to your records any more, so nothing has been sent yet. Please tell me again '
                'which charge you don’t recognise, or call the fraud line on the back of your card now.')),None,None
    ids=[row.txn_id for row in rows]
    return (AgentResult(task_id=task.task_id,agent='fraud',status='needs_human',answer_kind='conversation',
        text='Thank you for confirming the charges.'),FraudProposal(card_id=card.card_id,transaction_id=ids[-1]),
        DisputeProposal(card_id=card.card_id,transaction_ids=ids))


async def ask_to_confirm(task: Task,proposal: FraudProposal,dispute: DisputeProposal | None,ctx: RequestContext) -> str | None:
    """The charge list for the client to confirm; None when the records no longer show the reported charge."""
    records=await fraud_records.load(ctx.model_copy(update={'agent':'fraud'}))
    card=next((item for item in records.cards if item.card_id==proposal.card_id),None)
    ids=dispute.transaction_ids if dispute is not None and dispute.card_id==proposal.card_id else [proposal.transaction_id]
    rows=charges_for(records,proposal.card_id,ids) if card is not None else []
    return ask_text(card.last4,rows) if card is not None and rows else None


def records_source(ctx: RequestContext) -> Source:
    return Source(id='customer-records',title='Authenticated synthetic card and transaction records',url=None,
        version=ctx.received_at.isoformat(),publisher='DemoBank synthetic records')


# Lost or stolen card ----------------------------------------------------------------------------------------------
LOST_SENT=('I’ve also passed this to our fraud team as urgent{reference}. They will contact you, block the card if it is still '
    'active and arrange a replacement. Don’t wait for them: call the fraud line on the back of your card or use the official '
    'app now. You can follow it in My requests.')
LOST_REUSED=('You already have an open lost-card report with our fraud team{reference}, so I haven’t opened another one. Don’t '
    'wait for them: call the fraud line on the back of your card or use the official app now. You can follow it in My requests.')
LOST_FAILED=('I couldn’t pass this to our fraud team just now, so nothing has been sent from here. Please call the fraud line on '
    'the back of your card or use the report function in the official app straight away.')


@dataclass(frozen=True)
class LostCardReport:
    reference: str | None
    reused: bool=False
    failed: bool=False

    def reply(self) -> str:
        if self.failed:return LOST_FAILED
        where=f' (reference {self.reference})' if self.reference else ''
        return (LOST_REUSED if self.reused else LOST_SENT).format(reference=where)


def lost_summary(question: str,records: SecurityRecords) -> str:
    cards=', '.join(f'card ending {card.last4} ({card.status})' for card in records.cards[:5]) or 'no cards on record'
    words=' '.join(redact(question)[0].split())[:400]
    return f'{LOST_TITLE} by the client in the assistant. Cards on record: {cards}. Client’s words: “{words}”'[:1000]


async def open_lost_card_ticket(ctx: RequestContext) -> str | None:
    read=await customer_data.get_tickets(ctx.model_copy(update={'agent':None}))
    tickets=TypeAdapter(list[CustomerTicket]).validate_python(read.data)
    return next((ticket.reference for ticket in tickets if ticket.category=='suspected_fraud' and ticket.status in OPEN
        and ticket.summary.startswith(LOST_TITLE)),None)


async def report_lost_card(question: str,ctx: RequestContext,task_id: str) -> LostCardReport:
    """One urgent card_lost handoff per chat (retries reuse it) and one open lost-card ticket across chats."""
    current=ctx.model_copy(update={'agent':None})
    try:
        if ctx.case_id is None:raise ActionIntegrityError('A lost-card report needs an owned conversation case')
        existing=await open_lost_card_ticket(current)
        command_id=uuid5(ctx.case_id,task_id+':handoff.create')
        if existing is not None:
            # A retry of this same turn finds its own handoff's ticket; that is not a reuse from another chat.
            try:
                with data_api.connect(current) as db:outcome=await action_store.load(command_id,current,db)
                if outcome.receipt is not None and isinstance(outcome.receipt.result,HandoffResult):
                    return LostCardReport(reference=await ticket_reference(outcome.receipt.result.handoff_id,current))
            except action_store.ActionNotFound:pass
            return LostCardReport(reference=existing,reused=True)
        records=await fraud_records.load(ctx.model_copy(update={'agent':'fraud'}))
        command=ActionCommand(command_id=command_id,type='handoff.create',
            target={'reason':'card_lost','priority':'P0','summary':lost_summary(question,records)},
            idempotency_key=f'{ctx.case_id}:{task_id}:handoff.create')
        receipt=await actions.run_action(command,current,False)
        with data_api.connect(current) as db:outcome=await action_store.load(command.command_id,current,db)
        if not isinstance(receipt,Receipt) or receipt.outcome!='succeeded' or outcome.receipt is None or not isinstance(outcome.receipt.result,HandoffResult):
            raise ActionIntegrityError('The lost-card report did not return a successful receipt')
        return LostCardReport(reference=await ticket_reference(outcome.receipt.result.handoff_id,current),
            reused=not outcome.receipt.result.created)
    except HANDOFF_ERRORS as error:
        code=data_api.database_code(error) if isinstance(error,APIError) else None
        log_event('lost_card_report_failed',error_type=type(error).__name__,db_code=code)
        return LostCardReport(reference=None,failed=True)
