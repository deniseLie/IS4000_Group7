"""Questions the assistant should not answer go straight to the client's relationship manager (migration 0073).

Rules, not the model, decide what needs a person: advice that depends on the client's goals (investments, products to
buy), negotiating fees or rates, complaints, and explicit asks for the RM or a person. The assistant then says politely
that it cannot help with that part, records one support handoff (reason general_support) whose summary is the client's
redacted question, and SQL opens an 'rm_handover' ticket owned by the RM. The assistant never answers for the RM.
"""
import re
from uuid import UUID,uuid5
from pydantic import TypeAdapter
from app.schemas.contracts import ActionCommand,Priority,Receipt,RequestContext
from app.schemas.actions import HandoffResult
from app.schemas.tickets import CustomerTicket
from app.schemas.workflows import OwnedWorkflow
from app.core import data_api
from app.services import actions,customer_data
from app.services.actions import store as action_store
from app.services.actions.store import ActionIntegrityError

HANDOVER=re.compile(
    r"\b(?:should|shall|would you recommend|do you recommend|is it (?:a )?good (?:time|idea) to)\b[^.?!]{0,40}\b(?:invest|buy|sell|switch|refinance|put my money)\b"
    r"|\b(?:investment|financial|wealth|portfolio|retirement|estate|tax)\s+(?:advice|planning|plan|recommendations?|strategy)\b"
    r"|\brecommend\b[^.?!]{0,30}\b(?:funds?|stocks?|shares|bonds?|investments?|insurance|policy|products?|portfolio)\b"
    r"|\bwhich\s+(?:funds?|stocks?|shares|bonds?|investments?|insurance|policy)\b"
    r"|\b(?:waive|waiver|negotiate|reverse|refund)\b[^.?!]{0,30}\b(?:fees?|charges?|interest|rates?)\b"
    r"|\b(?:lower|reduce|better|preferential|special)\s+(?:my\s+|the\s+)?(?:interest\s+)?(?:rates?|fees?|pricing)\b"
    r"|\b(?:complain|complaint|unhappy with (?:the )?(?:service|bank))\b"
    r"|\b(?:talk|speak|chat|connect|get in touch)\b[^.?!]{0,25}\b(?:my\s+)?(?:rm|relationship manager|banker|advisor|adviser)\b"
    r"|\b(?:call|email|message)\s+me\b|\bcall\s+(?:me\s+)?back\b",re.I)
LABEL='Your relationship manager'
SORRY=("That's something I'm not able to help with here, as it needs someone who knows your full situation. "
    "I'll pass your question to your relationship manager so they can answer you directly.")
GUEST=("That's something I'm not able to help with here, as it needs someone who knows your full situation. "
    "Please sign in and ask again, and I'll pass it to your relationship manager.")


def needs_person(question: str) -> bool:
    return bool(HANDOVER.search(question))


def summary_for(question: str) -> str:
    """Only the client's own (already redacted) words, bounded; never model text or records."""
    text=' '.join(question.split())[:900] or 'The client asked to speak with their relationship manager.'
    return 'Client question for you: '+text


def task_key(name: str,turn: UUID) -> str:
    """Workflow task ids are at most 40 characters; the turn fragment keeps later turns from reusing a handoff."""
    return f'{name}_{turn.hex[:12]}'[:40]


def handed_over(reference: str | None) -> str:
    where=f' Your reference is {reference}.' if reference else ''
    return f"I've passed your question to your relationship manager.{where} They usually reply within one working day, and you can follow it in My requests."


async def ticket_reference(handoff_id: UUID,ctx: RequestContext) -> str | None:
    read=await customer_data.get_tickets(ctx.model_copy(update={'agent':None}))
    tickets=TypeAdapter(list[CustomerTicket]).validate_python(read.data)
    return next((ticket.reference for ticket in tickets if ticket.handoff_id==handoff_id),None)


async def hand_over(question: str,priority: Priority,ctx: RequestContext,owned: OwnedWorkflow | None,task_id: str) -> tuple[Receipt,str | None]:
    """One deduplicated handoff per task id (callers include the turn, so retries reuse it); returns its receipt and the RM ticket reference."""
    if ctx.case_id is None:raise ActionIntegrityError('A handover needs an owned conversation case')
    command=ActionCommand(command_id=uuid5(ctx.case_id,task_id+':handoff.create'),type='handoff.create',
        target={'reason':'general_support','priority':priority,'summary':summary_for(question)},
        idempotency_key=f'{ctx.case_id}:{task_id}:handoff.create')
    current=ctx.model_copy(update={'agent':None})
    if owned is not None:
        outcome=await actions.propose_for_workflow(command,task_id,owned,current)
        receipt=actions.receipt_for(outcome)
    else:
        receipt=await actions.run_action(command,current,False)
        with data_api.connect(current) as db:outcome=await action_store.load(command.command_id,current,db)
    if not isinstance(receipt,Receipt) or receipt.outcome!='succeeded' or outcome.receipt is None or not isinstance(outcome.receipt.result,HandoffResult):
        raise ActionIntegrityError('The relationship manager handover did not return a successful receipt')
    return receipt,await ticket_reference(outcome.receipt.result.handoff_id,current)
