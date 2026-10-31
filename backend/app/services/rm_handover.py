"""Questions the assistant should not answer go straight to the client's relationship manager (migrations 0073, 0082).

Rules, not the model, decide what needs a person: advice that depends on the client's goals (investments, products to
buy), negotiating fees or rates, complaints, complex transfers (large sums, trusts or companies, overseas moves) and
explicit asks for the RM or a person. The assistant then says politely that it cannot help with that part and passes
the question on. The assistant never answers for the RM.

Each handover gets a topic from the same rules (CR8/CR9). If the client already has an open RM ticket on that topic, the
new question is added to it as a client update instead of opening a duplicate, and she gets the same reference.
Otherwise one support handoff (reason general_support) is recorded; its summary is the topic, the client's redacted
question and a short excerpt of her earlier messages in the chat, and SQL opens an 'rm_handover' ticket owned by the RM.
A document she selected in the chat is attached to the ticket. If any of this fails, the client is told plainly that
nothing was sent and how else to reach the bank, instead of seeing a generic error.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID,uuid5
import httpx
from postgrest.exceptions import APIError
from pydantic import TypeAdapter,ValidationError
from app.core import data_api
from app.core.observability import log_event
from app.schemas.actions import HandoffResult
from app.schemas.chat import Message
from app.schemas.contracts import ActionCommand,Priority,Receipt,RequestContext
from app.schemas.tickets import CustomerTicket
from app.schemas.workflows import OwnedWorkflow
from app.services import actions,customer_data
from app.services.actions import store as action_store
from app.services.actions.store import ActionIntegrityError,ActionNotFound
from app.services.authorization import require_permission
from app.services.privacy import redact

ADVICE=re.compile(
    r"\b(?:should|shall|would you recommend|do you recommend|is it (?:a )?good (?:time|idea) to)\b[^.?!]{0,40}\b(?:invest|buy|sell|switch|refinance|put my money)\b"
    r"|\b(?:investment|financial|wealth|portfolio|retirement|estate|tax)\s+(?:advice|planning|plan|recommendations?|strategy)\b"
    r"|\brecommend\b[^.?!]{0,30}\b(?:funds?|stocks?|shares|bonds?|investments?|insurance|policy|products?|portfolio)\b"
    r"|\bwhich\s+(?:funds?|stocks?|shares|bonds?|investments?|insurance|policy)\b",re.I)
FEES=re.compile(
    r"\b(?:waive|waiver|negotiate|reverse|refund)\b[^.?!]{0,30}\b(?:fees?|charges?|interest|rates?)\b"
    r"|\b(?:waive|waiver)\b"
    r"|\b(?:lower|reduce|better|preferential|special)\s+(?:my\s+|the\s+)?(?:interest\s+)?(?:rates?|fees?|pricing)\b",re.I)
COMPLAINT=re.compile(r"\b(?:complain|complaint|unhappy with (?:the )?(?:service|bank))\b",re.I)
CONTACT=re.compile(
    r"\b(?:talk|speak|chat|connect|get in touch)\b[^.?!]{0,25}\b(?:my\s+)?(?:rm|relationship manager|banker|advisor|adviser)\b"
    r"|\b(?:call|email|message)\s+me\b|\bcall\s+(?:me\s+)?back\b",re.I)
# Complex transfers (CR8): a large sum, a trust or company, or moving money between countries or entities. Asking how
# transfers work (CR2) or why one is pending or failed (CR13) stays with the assistant.
LARGE_SUM=r"(?:s?\$|sgd|usd|eur|gbp|chf|hkd)?\s?(?:\d{1,3}(?:,\d{3}){2,}|\d{3},\d{3}|\d{6,}|\d{3,}\s?k\b|\d+(?:\.\d+)?\s?(?:m|million|mil)\b)"
TRANSFER_VERB=r"\b(?:transfer(?:ring)?|move|moving|send(?:ing)?|wire|wiring|remit(?:ting)?)\b"
ENTITY=(r"(?:(?:my|our|the|a)\s+)?(?:family\s+)?(?:trusts?|foundation|holding compan(?:y|ies)|family office|corporate account|business account|entities|jurisdictions)\b"
    r"|(?:my|our)\s+(?:own\s+)?compan(?:y|ies)\b")
STRONG_TRANSFER=re.compile(
    TRANSFER_VERB+r"[^.?!]{0,50}\b(?:to|from|into|out of|between)\s+(?:"+ENTITY+r")"
    r"|"+TRANSFER_VERB+r"[^.?!]{0,30}"+LARGE_SUM
    +r"|\b(?:transfer|move)\b[^.?!]{0,40}\b(?:offshore|abroad|overseas)\b[^.?!]{0,40}\b(?:trust|company|entity|portfolio|assets)\b",re.I)
WEAK_TRANSFER=re.compile(
    r"\b(?:large|big|significant|substantial|complex|complicated|multi[- ]step|cross[- ]border|offshore)\b[^.?!]{0,30}\b(?:transfers?|wires?|remittances?|payments?)\b",re.I)
TRANSFER_STATUS=re.compile(
    r"\b(?:pending|failed|declined|rejected|stuck|status|went through|gone through|arrived|bounced|reversed|still processing|how long)\b"
    r"|\bwhy\s+(?:is|has|did|hasn['’]?t|isn['’]?t|was)\b",re.I)
HOW_TO=re.compile(r"^\s*(?:how\s+(?:do|can|should|would|does)\s+(?:i|you|one|it)|what\s+(?:is|are)\s+the\s+(?:fees?|limits?|charges?|steps|process|cut[- ]?off)"
    r"|can\s+i\s+(?:make|send|do)\s+(?:an?\s+)?(?:international|overseas|foreign|telegraphic)?\s*(?:transfers?|payments?)\b)",re.I)
# Booking a meeting is self-service (CR17: My requests > Meetings), so it is never handed over.
MEETING=re.compile(r'\b(?:meet|meeting|appointment|book(?:ing)?|schedule|catch up)\b',re.I)
# Kept for callers and tests that use the combined rule.
HANDOVER=re.compile('|'.join(f'(?:{p.pattern})' for p in (ADVICE,FEES,COMPLAINT,CONTACT)),re.I)

# Topic labels, most specific first: a complaint about fees is a complaint; asking the RM about investing is advice.
GENERAL_TOPIC='Question for your relationship manager'
TOPIC=re.compile(r'^\[([^\]]{1,60})\]\s*')
LABEL='Your relationship manager'
SORRY=("That's something I'm not able to help with here, as it needs someone who knows your full situation. "
    "I'll pass your question to your relationship manager so they can answer you directly.")
GUEST=("That's something I'm not able to help with here, as it needs someone who knows your full situation. "
    "Please sign in and ask again, and I'll pass it to your relationship manager.")
FAILED=("I couldn't pass this to your relationship manager just now, so nothing has been sent yet. Please try again in a "
    "few minutes, or use Talk to a person at the top of the page. If it's urgent, contact your relationship manager "
    "directly or call the bank's client service line in the official app.")
OPEN=('submitted','in_review','waiting_on_client')
SUMMARY_LIMIT=500
QUESTION_LIMIT=300
EARLIER_LIMIT=140
HANDOVER_ERRORS=(ActionIntegrityError,APIError,httpx.TransportError,ValidationError,PermissionError,
    customer_data.CustomerDataIntegrityError,ValueError)


def complex_transfer(question: str) -> bool:
    if TRANSFER_STATUS.search(question):return False
    if STRONG_TRANSFER.search(question):return True
    return bool(WEAK_TRANSFER.search(question)) and not HOW_TO.search(question)


def needs_person(question: str) -> bool:
    return (bool(HANDOVER.search(question)) or complex_transfer(question)) and not MEETING.search(question)


def topic_for(question: str) -> str:
    if COMPLAINT.search(question):return 'Complaint'
    if complex_transfer(question):return 'Complex transfer'
    if FEES.search(question):return 'Fees or rates'
    if ADVICE.search(question):return 'Investment or product advice'
    if CONTACT.search(question):return 'Request to contact you'
    return GENERAL_TOPIC


def topic_of(summary: str) -> str | None:
    """The topic a ticket was opened with; tickets from before 0082 have none and are never matched."""
    found=TOPIC.match(summary)
    return found.group(1) if found else None


def compact(text: str) -> str:
    return ' '.join(redact(text)[0].split())


# One-tap replies (booking, card choice, confirmations) carry no context for the RM.
BUTTON=re.compile(r'^(?:Book .+|Confirm booking|Send to my RM|Cancel|Open a new request|Add to TCK-\d+|Yes, report it|Yes, report my card|No|'
    r'Both cards|.+ ending \d{4}|more times)$',re.I)


def earlier_messages(question: str,history: Sequence[Message]) -> list[str]:
    """The client's own earlier messages in this chat (never the assistant's replies or records), newest last."""
    messages=[compact(item.content) for item in history if item.role=='user' and not BUTTON.match(item.content.strip())]
    if messages and messages[-1]==compact(question):messages=messages[:-1]
    return [item for item in messages if item][-3:]


def summary_for(question: str,history: Sequence[Message]=(),context: str='') -> str:
    """Topic, the client's redacted question and, room permitting, her earlier messages; at most 500 characters,
    so the whole summary fits the ticket the RM opens (support.tickets.summary)."""
    text=compact(question)[:QUESTION_LIMIT] or 'The client asked to speak with their relationship manager.'
    summary=f'[{topic_for(question)}] Client question for you: {text}'
    if context and len(summary)+len(context)+3<=SUMMARY_LIMIT-60:summary+=' | '+context
    earlier=[item if len(item)<=EARLIER_LIMIT else item[:EARLIER_LIMIT-1]+'…' for item in earlier_messages(question,history)]
    while earlier:
        candidate=summary+' | Earlier in this chat: '+' / '.join(f'“{item}”' for item in earlier)
        if len(candidate)<=SUMMARY_LIMIT:return candidate
        earlier=earlier[1:]
    return summary[:SUMMARY_LIMIT]


import os
WITHHELD=('Try asking in a different way, or say “talk to a person” and I’ll show you what I would send to your relationship '
    'manager before anything is sent.')


def confirm_first() -> bool:
    """CR8: the client confirms a handover before it is sent. RM_HANDOVER_CONFIRM=0 records at once (used by native tests)."""
    return os.getenv('RM_HANDOVER_CONFIRM','1')!='0'


def validation_handoff() -> bool:
    """CR9: withheld answers create no ticket unless VALIDATION_HANDOFF=1 (the original F19 behaviour, kept for its tests)."""
    return os.getenv('VALIDATION_HANDOFF')=='1'


# Confirm before sending (CR8, CR9) ---------------------------------------------------------------------------------
# The client sees exactly what would go to her RM and chooses: send it, add it to her open request on the same topic,
# open a new request anyway, or cancel. Nothing is recorded until she chooses; her choice is read back by rules.
PREVIEW_MARK='Here’s what I’d send to your relationship manager:'
SEND_REPLY='Send to my RM'
NEW_REPLY='Open a new request'
CANCEL_REPLY='Cancel'
QUOTED=re.compile(r'• Your question: “(.+?)”')
OFFERED=re.compile(r'“Add to (TCK-[0-9]{6,})”')
SEND=re.compile(r'^\s*(?:yes|yep|ok|okay|sure)?[,.! ]*(?:please )?(?:send(?: it)?(?: to (?:my )?(?:rm|relationship manager))?|go ahead)\b',re.I)
ADD=re.compile(r'^\s*(?:yes[,.! ]*)?add\b',re.I)
NEW=re.compile(r'^\s*(?:open|create|raise|make|start)\s+(?:a\s+)?(?:new|separate|another)\b|^\s*new (?:request|ticket)\b',re.I)
CANCEL=re.compile(r"^\s*(?:cancel|no|nope|don['’]?t send|do not send|never ?mind|nvm|forget it)\b",re.I)


def preview(question: str,history: Sequence[Message]=(),existing: str | None=None,context: str='') -> str:
    """What would be sent, in the client's own (redacted) words; the choices to make are fixed phrases."""
    text=compact(question)[:QUESTION_LIMIT]
    if not text or (len(text)<60 and re.match(r'^\s*(?:can i |i want to |i need to |please )?(?:talk|speak|connect|chat)\b',text,re.I)):
        text='You asked to speak with your relationship manager.'
    lines=[PREVIEW_MARK,f'• Topic: {topic_for(question)}',f'• Your question: “{text}”']
    earlier=[item if len(item)<=EARLIER_LIMIT else item[:EARLIER_LIMIT-1]+'…' for item in earlier_messages(question,history)]
    if earlier:lines.append('• Earlier in this chat: '+' / '.join(f'“{item}”' for item in earlier))
    if context:lines.append(f'• {context}')
    if existing:
        lines.append(f'You already have an open request about this ({existing}). Reply “Add to {existing}” to add this question to it, '
            f'“{NEW_REPLY}” to send it separately, or “{CANCEL_REPLY}”.')
    else:
        lines.append(f'Reply “{SEND_REPLY}” to send it, or “{CANCEL_REPLY}” if you’d rather not. Nothing is sent until you choose.')
    return '\n'.join(lines)


def pending_choice(history: Sequence[Message]) -> tuple[str,str,str | None] | None:
    """(question, send|add|new|cancel, offered reference) when her latest message answers our preview."""
    if len(history)<2 or history[-1].role!='user' or history[-2].role!='assistant' or PREVIEW_MARK not in history[-2].content:return None
    found=QUOTED.search(history[-2].content)
    if not found:return None
    reply=history[-1].content
    offered=OFFERED.search(history[-2].content)
    reference=offered.group(1) if offered else None
    if CANCEL.search(reply):choice='cancel'
    elif ADD.search(reply) and reference:choice='add'
    elif NEW.search(reply):choice='new'
    elif SEND.search(reply):choice='new' if reference else 'send'
    else:return None
    return found.group(1),choice,reference


async def offer(question: str,history: Sequence[Message],ctx: RequestContext) -> str:
    """The preview; an open RM ticket on the same topic is offered rather than silently reused."""
    try:existing=await open_ticket_for(topic_for(question),ctx)
    except HANDOVER_ERRORS:existing=None
    return preview(question,history,existing,await open_requests_line(ctx))


async def choose(question: str,choice: str,reference: str | None,ctx: RequestContext,owned: OwnedWorkflow | None,task_id: str,
        history: Sequence[Message]=(),document_ids: Sequence[UUID]=()) -> str:
    """Carries out her choice. history is the chat before our preview, so the summary matches what she was shown."""
    if choice=='cancel':return 'No problem, nothing has been sent to your relationship manager.'
    try:
        if choice=='add' and reference:
            await follow_up(reference,question,ctx)
            if document_ids:await attach(reference,document_ids,ctx)
            return handed_over(reference,reused=True)
        done=await recorded(question,'P2',ctx,task_id)
        if done is not None:return done.reply()
        _,new_reference,created=await hand_over(question,'P2',ctx,owned,task_id,history)
        if not created:new_reference=await open_separate(summary_for(question,history,await open_requests_line(ctx)),ctx)
        if new_reference and document_ids:await attach(new_reference,document_ids,ctx)
        return handed_over(new_reference)
    except HANDOVER_ERRORS as error:
        log_event('handover_failed',error_type=type(error).__name__)
        return FAILED


def task_key(name: str,turn: UUID) -> str:
    """Workflow task ids are at most 40 characters; the turn fragment keeps later turns from reusing a handoff."""
    return f'{name}_{turn.hex[:12]}'[:40]


def handed_over(reference: str | None,reused: bool=False) -> str:
    where=f' ({reference})' if reference else ''
    if reused:
        return (f"You already have an open request with your relationship manager about this{where}, so I haven't opened "
            "another one. I've added your question to it, and they'll reply there. You can follow it in My requests.")
    where=f' Your reference is {reference}.' if reference else ''
    return f"I've passed your question to your relationship manager.{where} They usually reply within one working day, and you can follow it in My requests."


@dataclass(frozen=True)
class Handover:
    reference: str | None
    topic: str
    reused: bool=False
    failed: bool=False

    def reply(self) -> str:
        return FAILED if self.failed else handed_over(self.reference,self.reused)


STATUS_WORDS={'submitted':'sent','in_review':'in review','waiting_on_client':'waiting on client'}


async def open_requests_line(ctx: RequestContext) -> str:
    """CR8: previous actions and ticket status for the RM: her open requests (reference, topic or category, status)."""
    try:
        read=await customer_data.get_tickets(ctx.model_copy(update={'agent':None}))
        tickets=TypeAdapter(list[CustomerTicket]).validate_python(read.data)
    except HANDOVER_ERRORS:return ''
    open_items=[f'{ticket.reference} {topic_of(ticket.summary) or ticket.category_label} ({STATUS_WORDS.get(ticket.status,ticket.status)})'
        for ticket in tickets if ticket.status in OPEN][:3]
    return ('Open requests: '+'; '.join(open_items)) if open_items else 'No other open requests'


async def ticket_reference(handoff_id: UUID,ctx: RequestContext) -> str | None:
    read=await customer_data.get_tickets(ctx.model_copy(update={'agent':None}))
    tickets=TypeAdapter(list[CustomerTicket]).validate_python(read.data)
    return next((ticket.reference for ticket in tickets if ticket.handoff_id==handoff_id),None)


async def open_ticket_for(topic: str,ctx: RequestContext) -> str | None:
    """The newest open RM ticket on the same topic (F23: the same client and kind of request is shown, not duplicated)."""
    read=await customer_data.get_tickets(ctx.model_copy(update={'agent':None}))
    tickets=TypeAdapter(list[CustomerTicket]).validate_python(read.data)
    return next((ticket.reference for ticket in tickets if ticket.category=='rm_handover' and ticket.status in OPEN
        and topic_of(ticket.summary)==topic),None)


async def topic_of_reference(reference: str,ctx: RequestContext) -> str | None:
    read=await customer_data.get_tickets(ctx.model_copy(update={'agent':None}))
    tickets=TypeAdapter(list[CustomerTicket]).validate_python(read.data)
    return next((topic_of(ticket.summary) for ticket in tickets if ticket.reference==reference),None)


async def open_separate(summary: str,ctx: RequestContext) -> str:
    """A new RM ticket in this chat for a question on another topic; a retried turn gets the same ticket back."""
    current=ctx.model_copy(update={'agent':None})
    if current.case_id is None:raise ActionIntegrityError('A separate RM ticket needs this chat')
    require_permission(current.principal,'tickets.open_rm',{'customer_id':current.principal.customer_id},current)
    with data_api.connect(current) as db:
        response=await data_api.execute(lambda:db.rpc('customer_open_rm_ticket',{'p_customer_id':current.principal.customer_id,
            'p_case_id':str(current.case_id),'p_summary':summary[:SUMMARY_LIMIT]}).execute(),'tickets.open_rm')
    reference=response.data.get('reference') if isinstance(response.data,dict) else None
    if not isinstance(reference,str) or not reference.startswith('TCK-'):raise ActionIntegrityError('No ticket reference was returned')
    return reference


async def follow_up(reference: str,question: str,ctx: RequestContext) -> bool:
    """Adds the client's new question to her open RM ticket as a visible update; a repeated note is not added twice."""
    current=ctx.model_copy(update={'agent':None})
    require_permission(current.principal,'tickets.follow_up',{'customer_id':current.principal.customer_id},current)
    note=compact(question)[:480]
    with data_api.connect(current) as db:
        response=await data_api.execute(lambda:db.rpc('customer_ticket_follow_up',{'p_customer_id':current.principal.customer_id,
            'p_reference':reference,'p_note':note}).execute(),'tickets.follow_up')
    if not isinstance(response.data,dict) or response.data.get('reference')!=reference:
        raise ActionIntegrityError('The follow-up was recorded on another ticket')
    return bool(response.data.get('added'))


async def attach(reference: str,document_ids: Sequence[UUID],ctx: RequestContext) -> int:
    """Links up to five of the client's own documents to her open RM ticket (0082 checks both ownerships)."""
    if not document_ids:return 0
    current=ctx.model_copy(update={'agent':None})
    require_permission(current.principal,'tickets.attach',{'customer_id':current.principal.customer_id},current)
    ids=[str(item) for item in dict.fromkeys(document_ids)][:5]
    with data_api.connect(current) as db:
        response=await data_api.execute(lambda:db.rpc('customer_attach_ticket_documents',{'p_customer_id':current.principal.customer_id,
            'p_reference':reference,'p_document_ids':ids}).execute(),'tickets.attach')
    if not isinstance(response.data,dict) or response.data.get('reference')!=reference:
        raise ActionIntegrityError('The attachment was recorded on another ticket')
    return int(response.data.get('attached') or 0)


def command_for(question: str,priority: Priority,ctx: RequestContext,task_id: str,history: Sequence[Message]=(),context: str='') -> ActionCommand:
    if ctx.case_id is None:raise ActionIntegrityError('A handover needs an owned conversation case')
    return ActionCommand(command_id=uuid5(ctx.case_id,task_id+':handoff.create'),type='handoff.create',
        target={'reason':'general_support','priority':priority,'summary':summary_for(question,history,context)},
        idempotency_key=f'{ctx.case_id}:{task_id}:handoff.create')


async def hand_over(question: str,priority: Priority,ctx: RequestContext,owned: OwnedWorkflow | None,task_id: str,
        history: Sequence[Message]=()) -> tuple[Receipt,str | None,bool]:
    """One deduplicated handoff per task id (callers include the turn, so retries reuse it); returns its receipt,
    the RM ticket reference and whether the handoff was newly created (False: this chat already had one open)."""
    current=ctx.model_copy(update={'agent':None})
    command=command_for(question,priority,ctx,task_id,history,await open_requests_line(current))
    if owned is not None:
        outcome=await actions.propose_for_workflow(command,task_id,owned,current)
        receipt=actions.receipt_for(outcome)
    else:
        receipt=await actions.run_action(command,current,False)
        with data_api.connect(current) as db:outcome=await action_store.load(command.command_id,current,db)
    if not isinstance(receipt,Receipt) or receipt.outcome!='succeeded' or outcome.receipt is None or not isinstance(outcome.receipt.result,HandoffResult):
        raise ActionIntegrityError('The relationship manager handover did not return a successful receipt')
    return receipt,await ticket_reference(outcome.receipt.result.handoff_id,current),outcome.receipt.result.created


async def recorded(question: str,priority: Priority,ctx: RequestContext,task_id: str) -> Handover | None:
    """A retried turn finds the handoff it already recorded for this task, so it neither follows up nor opens twice."""
    current=ctx.model_copy(update={'agent':None})
    command=command_for(question,priority,ctx,task_id)
    try:
        with data_api.connect(current) as db:outcome=await action_store.load(command.command_id,current,db)
    except ActionNotFound:return None
    if outcome.receipt is None or outcome.receipt.outcome!='succeeded' or not isinstance(outcome.receipt.result,HandoffResult):return None
    reference=await ticket_reference(outcome.receipt.result.handoff_id,current)
    # The chat's handover was on another topic: this question got (or gets) its own ticket, found again by route().
    if reference is None or await topic_of_reference(reference,current)!=topic_for(question):return None
    return Handover(reference=reference,topic=topic_for(question),reused=not outcome.receipt.result.created)


async def route(question: str,priority: Priority,ctx: RequestContext,owned: OwnedWorkflow | None,task_id: str,
        history: Sequence[Message]=(),document_ids: Sequence[UUID]=()) -> Handover:
    """Reuse an open ticket on the same topic or open a new one; attach selected documents; never raise for the client."""
    topic=topic_for(question)
    try:
        done=await recorded(question,priority,ctx,task_id)
        if done is not None:return done
        reference=await open_ticket_for(topic,ctx)
        reused=reference is not None
        if reference is not None:await follow_up(reference,question,ctx)
        else:
            _,reference,created=await hand_over(question,priority,ctx,owned,task_id,history)
            # This chat already had an open handover. Same topic: add this question to it. A different topic is a
            # different request, so it gets its own ticket (0085).
            if not created and reference is not None:
                if await topic_of_reference(reference,ctx)==topic:
                    reused=True
                    await follow_up(reference,question,ctx)
                else:
                    reference=await open_separate(summary_for(question,history),ctx)
        if reference is not None and document_ids:await attach(reference,document_ids,ctx)
        return Handover(reference=reference,topic=topic,reused=reused)
    except HANDOVER_ERRORS as error:
        code=data_api.database_code(error) if isinstance(error,APIError) else None
        log_event('handover_failed',error_type=type(error).__name__,db_code=code)
        return Handover(reference=None,topic=topic,failed=True)
