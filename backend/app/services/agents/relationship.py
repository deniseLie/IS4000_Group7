"""Read-only personal relationship answers with separate, owned sources and no action claims."""
import json
import re
from uuid import UUID
from pydantic import JsonValue
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.customer_data import RelationshipAssignment
from app.schemas.events import Source
from app.schemas.relationship_records import RelationshipRecords
from app.services import llm_gateway,prompts
from app.core.observability import log_event
from app.services.agents import relationship_records
from app.services.agents.answer import WITHHELD,draft_schema,validate_draft
from app.services.privacy import redact

ACTION_CLAIM=re.compile(r"\b(?:I|we)(?:['’]ve)?\s+(?:(?:have|will|just|successfully|already)\s+){0,3}(?:book(?:ed)?|reserv(?:e|ed)|schedul(?:e|ed)|cancel(?:led|ed)?|sent|send|submit(?:ted)?|shar(?:e|ed)|sav(?:e|ed)|grant(?:ed)?|revok(?:e|ed)|contact(?:ed)?)\b",re.I)


def advisor_name(advisor_id: UUID | None,assignment: RelationshipAssignment | None) -> str | None:
    if advisor_id is None or assignment is None:return None
    for advisor in (assignment.current,assignment.previous):
        if advisor is not None and advisor.rm_id==advisor_id:return advisor.name
    return None


def evidence_for(records: RelationshipRecords) -> list[Evidence]:
    assignment=records.assignment.data
    requests: list[JsonValue]=[{**row.model_dump(mode='json',exclude={'customer_id','request_id','advisor_id'}),
        'who':redact(row.who)[0],'what':redact(row.what)[0],'advisor_name':advisor_name(row.advisor_id,assignment)} for row in records.requests.data]
    appointments: list[JsonValue]=[{**row.model_dump(mode='json',exclude={'appointment_id','advisor_id','case_id'}),
        'advisor_name':advisor_name(row.advisor_id,assignment)} for row in records.appointments.data]
    rows: list[tuple[str,str,JsonValue,str]]=[
        ('assignment','Your recorded relationship manager',assignment.model_dump(mode='json',exclude={
            'current':{'rm_id':True,'transition_reason_code':True,'contact':{'email'}},
            'previous':{'rm_id':True,'transition_reason_code':True,'contact':{'email'}}}) if assignment else None,records.assignment.as_of.isoformat()),
        ('requests','Your recorded relationship requests',requests,records.requests.as_of.isoformat()),
        ('appointments','Your recorded appointments',appointments,records.appointments.as_of.isoformat()),
        ('slots','Available appointment suggestions',{'search':records.search.model_dump(mode='json'),'slots':[
            {**row.model_dump(mode='json',exclude={'customer_id','advisor_id'}),'advisor_name':advisor_name(row.advisor_id,assignment)}
            for row in records.slots.data]},records.slots.as_of.isoformat())]
    return [Evidence(id='relationship-'+key,text=json.dumps({'records':data},ensure_ascii=False),publisher='DemoBank demo records',
        scope='Own-customer audited snapshot. Separate reads, not one atomic statement. Latest 200 requests/appointments; at most 20 suggestions within the recorded 14-day Singapore-date search. No consent, sharing, booking or financial execution.',
        source=Source(id='relationship-'+key,title=title,url=None,version=as_of,publisher='DemoBank demo records'))
        for key,title,data,as_of in rows]


def validate_result(task: Task,raw: str,evidence: list[Evidence]) -> AgentResult:
    result=validate_draft(task,raw,evidence)
    if ACTION_CLAIM.search(result.text):
        log_event('agent_validation',agent='relationship',reason='read_only_action_claim')
        return AgentResult(task_id=task.task_id,agent='relationship',status='error',text=WITHHELD)
    return result


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[AgentResult,list[Evidence],str]:
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Relationship task question must be text')
    evidence=[*public_evidence,*evidence_for(await relationship_records.load(ctx))]
    pv=prompts.load('relationship','v2')
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],draft_schema(evidence))
    return validate_result(task,json.dumps(response.data,ensure_ascii=False),evidence),evidence,pv['id']
