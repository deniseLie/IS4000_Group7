"""Explain only the caller's client-visible review snapshot; checklist writes stay in the Action Service."""
import json
from pydantic import JsonValue
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.customer_data import ComplianceCase,CustomerEnvelope
from app.schemas.events import Source
from app.services import customer_data,llm_gateway,prompts,compliance_selection,tools
from app.services.agents.answer import draft_schema,validate_draft


async def load(ctx: RequestContext) -> CustomerEnvelope[ComplianceCase | None]:
    result=await tools.read('my_compliance_case',{},ctx)
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
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],draft_schema(evidence))
    return validate_draft(task,json.dumps(response.data,ensure_ascii=False),evidence),evidence,pv['id']
