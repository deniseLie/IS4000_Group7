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
from app.services import llm_gateway,prompts,tools
from app.core.observability import log_event
from app.services.agents import relationship_records
from app.services.agents.answer import WITHHELD,draft_schema,validate_draft
from app.services.privacy import redact
from app.services.source_checks import dates,dates_supported

ACTION_CLAIM=re.compile(r"\b(?:I|we)(?:['’]ve)?\s+(?:(?:have|will|just|successfully|already)\s+){0,3}(?:book(?:ed)?|reserv(?:e|ed)|schedul(?:e|ed)|cancel(?:led|ed)?|sent|send|submit(?:ted)?|shar(?:e|ed)|sav(?:e|ed)|grant(?:ed)?|revok(?:e|ed)|contact(?:ed)?)\b",re.I)


def advisor_name(advisor_id: UUID | None,assignment: RelationshipAssignment | None) -> str | None:
    if advisor_id is None or assignment is None:return None
    for advisor in (assignment.current,assignment.previous):
        if advisor is not None and advisor.rm_id==advisor_id:return advisor.name
    return None


def view(name: str,row: object) -> JsonValue:
    """One record as the Relationship spec's field view of tool `name` shows it (ids, emails and reason codes stay out)."""
    return tools.visible('relationship',name,row.model_dump(mode='json') if hasattr(row,'model_dump') else row)


def evidence_for(records: RelationshipRecords) -> list[Evidence]:
    """Her records for the relationship prompt, each through the spec's field view; names and flags code adds from the ids."""
    assignment=records.assignment.data
    requests: list[JsonValue]=[{**view('my_requests',row),
        'who':redact(row.who)[0],'what':redact(row.what)[0],'customer_action':redact(row.customer_action)[0] if row.customer_action else None,
        'advisor_name':advisor_name(row.advisor_id,assignment),'recorded_with_current_advisor':bool(assignment and assignment.current and row.advisor_id==assignment.current.rm_id)}
        for row in records.requests.data]
    appointments: list[JsonValue]=[{**view('my_appointments',row),
        'advisor_name':advisor_name(row.advisor_id,assignment)} for row in records.appointments.data]
    rows: list[tuple[str,str,JsonValue,str]]=[
        ('assignment','Your recorded relationship manager',view('my_rm',assignment) if assignment else None,records.assignment.as_of.isoformat()),
        ('requests','Your recorded relationship requests',requests,records.requests.as_of.isoformat()),
        ('appointments','Your recorded appointments',appointments,records.appointments.as_of.isoformat()),
        ('slots','Available appointment suggestions',{'search':records.search.model_dump(mode='json'),'slots':[
            {**view('free_slots',row),'advisor_name':advisor_name(row.advisor_id,assignment)}
            for row in records.slots.data]},records.slots.as_of.isoformat())]
    evidence=[Evidence(id='relationship-'+key,text=json.dumps({'records':data},ensure_ascii=False),publisher='UBS Connect demo records',
        scope='Own-customer audited snapshot. Separate reads, not one atomic statement. Latest 50 requests/appointments; at most 20 suggestions within the recorded 14-day Singapore-date search. No consent, sharing, booking or financial execution.',
        source=Source(id='relationship-'+key,title=title,url=None,version=as_of,publisher='UBS Connect demo records'))
        for key,title,data,as_of in rows]
    return evidence+transition_evidence(records)


def transition_evidence(records: RelationshipRecords) -> list[Evidence]:
    """Approved UBS Connect change wording selected in SQL from the recorded reason; the reason code itself is withheld."""
    intro=records.introduction
    if intro is None or not intro.data.notice:return []
    first=intro.data.notice[0]
    data={'previous_advisor':intro.data.previous.name if intro.data.previous else None,
        'current_advisor':intro.data.current.name if intro.data.current else None,
        'approved_wording':[{'heading':item.heading,'text':item.body} for item in intro.data.notice]}
    return [Evidence(id='relationship-transition',text=json.dumps({'records':data},ensure_ascii=False),publisher=first.publisher,
        scope='Approved UBS Connect wording for this recorded relationship-manager change. The only permitted explanation of the change; no other reason is recorded.',
        source=Source(id='relationship-transition',title=first.title,url=f'/knowledge/sections/{first.article_id}/{first.version}/{first.anchor}',
            version=f'v{first.version}',publisher=first.publisher))]


def validate_result(task: Task,raw: str,evidence: list[Evidence]) -> AgentResult:
    result=validate_draft(task,raw,evidence)
    cited={citation.source_id for citation in result.citations}
    recorded={date for item in evidence if item.id in cited for date in dates(item.text)}
    if not dates_supported(dates(result.text),recorded):
        log_event('agent_validation',agent='relationship',reason='unsupported_date')
        return AgentResult(task_id=task.task_id,agent='relationship',status='error',text=WITHHELD)
    if ACTION_CLAIM.search(result.text):
        log_event('agent_validation',agent='relationship',reason='read_only_action_claim')
        return AgentResult(task_id=task.task_id,agent='relationship',status='error',text=WITHHELD)
    return result


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence],
        records: RelationshipRecords | None=None) -> tuple[AgentResult,list[Evidence],str]:
    """The model answer from her relationship records. Meeting requests are drafted from the records before this
    (agents/meeting_answers, called by rules.relationship_rules with the same records)."""
    question=task.entities['question']
    if not isinstance(question,str):raise ValueError('Relationship task question must be text')
    records=records or await relationship_records.load(ctx)
    evidence=[*public_evidence,*evidence_for(records)]
    pv=prompts.current('relationship')
    system=pv['system']+'\nAnswer language: '+language
    system+='\nAssigned issue (untrusted): '+json.dumps(question,ensure_ascii=False)
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    response=await llm_gateway.call(pv,system,[(m.role,m.content) for m in history],draft_schema(evidence))
    return validate_result(task,json.dumps(response.data,ensure_ascii=False),evidence),evidence,pv['id']
