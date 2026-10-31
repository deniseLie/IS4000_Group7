"""Safe, read-only demo app instructions and clarifying questions.

No product terms, private figures or actions are invented. Actual request submission
remains behind the explicit review and confirmation service.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from app.schemas.contracts import AgentResult, Citation
from app.services.client_intents import STATEMENT, COMPLIANCE, account_balance

UNCLEAR_HELP=re.compile(r"\b(?:need|want)\s+help\b.{0,120}\b(?:don['’]?t\s+know|not\s+sure|hard\s+to|can['’]?t)\b.{0,100}\b(?:explain|describe|ask|mean|start)\b",re.I)
NEW_REQUEST = re.compile(r"\b(?:raise|open|start|create|make)\s+(?:a\s+)?(?:new\s+)?(?:service\s+)?request\b",re.I)
PERSONAL_DETAILS = re.compile(r"\b(?:update|change|correct|replace)\b.{0,65}\b(?:my\s+)?(?:contact\s+number|phone\s+number|mobile\s+number|mobile|address|email\s+address)\b",re.I)
GENERIC_LOAN = re.compile(r"\b(?:borrow\s+money|need\s+(?:a\s+)?loan|which\s+loan\s+(?:i\s+)?need)\b",re.I)
LOAN_TYPE = re.compile(r"\b(?:car|vehicle|used\s+car|personal|home|housing|property|mortgage|refinanc|education|renovation)\b",re.I)
STATEMENT_COPY = re.compile(r"\b(?:certified|stamped|paper|physical|older|last\s+year|old\s+statement|copy\s+of|statement\s+copy)\b",re.I)


def older_period(question,now):
    year=re.search(r"\b(20\d{2})\b",question)
    if not year:return False
    year=int(year.group(1))
    today=(now or datetime.now(ZoneInfo('Asia/Singapore'))).astimezone(ZoneInfo('Asia/Singapore'))
    month=re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",question,re.I)
    if month:
        m=['january','february','march','april','may','june','july','august','september','october','november','december'].index(month.group(1).lower())+1
        full={(today.year if today.month-i>0 else today.year-1, (today.month-i-1)%12+1) for i in range(1,4)}
        return (year,m) not in full
    return year<today.year


def respond(task_id,question,public_evidence,now=None):
    if UNCLEAR_HELP.search(question):
        return AgentResult(task_id=task_id,agent='retail',status='needs_clarification',answer_kind='conversation',
            text='I can help you work out where to start. Is your concern about an account, card, payment, document, meeting, or something else? You can describe the situation in your own words.',
            question='What area would you like help with?')
    if NEW_REQUEST.search(question):
        return AgentResult(task_id=task_id,agent='retail',status='needs_clarification',answer_kind='conversation',
            text='What kind of service request would you like to raise? You can describe it in your own words. I can help prepare a draft for you to review before anything is submitted.',
            question='What kind of service request would you like to raise?')
    if PERSONAL_DETAILS.search(question):
        return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='conversation',
            text='I can help you prepare a personal-details update request for the client records team. Review the details in the secure request form before you submit it. Please do not enter sensitive identifiers in chat; the assistant does not change your records directly.')
    if GENERIC_LOAN.search(question) and not LOAN_TYPE.search(question):
        return AgentResult(task_id=task_id,agent='retail',status='needs_clarification',answer_kind='conversation',
            text='What would you use the loan for—for example a home, a vehicle or a personal expense? I can explain the relevant options, but the bank makes eligibility and approval decisions.',
            question='What would you use the loan for?')
    if STATEMENT.search(question) and not COMPLIANCE.search(question) and not account_balance(question):
        # The demo UI is a known application capability, not permission to
        # retrieve/download another person's statement or create a request.
        if STATEMENT_COPY.search(question) or older_period(question,now):
            text='For an older, certified or paper statement, you can prepare a Statement or document copy request. Review the account or card, period and requested format before you confirm submission. The operations team prepares it; the chatbot cannot issue a certified statement.'
        else:
            text='To view a recent statement, open Overview → Statements, select your account or card and the month, then select View statement. You can print or save it as PDF. If the month is not offered, you can request a statement copy.'
        source=next((item for item in public_evidence if 'statement' in item.source.title.lower() or 'statement' in item.id.lower()),None)
        return AgentResult(task_id=task_id,agent='retail',status='ok',answer_kind='banking' if source else 'conversation',
            text=text,citations=[Citation(source_id=source.id)] if source else [])
    return None
