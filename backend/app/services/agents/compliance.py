"""Explain only the caller's client-visible review snapshot; checklist writes stay in the Action Service."""
import json
import re
from pydantic import JsonValue
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.customer_data import ComplianceCase,CustomerEnvelope
from app.schemas.events import Source
from app.services import customer_data,llm_gateway,prompts,compliance_selection
from app.services.agents.answer import checked_model_answer


# An open-ended request for assistance needs a clarification, not a banking
# fact or an invented status. Do not intercept specific requests for a notice,
# document checklist, upload, deadline, correction or bank review decision.
GENERAL_HELP = re.compile(
    r"\b(?:help|assistance|assist|support|guidance)\b|\bwhat can you do\b",re.I)
COMPLIANCE_SUBJECT = re.compile(
    r"\b(?:compliance|kyc|review|documents?|checklist)\b",re.I)
SPECIFIC_NEED = re.compile(
    r"\b(?:which|what\s+(?:documents?|items?|files?)|how|why|where|when|"
    r"upload(?:ing|ed|s)?|submit(?:ting|ted)?|received|accepted|approved|reject(?:ed|ion)?|"
    r"status|progress|outstanding|missing|prepared|due|deadline|"
    r"fix(?:ing|es|ed)?|correct(?:ing|ed|ion)?|expiry|expired|checklist|proof\s+of)\b",re.I)


def general_help(question: str) -> bool:
    """True only for unspecific help-seeking, not questions about record facts."""
    return bool(GENERAL_HELP.search(question) and COMPLIANCE_SUBJECT.search(question)
                and not SPECIFIC_NEED.search(question))


CLARIFICATION = (
    "I can help with your compliance review. Would you like to check the "
    "documents required, upload a document, check your review status, "
    "or understand a correction request?"
)


async def load(ctx: RequestContext) -> CustomerEnvelope[ComplianceCase | None]:
    result=await customer_data.get_compliance_case(ctx)
    return CustomerEnvelope[ComplianceCase | None].model_validate({'customer_id':ctx.principal.customer_id,
        'call_id':result.call_id,'as_of':result.as_of,'data':result.data})


def evidence_for(snapshot: CustomerEnvelope[ComplianceCase | None]) -> list[Evidence]:
    case=snapshot.data
    rows: list[tuple[str,str,JsonValue]]=[('case','Authenticated synthetic review case',case.model_dump(mode='json',exclude={'case_id','requested_items','status_history','info_requests'}) if case else None),
        ('items','Authenticated synthetic review checklist',[item.model_dump(mode='json',exclude={'item_id'}) for item in case.requested_items] if case else []),
        ('history','Authenticated synthetic review status history',[item.model_dump(mode='json') for item in case.status_history] if case else []),
        ('requests','Authenticated synthetic review information requests',[item.model_dump(mode='json',exclude={'request_id'}) for item in case.info_requests] if case else [])]
    return [Evidence(id='compliance-'+key,text=json.dumps({'records':records},ensure_ascii=False),publisher='DemoBank synthetic records',
        scope='One audited own-customer snapshot of the latest client-visible review. Each child collection is bounded to 200 rows. No internal assessment or document contents.',
        source=Source(id='compliance-'+key,title=title,url=None,version=snapshot.as_of.isoformat(),publisher='DemoBank synthetic records'))
        for key,title,records in rows]


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[AgentResult,list[Evidence],str]:
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Compliance task question must be text')
    # No personal-data read or model guess is needed to clarify an unspecified
    # support request. This never changes a checklist or submits a document.
    if compliance_selection.selection.get() is None and language=='English' and general_help(question):
        return (AgentResult(task_id=task.task_id,agent='compliance',
                    status='needs_clarification',answer_kind='conversation',
                    text=CLARIFICATION,question=CLARIFICATION),
                [],prompts.load('compliance','v3')['id'])
    snapshot=await load(ctx)
    chosen=compliance_selection.selection.get()
    if chosen is not None:
        if snapshot.data is None or snapshot.data.case_id!=chosen.case_id:raise customer_data.CustomerDataIntegrityError('Selected case changed')
        requests=[r for r in snapshot.data.info_requests if r.request_id==chosen.request_id]
        if not requests:raise customer_data.CustomerDataIntegrityError('Selected request changed')
        snapshot=snapshot.model_copy(update={'data':snapshot.data.model_copy(update={'requested_items':[],'status_history':[],'info_requests':requests})})
    evidence=[*public_evidence,*evidence_for(snapshot)]
    pv=prompts.load('compliance','v3')
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    # Like retail, compliance gets one evidence-aware revision for a recoverable
    # format/citation failure. Both drafts must satisfy the same source checks;
    # no unsupported bank facts, statuses or approvals are allowed through.
    result=await checked_model_answer(task,system,history,evidence,pv)
    return result,evidence,pv['id']
