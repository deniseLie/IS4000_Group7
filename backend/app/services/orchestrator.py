"""v5 LangGraph (build plan phases 5 and 7; architecture §14): planner → agent (Send ×N) → gather → [agent (wave 2) →
gather →] compose, and planner → act → compose when the client answers an open offer.

The planner tries English rules first (an answer to an open offer, an answer to our own question, the selected record,
urgent card and login wording, an explicit request for a person, greetings), then one planner/v12 call, then the rule
overrides and a plan check against registry.CHAT_SPECS. Each task runs on its agent through runner.run_agent, except an
urgent card task (a lost card or a charge she doesn't recognise), which gets fixed steps and one urgent RM request
offer from code, never the model (build plan 2b: there is no Security agent). Agents never
write: what they propose is saved by compose as one offer group, and only a later confirmation (act) runs it. There is
no checkpointer: a crash fails the turn and the client retries, which is safe because offers are idempotent.

Agents never call each other (§14.6). A planned task may depend on an earlier one: gather runs it in a second wave with
only the exports it imports, only when its condition export is true ("unknown means no"), once per item of a list
export (at most 5), and hands an out-of-scope question to one allowed agent once. Offers of a dependent task run after
its upstream's offers and receive their receipt (after_receipt).
"""
import operator
import re
from collections.abc import AsyncIterator
from typing import Annotated, Literal, TypedDict
from uuid import UUID
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, ValidationError
from app.core.config import settings
from app.core.observability import evidence_sink, log_event, span
from app.schemas.actions import ActionOutcome, HandoffResult
from app.schemas.consent_actions import ConsentGrantV2Result
from app.schemas.agents import Evidence
from app.schemas.chat import ChatRequest, Message
from app.schemas.contracts import OPEN_INTENT, AgentOutput, AgentResult, Domain, Export, OfferProposal, PlanTask, Priority, RequestContext, RiskAssessment, TargetAgent
from app.schemas.events import ActionRequiredEvent, AnswerEvent, AnswerSection, OfferGroupEvent, PlanEvent, PlannedSection, Source
from app.schemas.meeting_requests import MeetingRequestResult
from app.schemas.talking_points import InsightShareResult
from app.schemas.tickets import TicketResult
from app.services import audit, limit_context, llm_gateway, offers, prompts, rm_handover, talking_points, typos, client_intents
from app.services.llm_gateway import LLMError
from app.services.agents import card_answers, client_guidance, compliance, consent_answers, derived, meeting_answers, registry, request_status_answers, runner, rules  # noqa: F401 (rules registers)
from app.services.agents.answer import WITHHELD
from app.services.answers import NOTICE, PINNED, clean_history
from app.services.guardrail import FRAUD

MAX_TASKS = 5
WITH_RELATIONSHIP = rules.WITH_RELATIONSHIP  # an Accounts task's entity, set only by checked()
MAX_ITEMS = 5  # fan-out cap per list export
MULTIPLE = re.compile(r'\b(?:and|also|plus|another|as well)\b|;|\?\s*\S|,\s+[A-Za-z]', re.I)
GREETING = re.compile(r'\s*(?:hi|hello|hey|thanks|thank you)[!.\s]*', re.I)
LOGIN = re.compile(r"\b(?:locked\s+out|lockout|log\s*in|sign\s*in|password|passcode|can['’]?t\s+(?:get\s+into|access|open)\s+(?:the|my)\s+app"
    r"|(?:app|account)\s+(?:is\s+|was\s+|got\s+|has\s+been\s+)?locked|otps?|one[- ]time\s+(?:codes?|passwords?))\b", re.I)
PHISHING = re.compile(r'\b(?:phishing|scams?|suspicious\s+(?:sms|text|message|email|link|call))\b', re.I)
LOST = re.compile(r'\b(?:lost|stolen|missing)\b[^.?!]{0,30}\bcards?\b|\bcards?\b[^.?!]{0,30}\b(?:lost|stolen|missing)\b', re.I)
CHARGE = re.compile(r'\bcharges?\b', re.I)
RELATED = re.compile(r'\b(?:related|connected|linked|because of|caused by)\b', re.I)
DEADLINE = re.compile(r'\b(?:deadline|overdue|expir(?:ation|e[sd]?|ing|y)|time left)\b|\bdue\b(?!\s+(?:to|diligence)\b)', re.I)
DOCUMENTS = re.compile(r'\b(?:documents?|docx|pdf|files?|upload|pages)\b', re.I)
TOPICS: dict[TargetAgent, re.Pattern[str]] = {
    'accounts': re.compile(r'\b(?:accounts?|balances?|loans?|cards?|payments?|transfers?|deposits?|statements?'
        # A charge, fee or spending question is a records question; disowned charges are caught as urgent first (urgent_task).
        r'|charged|charges?|fees?|transactions?|spent|spend|spending|purchases?)\b', re.I),
    'compliance': re.compile(r'\b(?:compliance|review|checklist|notice|documents?|docx|pdf|files?|upload|pages)\b'
        r'|\bletter\b[^.!?]{0,100}\b(?:update|provide|send)\b[^.!?]{0,80}\b(?:info(?:rmation)?|documents?|details)\b', re.I),
    'relationship': re.compile(r'\b(?:relationship manager|advisor|adviser|meeting|appointment|handover)\b|\bRMs?\b|\bmeet (?:her|him|them|my)\b'
        r'|\b(?:old|previous|former|new)\s+(?:guy|lady|person|rm|banker|manager|advisor|adviser)\b|\bmy (?:open |pending )?requests?\b', re.I),
}
# Asking a person at the bank to do something is relationship intake even when it names an account word such as "statement".
RM_REQUEST = re.compile(r'\b(?:can|could|would)\s+(?:someone|somebody|my (?:rm|relationship manager|advisor|adviser|banker))\s+(?:please\s+)?'
    r'(?:send|arrange|prepare|get|call|email|post)\b', re.I)
# Passing something on to her RM ("tell my RM so she knows why it's late", P07) is a handover of that one Relationship task;
# it keeps the task's link, so it is not in rm_handover's whole-message rules (which would replace the plan with one handover).
TELL_RM = re.compile(r'\b(?:tell|let|inform|notify|update|warn|alert)\s+(?:my\s+|the\s+)?(?:rm|relationship manager|banker|advis[eo]r)\b', re.I)
ACCOUNT_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('transfer_status', re.compile(r'\b(?:transfers?|pending|landlord|payee|arrive|go through)\b', re.I)),
    ('card_limit', re.compile(r'\b(?:card|credit)\s+limit\b|\blimit\s+(?:increase|change)\b', re.I)),
    ('portfolio', re.compile(r'\b(?:portfolio|investments?|holdings?|time\s+deposits?|fixed\s+deposits?|matur\w*)\b', re.I)),  # 0116
    ('loans', re.compile(r'\bloans?\b', re.I)), ('statements', re.compile(r'\bstatements?\b', re.I)),
    ('transactions', re.compile(r'\b(?:transactions?|spent|spend|spending|purchases?|charged|charges?)\b', re.I)),
    ('card_fees', re.compile(r'\bfees?\b', re.I)), ('card_figures', re.compile(r'\bcards?\b', re.I)),
    ('account_balance', re.compile(r'\b(?:accounts?|balances?|savings|deposits?)\b', re.I)))
REPHRASE = ('Sorry, I couldn’t work out what you need just now. Could you ask again in a different way, or say “talk to a person” '
    'and I’ll show you what I would send to your relationship manager?')
CONFIRM_LINE = 'Reply “yes” to confirm or “no” to cancel. Nothing happens until you confirm, and this expires in 30 minutes.'
EXPIRED = 'That offer has expired or was already answered, so nothing was sent. Ask again if you still want this.'
CANCELLED = 'Okay, nothing has been sent.'
STEP_FAILED = 'That couldn’t be completed, so nothing was changed. Please try again, or use the official app.'
DONE = 'Done. You can see the receipt in Activity.'
GRANTED = 'Done: {rm} can now see your {label} {until}. You can switch it off on Data sharing at any time.'
GRANT_UNTIL = {'date': 'until {date}', 'meeting': 'until your meeting ends', 'request': 'until your meeting request is answered'}
TICKET_RAISED = 'Done: your {label} request is raised (reference {reference}). You can follow it in Requests.'
MEETING_SENT = ('Sent: your meeting request is with your relationship manager. They will answer by {respond_by} (Singapore time): '
    'they accept one of your times, suggest another or reply in a message. You can follow it in the Meetings page.')  # 0106
SHARED = ('Done: your relationship manager can now see “{line}”, and nothing else from this chat. You can remove it at any time '
    'under Data sharing.')  # 0108, lane 2
LINKED = ' It is linked to your earlier request {reference}.'
CHAIN_STOPPED = 'I didn’t do this next step, because the step before it didn’t go through. Nothing was changed for it.'
# Dependencies (§14.6): fixed wording, never the planner's text.
UNLINKED = 'I couldn’t link one part of your message to the rest, so I haven’t done it. Could you ask it on its own?'
UPSTREAM_UNKNOWN = 'I couldn’t check what this part depends on, so I haven’t gone ahead with it. Ask again on its own if you still want it.'
CONDITION_FALSE: dict[str, str] = {
    'due_in_window': 'None of your card or loan payments falls due during your trip, so I haven’t gone ahead with that part.',
    'below_threshold': 'You would still have at least the amount you named in savings after paying your card balances, so I haven’t gone ahead with that part.',
    'check_failed': 'Your document passed its preliminary check, so I haven’t gone ahead with that part.',
    'consent_active': 'The client hasn’t shared a handover note with you under an active consent, so nothing from one is included.'}  # RM (R23)
CONDITION_FALSE_ANY = 'What you asked this to depend on isn’t the case, so I haven’t gone ahead with that part.'
MORE_ITEMS = 'I’ve shown the first {shown} of {total}. Ask about the others if you need them.'
OUT_OF_SCOPE = 'I can’t help with that part here. Say “talk to a person” and I’ll show you what I would send to your relationship manager.'
# Our own fixed asks a short yes or no answers; with no open offer left, the reply says so instead of guessing.
ASK_WHICH = 'You have more than one open offer. Use the Confirm or Cancel button on the offer you mean; nothing has been sent.'
OFFER_MARKS = (CONFIRM_LINE, 'Yes, report it', 'Yes, report my card')  # the last two: fraud-team asks in chats from before 2b
DOMAIN: dict[TargetAgent, Domain] = {'guide': 'retail', 'accounts': 'retail', 'compliance': 'compliance', 'relationship': 'relationship',
    'rm_client': 'relationship', 'rm_requests': 'relationship', 'rm_calendar': 'relationship'}


class TurnContext(BaseModel):
    """Runtime identity, never graph state: the caller, and the saved turn offers belong to."""
    model_config = ConfigDict(extra='forbid')
    request: RequestContext
    turn_id: UUID | None = None
    conversation_id: UUID | None = None  # the saved chat's session; its open offers can be answered


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group: UUID
    decision: Literal['confirm', 'cancel']
    include: list[UUID] = []  # the switches she turned on (a typed yes turns none on)


class Turn(TypedDict, total=False):
    request: ChatRequest
    history_count: int
    risk: RiskAssessment
    history: list[Message]
    tasks: list[PlanTask]
    confirm: Confirmation | None
    lines: list[AnswerSection]  # fixed sections: the rephrase line, or the outcomes of a confirmed offer group
    outputs: Annotated[list[AgentOutput], operator.add]
    next: list['AgentInput']  # wave 2, from gather; set (even empty) once gather has run, so it runs wave 2 only once
    answer: AnswerEvent


class AgentInput(TypedDict):
    task: PlanTask
    history: list[Message]
    language: str
    inputs: dict[str, Export]  # the upstream exports this agent imports (wave 2 only)


# ---- Planner rules ----

def urgent_wording(question: str) -> bool:
    """A charge she doesn't recognise or a lost, stolen or missing card (her words or the typo-tolerant copy)."""
    probe = typos.normalise(question)
    return bool(FRAUD.search(question) or FRAUD.search(probe) or LOST.search(probe))


def urgent_task(question: str, message: str, task_id: str = 't1') -> PlanTask:
    """Fixed steps and one urgent RM request, answered by code in `agent` (urgent_output), never by a model. The marker is
    a bool set only here: the planner's entity values are strings, so a model plan can never make a task urgent."""
    return PlanTask(id=task_id, agent='relationship', intent=OPEN_INTENT, question=question, priority='P0',
        entities={'urgent': True, 'message': message})


def is_urgent(item: PlanTask) -> bool:
    return item.entities.get('urgent') is True


def domain_of(item: PlanTask) -> Domain:
    return 'fraud' if is_urgent(item) else DOMAIN[item.agent]


def entity_agent(question: str) -> TargetAgent | None:
    """Rules own clear agent assignments, including questions the planner extracted."""
    if urgent_wording(question):return 'relationship'  # simple_task makes it the urgent RM request
    if LOGIN.search(question) or PHISHING.search(question):return 'guide'
    if RM_REQUEST.search(question):return 'relationship'
    if meeting_answers.ASK.search(question) and not meeting_answers.EXISTING.search(question):return 'relationship'  # a meeting request, whatever its topic
    found = [agent for agent, pattern in TOPICS.items() if pattern.search(question)]
    return found[0] if len(found) == 1 else None


def topic_agent(question: str) -> TargetAgent | None:
    return entity_agent(question) or entity_agent(typos.normalise(question))


def intent_for(agent: TargetAgent, question: str) -> str:
    """The listed intent a rule-planned question fits; the model never chooses reads for it."""
    if agent == 'guide' and (LOGIN.search(question) or LOGIN.search(typos.normalise(question)) or PHISHING.search(question)):
        return 'phishing' if PHISHING.search(question) and not LOGIN.search(question) else 'login_help'
    if agent == 'accounts':
        probe = typos.normalise(question)
        return next((intent for intent, pattern in ACCOUNT_INTENTS if pattern.search(probe)), OPEN_INTENT)
    if agent == 'compliance':return 'document_findings' if DOCUMENTS.search(question) else 'review_status'
    if agent == 'relationship':
        if client_intents.request_status(question):return 'request_status'
        if meeting_answers.EXISTING.search(question):return 'appointments'  # the meetings she already has
        if meeting_answers.ASK.search(question) or re.search(r'\b(?:meet|meeting|appointment|book)\b', question, re.I):return 'meeting_request'
        return 'meeting_times' if meeting_answers.AVAILABILITY.search(question) else OPEN_INTENT
    return 'general'


def task(agent: TargetAgent, question: str, **entities) -> PlanTask:
    return PlanTask(id='t1', agent=agent, intent=intent_for(agent, question), question=question, entities=entities)


def simple_task(question: str, language: str) -> PlanTask | None:
    """A clear single-topic English question planned by rules (CLIENT_INTENT_ROUTING=rules and the planner-failure fallback)."""
    if language == 'English' and GREETING.fullmatch(question):return task('guide', question)
    if language != 'English' or MULTIPLE.search(question):return None
    probe = typos.normalise(question)
    if urgent_wording(question):return urgent_task(question, question)
    if rm_handover.needs_person(probe) and not FRAUD.search(probe):return task('relationship', question, handover='rm')
    agent = topic_agent(question)
    return task(agent, question) if agent is not None else None


def critical_task(question: str, language: str) -> PlanTask | None:
    """Only urgent card, sign-in and greeting questions bypass the planner (no keyword-to-answer mapping for ordinary intents)."""
    if language != 'English' or MULTIPLE.search(question):return None
    if GREETING.fullmatch(question):return task('guide', question)
    probe = typos.normalise(question)
    if urgent_wording(question):return urgent_task(question, question)
    # The typo copy reads "locked" as "blocked", so sign-in words are matched on her own words too.
    if LOGIN.search(question) or LOGIN.search(probe):return task('guide', question)
    # A meeting she asks the RM to arrange is a meeting request (2a), never a handover (rm_handover.MEETING).
    if (RM_REQUEST.search(probe) and not rm_handover.MEETING.search(probe)) or rm_handover.needs_person(probe):return task('relationship', question, handover='rm')
    return None


def fixed_task(question: str, req: ChatRequest, history: list[Message] = ()) -> PlanTask | None:
    """Fresh requests answered by rules; they win over the older questions we are still waiting on."""
    # A request-status question is a read, even in an active card-limit draft, so it never starts another application.
    # A short follow-up about the ticket just shown ("the statement one") keeps that ticket (request_status_answers.follow_up).
    status = request_status_answers.follow_up(question, history)
    if req.mode == 'personal' and client_intents.request_status(status):return PlanTask(id='t1', agent='relationship', intent='request_status', question=status)
    # Consent v2 (6e): sharing, stopping or deleting a topic in her own words is a Relationship offer from her records.
    if req.mode == 'personal' and not MULTIPLE.search(question) and consent_answers.wanted(question):
        return PlanTask(id='t1', agent='relationship', intent='consent', question=question)
    if client_guidance.PERSONAL_DETAILS.search(question) or client_guidance.UNCLEAR_HELP.search(question) or client_guidance.NEW_REQUEST.search(question) \
            or (client_guidance.GENERIC_LOAN.search(question) and not client_guidance.LOAN_TYPE.search(question)):
        return PlanTask(id='t1', agent='guide', intent='how_to_request', question=question)
    # An explicit, non-specific compliance help request is a clarification, not a document inspection.
    if req.mode == 'personal' and req.language == 'English' and not MULTIPLE.search(question) and compliance.general_help(question):
        return PlanTask(id='t1', agent='compliance', intent='review_status', question=question)
    return None


def follow_up(question: str, history: list[Message]) -> PlanTask | None:
    """Her answer to a question in our previous reply goes back to that flow, never to the planner."""
    if choice := rm_handover.pending_choice(history):
        quoted, chosen, reference = choice
        return PlanTask(id='t1', agent='relationship', intent=OPEN_INTENT, question=quoted, priority='P2',
            entities={'handover': 'rm', 'handover_choice': chosen, 'handover_reference': reference or '', 'follow_up': True})
    # A reply to "which card do you mean?" goes back to the card answer, however short ("the travel one").
    if not MULTIPLE.search(question) and (card_answers.awaiting_card(history) or limit_context.active(history)):
        return PlanTask(id='t1', agent='accounts', intent='card_figures', question=question, entities={'follow_up': True})
    # Changing the meeting request we just drafted (times, format, what it is about) goes back to the draft (2a).
    if meeting_answers.awaiting(history):
        return PlanTask(id='t1', agent='relationship', intent='meeting_request', question=question, entities={'follow_up': True})
    return None


def rule_plan(req: ChatRequest, risk: RiskAssessment, history: list[Message]) -> list[PlanTask] | None:
    question = risk.redacted_message
    if req.mode == 'personal' and req.record_context is not None:
        selection = req.record_context
        return [PlanTask(id='t1', agent='compliance', intent='document_findings' if selection.kind == 'document' else 'bank_question',
            question=question, entities={'record_context': selection.model_dump(mode='json')})]
    if (fixed := fixed_task(question, req, history)) is not None:return [fixed]
    if req.mode == 'personal' and (answer := follow_up(question, history)) is not None:return [answer]
    if (critical := critical_task(question, req.language)) is not None:return [critical]
    return None


# ---- The planner call, overrides and plan check ----

def catalogue() -> str:
    lines = ['Agents (name: what it answers; intents):']
    for spec in registry.CLIENT_SPECS.values():
        intents = [*spec.intents, *([OPEN_INTENT] if spec.loop_tools else [])]
        exports = f' Exports: {", ".join(f"{n} ({t})" for n, t in spec.exports.items())}.' if spec.exports else ''
        imports = f' Imports: {", ".join(sorted(spec.imports))}.' if spec.imports else ''  # v10: a question needing an import is two tasks
        lines.append(f'- {spec.name}: {spec.answers} Intents: {", ".join(intents)}.{exports}{imports}')
    return '\n'.join(lines)


def plan_schema() -> dict:
    return {'title': 'task_plan', 'type': 'object', 'additionalProperties': False, 'properties': {
        'tasks': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'properties': {
            'agent': {'type': 'string', 'enum': sorted(registry.CLIENT_SPECS)}, 'intent': {'type': 'string'}, 'question': {'type': 'string'},
            'entities': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                'properties': {'name': {'type': 'string'}, 'value': {'type': 'string'}}, 'required': ['name', 'value']}},
            'depends_on': {'type': 'integer', 'minimum': 0, 'maximum': MAX_TASKS}, 'condition': {'type': 'string'}, 'for_each': {'type': 'string'}},
            'required': ['agent', 'intent', 'question', 'entities', 'depends_on', 'condition', 'for_each']}}}, 'required': ['tasks']}


async def model_plan(history: list[Message]) -> list[PlanTask]:
    pv = prompts.current('planner')
    result = await llm_gateway.call(pv, pv['system'] + '\n' + catalogue(), [(m.role, m.content) for m in history], plan_schema())
    rows = result.data.get('tasks') if isinstance(result.data, dict) else None
    if not isinstance(rows, list) or not rows:raise llm_gateway.LLMBadOutput('The planner returned no tasks')
    rows = rows[:MAX_TASKS]
    def upstream(i: int, row: dict) -> str | None:  # depends_on is the 1-based number of another task; 0 is none
        n = row.get('depends_on')
        return f't{n}' if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(rows) and n != i+1 else None
    def name(row: dict, key: str) -> str | None:
        return value.strip()[:40] if isinstance(value := row.get(key), str) and value.strip() else None
    return [PlanTask.model_validate({'id': f't{i+1}', 'agent': row['agent'], 'intent': row['intent'], 'question': row['question'],
        'entities': {item['name']: item['value'] for item in row['entities'] if isinstance(item, dict)},
        **({'depends_on': up, 'condition': name(row, 'condition'), 'for_each': name(row, 'for_each')} if (up := upstream(i, row)) else {})})
        for i, row in enumerate(rows)]


PORTFOLIO = dict(ACCOUNT_INTENTS)['portfolio']
MINE = re.compile(r'\b(?:my|mine|i have|i hold)\b', re.I)


def portfolio_task(item: PlanTask) -> PlanTask:
    """Her own time deposit, holdings or maturity date is read from her portfolio, also when the planner split it out of a
    longer message as a general (Guide) question or gave it another Accounts intent ("When does my time deposit mature?
    What did I spend last month?" answered the first part with "I cannot tell you the maturity date")."""
    text = typos.normalise(item.question)
    if not (PORTFOLIO.search(item.question) or PORTFOLIO.search(text)) or not MINE.search(text) or is_urgent(item):return item
    if item.agent == 'guide' or (item.agent == 'accounts' and item.intent not in ('portfolio', 'account_balance')):
        return item.model_copy(update={'agent': 'accounts', 'intent': 'portfolio'})
    return item


def overridden(tasks: list[PlanTask], risk: RiskAssessment) -> list[PlanTask]:
    """Rules win over the model for urgent card wording, login and explicit asks for a person; ordinary words never override it."""
    message = risk.redacted_message
    handovers = rm_handover.requested_questions(message)
    routed: list[PlanTask] = []
    for item in tasks:
        text = typos.normalise(item.question)
        if (FRAUD.search(item.question) or FRAUD.search(text)) and not (LOGIN.search(text) and RELATED.search(text)):
            item = urgent_task(item.question, message, item.id)
        elif LOGIN.search(item.question) or LOGIN.search(text):item = item.model_copy(update={'agent': 'guide', 'intent': 'login_help'})
        elif item.agent == 'relationship' and TELL_RM.search(text) and not rm_handover.MEETING.search(text):
            item = item.model_copy(update={'intent': OPEN_INTENT, 'entities': {**item.entities, 'handover': 'rm'}})
        elif RM_REQUEST.search(text) and not handovers and not rm_handover.MEETING.search(text):
            item = item.model_copy(update={'agent': 'relationship', 'intent': OPEN_INTENT, 'entities': {**item.entities, 'handover': 'rm'}})
        routed.append(portfolio_task(item))
    if handovers:  # one handover per part she asked a person about, in her words; an urgent card request stays
        if len(routed) == 1 and not is_urgent(routed[0]):routed = []
        else:routed = [t for t in routed if is_urgent(t) or (t.agent != 'relationship' and not rm_handover.needs_person(t.question))]
        used = {t.id for t in routed}
        fresh = (f't{n}' for n in range(1, 2 * MAX_TASKS + 2) if f't{n}' not in used)
        routed += [PlanTask(id=next(fresh), agent='relationship', intent=OPEN_INTENT, question=q, priority='P2', entities={'handover': 'rm'})
            for q in handovers]
    if {'fraud_suspected', 'lost_card'} & set(risk.risk_flags) and not any(is_urgent(t) for t in routed):
        # The planner's own charge question becomes the urgent request, so the charge is not answered twice.
        charge = next((i for i, t in enumerate(routed) if CHARGE.search(t.question) and not LOGIN.search(t.question)), None)
        if charge is not None:routed[charge] = urgent_task(routed[charge].question, message, routed[charge].id)
        else:routed.insert(0, urgent_task(message, message, 't0'))
    return routed[:MAX_TASKS]


def priority(item: PlanTask) -> Priority:
    """Bounded English rules: regulatory deadlines and RM handovers before the rest; ties keep message order. An urgent
    card task is P0 from its rule (urgent_task); P1 is no longer used (build plan 2b)."""
    if item.agent == 'compliance' and DEADLINE.search(item.question):return 'P2'
    return 'P2' if item.entities.get('handover') == 'rm' else 'P3'


def entity_value(name: str, value):
    if name == 'days':
        days = int(value) if isinstance(value, str) and value.isdigit() else value if isinstance(value, int) else None
        return days if days is not None and 1 <= days <= 90 else None
    if name == 'threshold':  # "5k", "5,000", "SGD 5000" -> "5000"; anything else is dropped, and Accounts asks for it
        found = derived.amount(value)
        return None if found is None else format(found.normalize(), 'f')
    return value


def for_guest(tasks: list[PlanTask]) -> tuple[list[PlanTask], dict[str, str]]:
    """A guest gets Guide only. Guide's own tasks stay; every task planned for a personal agent becomes ONE Guide
    `general` task (one "sign in to check" answer, P12), with their questions in plan order and the highest priority; an
    urgent card task stays its own (the fixed steps, no offer). Returns the tasks and the merged ids (old -> kept)."""
    personal = [t for t in tasks if t.agent != 'guide' and not is_urgent(t)]
    merged = {t.id: personal[0].id for t in personal}
    out: list[PlanTask] = []
    for item in tasks:
        if item in personal[1:]:continue
        update: dict = {'agent': 'guide', 'intent': item.intent if item.agent == 'guide' else 'general', 'entities': {'urgent': True} if is_urgent(item) else {}}
        if personal and item is personal[0]:
            update.update(question=' '.join(dict.fromkeys(t.question for t in personal)), priority=min(t.priority for t in personal))
        out.append(item.model_copy(update=update))
    return out, merged


def checked(tasks: list[PlanTask], req: ChatRequest) -> list[PlanTask]:
    """The plan check against CHAT_SPECS: guests get only Guide (for_guest); an unknown intent becomes `open`, or the agent's
    first intent. Accounts tasks learn whether Relationship also has a task (with_relationship)."""
    plan: list[PlanTask] = []
    ids: dict[str, str] = {}
    merged: dict[str, str] = {}
    if req.mode != 'personal':tasks, merged = for_guest(tasks[:MAX_TASKS])
    for item in tasks[:MAX_TASKS]:
        spec = registry.CHAT_SPECS[item.agent]
        intent = item.intent if item.intent in spec.intents or (item.intent == OPEN_INTENT and spec.loop_tools) else \
            OPEN_INTENT if spec.loop_tools else next(iter(spec.intents))
        entities = {k: v for k, v in ((k, entity_value(k, v)) for k, v in item.entities.items()) if v is not None and k != WITH_RELATIONSHIP}
        ids[item.id] = f't{len(plan)+1}'
        plan.append(item.model_copy(update={'id': ids[item.id], 'intent': intent, 'entities': entities}))
    ids.update({old: ids[kept] for old, kept in merged.items() if kept in ids})
    plan = [t.model_copy(update={'depends_on': ids.get(t.depends_on, '?'), 'inputs_from': [ids.get(u, '?') for u in t.inputs_from]})
        if t.depends_on else t for t in plan]  # '?': not in the plan
    # Set by code only: Relationship already answers part of this message (C28: the fee waiver), so Accounts leaves out
    # its "say 'waive my annual fee'" hint.
    with_relationship = any(t.agent == 'relationship' and not is_urgent(t) for t in plan)
    return [t.model_copy(update={'priority': t.priority if t.priority != 'P3' else priority(t),
        **({'entities': {**t.entities, WITH_RELATIONSHIP: True}} if with_relationship and t.agent == 'accounts' else {})}) for t in plan]


def unlinked(item: PlanTask, up: PlanTask | None, by_id: dict[str, PlanTask] | None = None) -> str | None:
    """Why a dependency is not declared by the specs (a code for the planner span), or None when it is. Each extra
    upstream in inputs_from (build plan 3b) passes the same checks and must export something the receiver imports."""
    exports = registry.CHAT_SPECS[up.agent].exports if up else {}
    if item.depends_on is None:return None
    if up is None or up.id == item.id:return 'no_upstream'
    if up.depends_on is not None:return 'chained_upstream'
    if (registry.CHAT_SPECS[up.agent].role == 'rm') != (registry.CHAT_SPECS[item.agent].role == 'rm'):return 'other_side'
    if item.condition is not None and exports.get(item.condition) != 'bool':return 'condition_not_bool_export'
    if item.for_each is not None and not exports.get(item.for_each, '').startswith('list['):return 'for_each_not_list_export'
    for extra in ((by_id or {}).get(u) for u in item.inputs_from):
        if why := unlinked(item.model_copy(update={'condition': None, 'for_each': None, 'inputs_from': []}), extra):return why
        if not registry.CHAT_SPECS[item.agent].imports & registry.CHAT_SPECS[extra.agent].exports.keys():return 'imports_nothing'
    return None


def export_intents(tasks: list[PlanTask], facts: dict | None = None) -> list[PlanTask]:
    """A link whose condition or for_each names an export only one intent computes (registry.EXPORT_INTENT) gives the
    upstream task that intent when its agent has it (P06: below_threshold needs savings_after_cards), so a declared link
    is not skipped at gather as unknown because the planner chose a neighbouring intent. Recorded in the planner span."""
    wanted = {t.depends_on: registry.EXPORT_INTENT[n] for t in tasks for n in (t.condition, t.for_each) if t.depends_on and n in registry.EXPORT_INTENT}
    out: list[PlanTask] = []
    for item in tasks:
        if (intent := wanted.get(item.id)) and item.intent != intent and intent in registry.CHAT_SPECS[item.agent].intents:
            if facts is not None:facts.setdefault('intent_set', []).append({'task': item.id, 'from': item.intent, 'to': intent})
            item = item.model_copy(update={'intent': intent})
        out.append(item)
    return out


def linked(tasks: list[PlanTask], req: ChatRequest, facts: dict | None = None) -> tuple[list[PlanTask], list[AnswerSection]]:
    """The dependency check (§14.6 rules 3 and 5): the upstream is another planned task that depends on nothing (so no
    cycles and at most 2 waves), both agents are on the same side (client or RM), and the condition is a bool export
    and for_each a list export of the upstream's spec. Guests get no dependent tasks. A task that fails is dropped with
    a fixed line; nothing runs on a link the specs do not declare."""
    dropped = [] if facts is None else facts.setdefault('unlinked', [])  # the planner span: links dropped and why
    if getattr(req, 'mode', 'personal') != 'personal':  # the RM request (rm/graph.py) has no mode: an RM is always signed in
        dropped += [{'task': t.id, 'agent': t.agent, 'reason': 'guest'} for t in tasks if t.depends_on]
        return [t.model_copy(update={'depends_on': None, 'condition': None, 'for_each': None, 'inputs_from': []}) for t in tasks], []
    tasks = export_intents(tasks, facts)
    by_id = {t.id: t for t in tasks}
    kept, lines = [], []
    for item in tasks:
        why = unlinked(item, by_id.get(item.depends_on) if item.depends_on else None, by_id)
        if why is None:kept.append(item)
        else:
            log_event('plan_unlinked', task=item.id, agent=item.agent)
            dropped.append({'task': item.id, 'agent': item.agent, 'reason': why})
            lines.append(fixed_section(item.id, UNLINKED, DOMAIN[item.agent], item.priority))
    return kept, lines


def plan_facts(tasks: list[PlanTask]) -> list[dict]:
    """The planner span's tasks: agent, intent and dependency only (never the question or entities); inputs_from when set."""
    return [{'id': t.id, 'agent': t.agent, 'intent': t.intent, 'depends_on': t.depends_on, 'condition': t.condition, 'for_each': t.for_each,
        **({'inputs_from': t.inputs_from} if t.inputs_from else {})} for t in tasks]


async def confirmation(req: ChatRequest, question: str, history: list[Message], tctx: TurnContext) -> Confirmation | Literal['expired', 'ambiguous'] | None:
    """A whole short English yes or no answers the one open offer group in this chat."""
    if req.mode != 'personal' or tctx.conversation_id is None:return None
    if req.offer is not None:return Confirmation(group=req.offer.offer_group, decision=req.offer.decision, include=req.offer.include)
    kind = offers.reply_kind(question)
    if kind == 'other':return None
    groups = await offers.open_groups(tctx.request.model_copy(update={'agent': None}), tctx.conversation_id)
    if len(groups) > 1:return 'ambiguous'
    if groups:return Confirmation(group=next(iter(groups)), decision='confirm' if kind == 'yes' else 'cancel')
    previous = history[:-1]
    if not previous or previous[-1].role != 'assistant':return None
    if previous[-1].content == EXPIRED or any(mark in previous[-1].content for mark in OFFER_MARKS):return 'expired'
    # Only an immediate repeated confirmation refers back to an already answered offer.
    if len(previous) >= 3 and previous[-2].role == 'user' and offers.reply_kind(previous[-2].content) != 'other':
        asked = previous[-3]
        if asked.role == 'assistant' and any(mark in asked.content for mark in OFFER_MARKS):return 'expired'
    return None


def fixed_section(task_id: str, text: str, domain: Domain = 'retail', priority: Priority = 'P3', sources: list[Source] | None = None,
        needs_human: bool = False) -> AnswerSection:
    return AnswerSection(task_id=task_id, domain=domain, priority=priority, answer_kind='conversation', answer=text,
        sources=sources or [], needs_human=needs_human, response_origin='safety', prompt_version=None)


async def planner(state: Turn, runtime: Runtime[TurnContext]) -> dict:
    req, risk, history = state['request'], state['risk'], state['history']
    question = risk.redacted_message
    with span('planner', 'agent') as attrs:
        reply = await confirmation(req, question, history, runtime.context)
        attrs['side'] = 'client'
        if isinstance(reply, Confirmation):
            attrs['route'], attrs['source'] = 'act', 'button' if req.offer is not None else 'rule'
            return {'confirm': reply, 'tasks': [], 'lines': []}
        if reply == 'ambiguous':
            attrs['route'] = 'ask_which'
            return {'confirm': None, 'tasks': [], 'lines': [fixed_section('t1', ASK_WHICH)]}
        if reply == 'expired':
            attrs['route'] = 'expired'
            return {'confirm': None, 'tasks': [], 'lines': [fixed_section('t1', EXPIRED)]}
        tasks = rule_plan(req, risk, history)
        attrs['route'] = 'rules' if tasks is not None else 'model'
        if tasks is None:
            single = simple_task(question, req.language)
            if settings.client_intent_routing == 'rules' and single is not None:tasks = [single]
            else:
                try:tasks = await model_plan(history)
                except (LLMError, ValidationError, KeyError, TypeError) as error:
                    # A clear single-topic question may fall back to the rules; never fabricate an answer.
                    log_event('intent_fallback', reason=type(error).__name__, agent=single.agent if single else None)
                    if single is None:return {'confirm': None, 'tasks': [], 'lines': [fixed_section('t1', REPHRASE)]}
                    tasks = [single]
            tasks = overridden(tasks, risk) if req.language == 'English' else tasks
        tasks, lines = linked(checked(tasks, req), req, attrs)
        attrs['tasks'], attrs['dependent'] = len(tasks), sum(1 for t in tasks if t.depends_on)
        attrs.update(side='client', source='rule' if attrs['route'] == 'rules' else 'model', plan=plan_facts(tasks))
    return {'confirm': None, 'tasks': tasks, 'lines': lines}


def agent_history(history: list[Message], item: PlanTask, several: bool) -> list[Message]:
    """The client's last 3 redacted messages, with the task's own question in place of the latest when one message held
    several issues. Assistant text is never included, except our own question for a reply to it (follow_up)."""
    if item.entities.get('follow_up'):return history
    earlier = [m for m in history[:-1] if m.role == 'user'][-2:]
    latest = Message(role='user', content=item.question) if several or not history else history[-1]
    return [*earlier, latest]


def language_of(state: Turn) -> str:
    return getattr(state['request'], 'language', 'English')  # the RM request has no language field (English only)


def after_planner(state: Turn) -> list[Send] | str:
    if state.get('confirm') is not None:return 'act'
    tasks = state.get('tasks') or []
    first = [item for item in tasks if item.depends_on is None]
    if not first:return 'compose'
    return [Send('agent', AgentInput(task=item, history=agent_history(state['history'], item, len(tasks) > 1), language=language_of(state),
        inputs={})) for item in first]


async def urgent_output(item: PlanTask, ctx: RequestContext) -> AgentOutput:
    """A lost card or a charge she doesn't recognise: fixed steps (no model, no tool for a guest) and, signed in, one urgent
    request to her RM, saved by compose and sent only when she confirms. An open lost-card request is quoted instead."""
    kind = 'lost' if LOST.search(typos.normalise(item.question)) and not FRAUD.search(typos.normalise(item.question)) else 'charge'
    pin = PINNED['lost-card' if kind == 'lost' else 'unrecognised-charge']
    text = rm_handover.URGENT_LOST_STEPS if kind == 'lost' else rm_handover.URGENT_STEPS
    proposals: list[OfferProposal] = []
    with span('agent', 'agent', agent=item.agent, intent=item.intent, path='urgent', status='needs_human') as facts:
        if ctx.principal.kind != 'customer':text += ' ' + rm_handover.URGENT_GUEST
        else:
            try:existing = await rm_handover.open_urgent(kind, ctx) if kind == 'lost' else None
            except rm_handover.HANDOVER_ERRORS:existing = None
            if existing:text += ' ' + rm_handover.URGENT_REUSED.format(reference=f' (reference {existing})')
            else:proposals = [OfferProposal(task_id=item.id, agent='relationship', type='handoff.create', summary=rm_handover.URGENT_OFFER[kind],
                target=rm_handover.urgent_target(kind, str(item.entities.get('message') or item.question), await rm_handover.urgent_cards(ctx)))]
        facts['offers'] = len(proposals)
    steps = Evidence(id=pin['id'], text=text, publisher=pin['publisher'], scope='Fixed urgent card steps that follow this approved section',
        source=Source.model_validate(pin))
    return AgentOutput(task_id=item.id, agent=item.agent, status='needs_human', origin='safety', proposals=proposals,
        evidence=[steps.model_dump(mode='json')], result=AgentResult(task_id=item.id, agent=item.agent, status='needs_human',
            answer_kind='conversation', text=text, citations=[{'source_id': pin['id']}]))


async def agent(item: AgentInput, runtime: Runtime[TurnContext]) -> dict:
    current = item['task']
    if is_urgent(current):
        output = await urgent_output(current, runtime.context.request)
        if (collected := evidence_sink.get()) is not None:collected.extend(Evidence.model_validate(d) for d in output.evidence)
        return {'outputs': [output]}
    try:output = await runner.run_agent(registry.CHAT_SPECS[current.agent], current, item.get('inputs') or {}, runtime.context.request,
        item['history'], item['language'])
    except runner.PlanRefused as error:
        log_event('plan_refused', agent=current.agent, intent=current.intent, reason=str(error)[:120])
        output = AgentOutput(task_id=current.id, agent=current.agent, status='error', origin='validation',
            result=AgentResult(task_id=current.id, agent=current.agent, status='error', text=WITHHELD))
    if (collected := evidence_sink.get()) is not None:collected.extend(Evidence.model_validate(d) for d in output.evidence)
    return {'outputs': [output]}


def skipped(item: PlanTask, text: str, reason: str, facts: dict) -> AgentOutput:
    """A dependent task that does not run: a fixed line in its place, never the planner's or a model's text."""
    log_event('dependency_skipped', task=item.id, agent=item.agent, reason=reason)
    facts['skipped'][item.agent] = reason
    return AgentOutput(task_id=item.id, agent=item.agent, status='ok', origin='safety',
        result=AgentResult(task_id=item.id, agent=item.agent, status='ok', answer_kind='conversation', text=text))


def items_of(export: Export, name: str, agent: TargetAgent) -> list:
    """A list export in its declared order (the spec's sort field; dates by value)."""
    values = export.value if isinstance(export.value, list) else []
    key = registry.CHAT_SPECS[agent].sort_by.get(name)
    return sorted(values, key=lambda v: str(v.get(key, '')) if key and isinstance(v, dict) else str(v))


def passed(up: AgentOutput, to: PlanTask, inputs: dict[str, Export], facts: dict) -> None:
    """Audit names and counts, never values (§14.6 rule 6; audited as agent.export_passed)."""
    for name, export in inputs.items():
        count = len(export.value) if isinstance(export.value, list) else 1
        if (fact := f'{up.agent}>{to.agent}:{name}') not in facts['exports']:facts['exports'].append(fact)
        facts.setdefault('passed', []).append({'from': up.agent, 'to': to.agent, 'name': name, 'count': count})
        audit.add('agent', 'agent.export_passed', details={'from': up.agent, 'to': to.agent, 'name': name, 'count': count})


def gather(state: Turn) -> dict:
    """After wave 1 (next_wave, §14.6): the dependent tasks with their imports, conditions and fan-out, and one handoff per
    out-of-scope task to an agent in its spec's hands_to (question only, no planner call). After wave 2: nothing.
    It changes the state only when there is a wave 2 (or a skip), so the evaluation counts it as acting only then."""
    if state.get('next') is not None:return {'next': []}
    with span('gather', 'decision') as facts:
        facts.update(exports=[], skipped={}, rerouted={})
        update = next_wave(state, facts)
        facts['sends'] = len(update['next'])
    return update


def next_wave(state: Turn, facts: dict) -> dict:
    outputs = {o.task_id: o for o in state.get('outputs') or []}
    language, history = language_of(state), state['history']
    tasks, sends, extra, lines = [], [], [], list(state.get('lines') or [])
    for item in state.get('tasks') or []:
        up = outputs.get(item.depends_on) if item.depends_on else None
        if item.depends_on is None:
            done = outputs.get(item.id)
            hand = done.suggested if done is not None and done.status == 'out_of_scope' and not item.rerouted else None
            if hand is not None and hand in registry.CHAT_SPECS[item.agent].hands_to:
                spec = registry.CHAT_SPECS[hand]
                item = item.model_copy(update={'agent': hand, 'intent': OPEN_INTENT if spec.loop_tools else next(iter(spec.intents)),
                    'entities': {}, 'rerouted': True})
                log_event('task_rerouted', task=item.id, agent=hand)
                facts['rerouted'] = {'from': done.agent, 'to': hand}
                sends.append(AgentInput(task=item, history=[Message(role='user', content=item.question)], language=language, inputs={}))
            tasks.append(item)
            continue
        ups = [up, *(outputs.get(u) for u in item.inputs_from)]  # inputs_from: more upstreams, inputs only (build plan 3b)
        if any(u is None or u.status != 'ok' for u in ups) or any(name and name not in up.exports for name in (item.condition, item.for_each)):
            extra.append(skipped(item, UPSTREAM_UNKNOWN, 'upstream_unavailable', facts));tasks.append(item);continue
        if item.condition and up.exports[item.condition].value is not True:
            extra.append(skipped(item, CONDITION_FALSE.get(item.condition, CONDITION_FALSE_ANY), 'condition_false', facts));tasks.append(item);continue
        given = [(u, {k: u.exports[k] for k in registry.CHAT_SPECS[item.agent].imports & u.exports.keys()}) for u in ups]
        inputs = {k: v for _, part in reversed(given) for k, v in part.items()}  # a name two upstreams export: depends_on's
        own = agent_history(history, item, True)
        if not item.for_each:
            for u, part in given:passed(u, item, part, facts)
            sends.append(AgentInput(task=item, history=own, language=language, inputs=inputs));tasks.append(item);continue
        values = items_of(up.exports[item.for_each], item.for_each, up.agent)
        if not values:
            extra.append(skipped(item, CONDITION_FALSE_ANY, 'no_items', facts));tasks.append(item);continue
        for i, value in enumerate(values[:MAX_ITEMS]):
            one = item.model_copy(update={'id': item.id if i == 0 else f'{item.id}-{i+1}', 'entities': {**item.entities, 'item': value}})
            mine = {**inputs, **({item.for_each: Export(value=value, source_ids=up.exports[item.for_each].source_ids)} if item.for_each in inputs else {})}
            passed(up, one, {k: v for k, v in mine.items() if k in given[0][1]}, facts)
            for u, part in given[1:]:passed(u, one, part, facts)
            sends.append(AgentInput(task=one, history=own, language=language, inputs=mine));tasks.append(one)
        if len(values) > MAX_ITEMS:
            facts.setdefault('fan_out', []).append({'task': item.id, 'shown': MAX_ITEMS, 'total': len(values)})
            lines.append(fixed_section(f'{item.id}-more', MORE_ITEMS.format(shown=MAX_ITEMS, total=len(values)), DOMAIN[item.agent], item.priority))
    return {'next': sends, 'tasks': tasks, 'outputs': extra, 'lines': lines}


def after_gather(state: Turn) -> list[Send] | str:
    return [Send('agent', item) for item in state.get('next') or []] or 'compose'


# ---- Confirmation turns ----

def receipt_source(command_id: UUID) -> Source:
    # Keep UUID groups separate from card-number patterns in saved replies.
    identifier: str = str(command_id).replace('-', '_')
    return Source(id=f'action-receipt-{identifier}', title='Recorded action receipt', url=None, version='v1', publisher='UBS Connect action ledger')


async def outcome_text(outcome: ActionOutcome, ctx: RequestContext, earlier: str | None = None) -> str:
    """Fixed wording per recorded outcome; "sent" is said only with a succeeded receipt. `earlier` is the reference of the
    step this one was chained to (it received that step's receipt)."""
    command, receipt = outcome.command, outcome.receipt
    urgent = rm_handover.urgent(getattr(command.target, 'summary', None) or '')
    if command.state == 'rejected':return rm_handover.URGENT_DECLINED if urgent else CANCELLED
    if command.state == 'expired':return EXPIRED
    if receipt is None or receipt.outcome != 'succeeded':
        return CHAIN_STOPPED if getattr(receipt, 'result', None) is not None and getattr(receipt.result, 'error_code', None) == 'earlier_offer_failed' else STEP_FAILED
    link = LINKED.format(reference=earlier) if earlier else ''
    if isinstance(receipt.result, HandoffResult):
        reference = await rm_handover.ticket_reference(receipt.result.handoff_id, ctx)
        if urgent and not receipt.result.created:return await rm_handover.urgent_elsewhere(command.target.summary, reference, ctx)
        if urgent:return rm_handover.URGENT_SENT.format(reference=f' (reference {reference})' if reference else '')
        return rm_handover.handed_over(reference) + link
    if isinstance(receipt.result, TicketResult):
        label = getattr(command.target, 'category_label', 'support').lower()
        return TICKET_RAISED.format(label=label, reference=receipt.result.reference) + link
    if isinstance(receipt.result, MeetingRequestResult):return MEETING_SENT.format(respond_by=receipt.result.respond_by_text) + link
    if isinstance(receipt.result, InsightShareResult):
        return SHARED.format(line=talking_points.shared_line(receipt.result)) + link
    if isinstance(receipt.result, ConsentGrantV2Result):
        on = receipt.result.expires_on
        until = GRANT_UNTIL[receipt.result.until].format(date=f'{on.day} {on:%b %Y}' if on else '')
        return GRANTED.format(rm=re.sub(r'\s*\(DEMO\)\s*$', '', receipt.result.recipient_name), label=receipt.result.category_label.lower(), until=until)
    return DONE + link


async def reference_of(outcome: ActionOutcome, ctx: RequestContext) -> str | None:
    result = getattr(outcome.receipt, 'result', None)
    if isinstance(result, TicketResult):return result.reference
    if isinstance(result, HandoffResult):return await rm_handover.ticket_reference(result.handoff_id, ctx)
    return None


async def act(state: Turn, runtime: Runtime[TurnContext]) -> dict:
    """Run or cancel the open group in order; SQL checks ownership, TTL and order, and a double confirm records once."""
    reply = state['confirm']
    ctx = runtime.context.request.model_copy(update={'agent': None})
    with span('act', 'tool', decision=reply.decision) as attrs:
        chained = await offers.chained(reply.group, ctx, runtime.context.conversation_id) if reply.decision == 'confirm' else set()
        outcomes = await offers.confirm(reply.group, ctx, reply.include) if reply.decision == 'confirm' else await offers.cancel(reply.group, ctx)
        attrs['offers'], attrs['chained'], attrs['switches_on'] = len(outcomes), len(chained), len(reply.include)
        if reply.decision == 'confirm':  # a switch she left off is her no (rejected), not a failure
            attrs['failed'] = sum(o.command.state != 'rejected' and (o.receipt is None or o.receipt.outcome != 'succeeded') for o in outcomes)
    lines, earlier = [], None
    # a switch she left off (or a cancelled group's switches) is her no; it needs no line of its own
    outcomes = [o for o in outcomes if not (o.command.state == 'rejected' and offers.optional(o.command.type, o.command.target.model_dump(mode='json')))]
    for i, outcome in enumerate(outcomes):
        urgent = rm_handover.urgent(getattr(outcome.command.target, 'summary', None) or '')
        succeeded = outcome.receipt is not None and outcome.receipt.outcome == 'succeeded'
        link = earlier if outcome.command.command_id in chained else None
        earlier = await reference_of(outcome, ctx) if succeeded else None
        lines.append(fixed_section(f'offer{i+1}', await outcome_text(outcome, ctx, link), domain='fraud' if urgent else 'relationship',
            priority='P0' if urgent else 'P3', needs_human=urgent or isinstance(getattr(outcome.receipt, 'result', None), HandoffResult),
            sources=[receipt_source(outcome.command.command_id)] if succeeded else []))
    if not lines:lines.append(fixed_section('t1', EXPIRED))
    elif reply.decision == 'confirm' and (pending_rm := rm_handover.unanswered_preview(state['history'])):
        lines.append(fixed_section('pending_rm', pending_rm, domain='relationship', priority='P2', needs_human=True))
    return {'lines': lines}


# ---- Compose ----

def ordered(state: Turn) -> list[tuple[PlanTask, AgentOutput]]:
    found = {item.task_id: item for item in state.get('outputs') or []}
    tasks = sorted(state.get('tasks') or [], key=lambda t: t.priority)  # stable: ties keep message order
    return [(t, found[t.id]) for t in tasks if t.id in found]


def section_for(item: PlanTask, output: AgentOutput, decision: str | None = None) -> AnswerSection:
    if output.status == 'out_of_scope' and output.result is None:  # not handed on (no allowed agent, or already handed once)
        return fixed_section(item.id, OUT_OF_SCOPE, DOMAIN[item.agent], item.priority, needs_human=True)
    result = output.result or AgentResult(task_id=item.id, agent=item.agent, status='error', text=WITHHELD)
    sources = {e.id: e.source for e in (Evidence.model_validate(d) for d in output.evidence)}
    cited = list({s.id: s for s in (sources[c.source_id] for c in result.citations if c.source_id in sources)}.values())
    domain: Domain = 'smalltalk' if item.agent == 'guide' and result.answer_kind == 'conversation' and not is_urgent(item) else domain_of(item)
    return AnswerSection(task_id=item.id, domain=domain, priority=item.priority, answer_kind=result.answer_kind, answer=result.text,
        sources=cited, needs_human=output.status in ('error', 'needs_human') or bool(decision), response_origin=output.origin,
        prompt_version=output.prompt_id, decision=decision)


def offer_order(rows: list[tuple[PlanTask, AgentOutput]]) -> list[OfferProposal]:
    """Offers in section order, each dependent task's straight after its upstream's. A single dependent task's first offer
    is chained: it runs after the upstream's last offer and receives its receipt."""
    usable = [(t, o) for t, o in rows if o.status != 'error']
    found: list[OfferProposal] = []
    for item, output in usable:
        if item.depends_on is not None:continue
        found += output.proposals
        for other, after in usable:
            if other.depends_on != item.id:continue
            found += [p.model_copy(update={'chained': True}) if i == 0 and output.proposals and not other.for_each else p
                for i, p in enumerate(after.proposals)]
    return found


def validation_offer(rows: list[tuple[PlanTask, AgentOutput]], ctx: RequestContext) -> list[OfferProposal]:
    """VALIDATION_HANDOFF=1 only (CR9): one withheld section may be offered to the RM; the summary carries no rejected text."""
    failed = next((t for t, o in rows if o.status == 'error' and o.origin == 'validation'), None)
    # One general_support handoff per case: an urgent card request in the same reply wins.
    if failed is None or any(is_urgent(t) for t, _ in rows) or not rm_handover.validation_handoff() or ctx.principal.kind != 'customer' or ctx.case_id is None:return []
    return [OfferProposal(task_id='validation', agent='relationship', type='handoff.create', target={'reason': 'general_support',
        'priority': failed.priority, 'summary': 'An assistant section was withheld because it could not be verified. Review the saved '
        'customer case; no rejected answer is included.'}, summary='Pass the part I couldn’t check to your relationship manager')]


async def superseded(proposals: list[OfferProposal], ctx: RequestContext, conversation_id: UUID | None) -> None:
    """A redrafted meeting request replaces the draft still open in this chat (her new times or format are her answer
    to it), so a typed yes answers the new one. Only groups holding nothing but a meeting request are cancelled."""
    if conversation_id is None or not any(p.type == 'meeting.request' for p in proposals):return
    current = ctx.model_copy(update={'agent': None})
    for group, items in (await offers.open_groups(current, conversation_id)).items():
        if group != offers.group_for(ctx.case_id) and all(o.type == 'meeting.request' or o.type == 'consent.grant' and offers.optional(o.type, o.target) for o in items):
            await offers.cancel(group, current)


async def share_offer(state: Turn, runtime: Runtime[TurnContext]) -> tuple[OfferProposal, str] | None:
    """Talking points lane 2 (0108): a rule, never the model. Only for a signed-in saved chat whose reply has no other
    offer and is not a confirmation; talking_points.share_offer checks her words and what rules the prompt out."""
    ctx = runtime.context.request
    if state.get('confirm') or runtime.context.conversation_id is None or runtime.context.turn_id is None or not state['request'].messages:return None
    with span('share_offer', 'decision') as attrs:
        risk = state.get('risk')
        found = await talking_points.share_offer(risk.redacted_message if risk else '', risk, ctx)
        attrs['offered'] = found is not None
    return found


async def compose(state: Turn, runtime: Runtime[TurnContext]) -> dict:
    """Sections in plan priority order, then the offers saved as one group; fixed lines (act outcomes, rephrase) last."""
    ctx = runtime.context.request
    rows = ordered(state)
    proposals = offer_order(rows) + validation_offer(rows, ctx)
    if proposals:await superseded(proposals, ctx, runtime.context.conversation_id)
    share = None if proposals else await share_offer(state, runtime)
    if share:proposals = [share[0]]
    try:saved = await offers.save(proposals, ctx, runtime.context.turn_id) if proposals else []
    except Exception as error:
        if share is None:raise
        log_event('share_offer_failed', error_type=type(error).__name__)  # the answer stands without the offer
        proposals, saved, share = [], [], None
    by_task: dict[str, list[tuple[OfferProposal, object]]] = {}
    for proposal, offer in zip(proposals, saved, strict=True):by_task.setdefault(proposal.task_id, []).append((proposal, offer))
    required = [p for p in proposals if not offers.optional(p.type, p.target)]
    line = CONFIRM_LINE + (' Your answer covers every offer in this reply.' if len(required) > 1 else '')
    sections = []
    for item, output in rows:
        mine = by_task.pop(item.id, [])
        decision = output.decision
        switches = [p.summary for p, _ in mine if offers.optional(p.type, p.target)]
        if mine:decision = (decision or 'Would you like me to: ' + '; '.join(p.summary for p, _ in mine if not offers.optional(p.type, p.target)) + '?') + ' ' + line
        if switches:decision += (' You can also switch on: ' + '; '.join(switches) + '. They stay off unless you switch them on below; '
            'a typed yes sends the request only.')
        sections.append(section_for(item, output, decision))
    for proposal, _ in (pair for pairs in by_task.values() for pair in pairs):
        if share:sections.append(fixed_section(proposal.task_id, share[1], domain='relationship'))
        else:sections.append(fixed_section(proposal.task_id, f'Would you like me to: {proposal.summary}? {line}', domain='relationship', needs_human=True))
    sections += state.get('lines') or []
    return {'answer': answer_for(sections, state, offer_event(saved, ctx, proposals))}


def offer_event(saved: list, ctx: RequestContext, proposals: list[OfferProposal] | None = None) -> OfferGroupEvent | None:
    if not saved:return None
    switches = [offers.optional(p.type, p.target) for p in proposals] if proposals else [False] * len(saved)
    return OfferGroupEvent(offer_group=offers.group_for(ctx.case_id), expires_at=min(o.expires_at for o in saved),
        offers=[ActionRequiredEvent(command_id=o.offer_id, type=o.type, summary=o.summary, expires_at=o.expires_at, optional=switch)
            for o, switch in zip(saved, switches, strict=True)])


def answer_for(sections: list[AnswerSection], state: Turn, offer: OfferGroupEvent | None = None) -> AnswerEvent:
    unique = {s.id: s for section in sections for s in section.sources}
    return AnswerEvent(answer='\n\n'.join(s.answer + ('\n\n' + s.decision if s.decision else '') for s in sections), sources=list(unique.values()),
        privacy_warning=False, history_trimmed=len(state['history']) < state['history_count'],
        disclaimer=NOTICE if any(s.answer_kind == 'banking' or s.decision for s in sections) else '',
        needs_human=any(s.needs_human for s in sections),
        source_check='rejected' if any(s.response_origin == 'validation' for s in sections) else 'basic_checks_passed', prompt_version=None,
        response_origin='ai' if any(s.response_origin == 'ai' for s in sections) else 'validation' if any(s.response_origin == 'validation' for s in sections) else 'safety',
        sections=sections, offer=offer)


def build_graph():
    graph = StateGraph(Turn, context_schema=TurnContext)
    graph.add_node('planner', planner)
    graph.add_node('agent', agent)
    graph.add_node('gather', gather)
    graph.add_node('act', act)
    graph.add_node('compose', compose)
    graph.add_edge(START, 'planner')
    graph.add_conditional_edges('planner', after_planner, ['agent', 'act', 'compose'])
    graph.add_edge('agent', 'gather')
    graph.add_conditional_edges('gather', after_gather, ['agent', 'compose'])
    graph.add_edge('act', 'compose')
    graph.add_edge('compose', END)
    return graph.compile()


GRAPH = build_graph()


def start(req: ChatRequest, risk: RiskAssessment) -> Turn:
    if risk.fixed_response is not None:raise ValueError('Fixed safety responses must use the no-model pipeline')
    history = clean_history(req.messages)
    return {'request': req.model_copy(update={'messages': history}), 'risk': risk, 'history': history, 'history_count': len(req.messages), 'outputs': []}


async def stream(req: ChatRequest, risk: RiskAssessment, tctx: TurnContext) -> AsyncIterator[tuple[str, BaseModel]]:
    """('plan', PlanEvent) once tasks are planned, ('section', AnswerSection) as each agent finishes (no offers yet),
    then ('answer', AnswerEvent)."""
    tasks: dict[str, PlanTask] = {}
    with span('route', 'agent', route='llm', pipeline='v5'):
        async for chunk in GRAPH.astream(start(req, risk), context=tctx, stream_mode='updates'):
            for node, update in chunk.items():
                if node == 'planner':
                    tasks = {t.id: t for t in (update or {}).get('tasks') or []}
                    if tasks:yield 'plan', PlanEvent(tasks=[PlannedSection(task_id=t.id, domain=domain_of(t), priority=t.priority)
                        for t in sorted(tasks.values(), key=lambda t: t.priority)])
                elif node in ('agent', 'gather'):
                    tasks.update({t.id: t for t in (update or {}).get('tasks') or []})
                    for output in (update or {}).get('outputs') or []:
                        if output.task_id in tasks and output.status != 'out_of_scope':
                            yield 'section', section_for(tasks[output.task_id], output, output.decision)
                elif node == 'compose':yield 'answer', update['answer']


async def answer(req: ChatRequest, risk: RiskAssessment, tctx: TurnContext) -> AnswerEvent:
    async for kind, item in stream(req, risk, tctx):
        if kind == 'answer':return item
    raise ValueError('The turn ended without an answer')


async def lost_card_offer(question: str, ctx: RequestContext, turn_id: UUID) -> tuple[str, OfferGroupEvent | None]:
    """CR12, build plan 2b: the fixed lost-card reply offers one urgent request to her RM; an open one is quoted instead."""
    summary = rm_handover.URGENT_OFFER['lost']
    try:
        if (existing := await rm_handover.open_urgent('lost', ctx)) is not None:
            return rm_handover.URGENT_REUSED.format(reference=f' (reference {existing})'), None
        saved = await offers.save([OfferProposal(task_id='lost_card', agent='relationship', type='handoff.create', summary=summary,
            target=rm_handover.urgent_target('lost', question, await rm_handover.urgent_cards(ctx)))], ctx, turn_id)
    except rm_handover.HANDOVER_ERRORS as error:
        log_event('lost_card_offer_failed', error_type=type(error).__name__)
        return rm_handover.URGENT_FAILED, None
    return f'Would you like me to: {summary}? {CONFIRM_LINE}', offer_event(saved, ctx)

