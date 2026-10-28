"""Explain only recorded preliminary findings; document contents never enter model evidence."""
import json
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task
from app.schemas.events import Source
from app.services import llm_gateway,prechecks,prompts,compliance_selection
from app.services.agents.answer import draft_schema,validate_draft


def report_evidence(report):
    value=report.model_dump(mode='json',exclude={'customer_id','case_id','item_id','document_id','check_id','sha256','requirements_hash'})
    for requirement in value['requirements']:requirement.pop('reference',None)
    return value


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[AgentResult,list[Evidence],str]:
    snapshot=await prechecks.workspace(ctx)
    workspace=prechecks.Workspace.model_validate(snapshot.data)
    chosen=compliance_selection.selection.get()
    if chosen is not None:compliance_selection.validate(chosen,workspace)
    # Labels, requirements and finding codes only; never contents, paths, names or extracted values.
    items=workspace.review.requested_items if workspace.review else []
    if chosen is not None:items=[item for item in items if item.item_id==chosen.item_id]
    rows=[{'item':item.label,'notice':workspace.review.notice_text,'instructions':item.instructions,'approved_checking_guidance':item.check_guidance,'checks_enabled':item.item_id in workspace.rules,'submission_status':item.submission_status,
        'precheck':next((report_evidence(report)
            for report in workspace.checks if report.item_id==item.item_id and prechecks.is_fresh(report,snapshot.as_of)),None)} for item in items]
    evidence=[*public_evidence,Evidence(id='document-prechecks',text=json.dumps({'records':rows},ensure_ascii=False),
        publisher='DemoBank preliminary notice checks',scope='Recorded findings for current owned uploads only; never formal approval.',
        source=Source(id='document-prechecks',title='Recorded preliminary document checks',url=None,version=snapshot.as_of.isoformat(),publisher='DemoBank'))]
    pv=prompts.load('document','v4')
    system=pv['system']+'\nAnswer language: '+language+'\nAssigned issue (untrusted): '+json.dumps(task.entities['question'])
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    result=await llm_gateway.call(pv,system,[(item.role,item.content) for item in history],draft_schema(evidence))
    return validate_draft(task,json.dumps(result.data),evidence),evidence,pv['id']
