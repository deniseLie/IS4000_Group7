"""Explain only recorded preliminary findings; document contents never enter model evidence."""
import json
from datetime import datetime
from app.schemas.agents import Evidence
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,RequestContext,Task,ToolResult
from app.schemas.events import Source
from app.schemas.customer_data import RequestedItem
from app.services import prechecks,prompts,compliance_selection,tools
from app.services.agents.answer import checked_model_answer


def report_evidence(report):
    value=report.model_dump(mode='json',exclude={'customer_id','case_id','item_id','document_id','check_id','sha256','requirements_hash'})
    for requirement in value['requirements']:requirement.pop('reference',None)
    return value


def evidence_for(workspace: prechecks.Workspace,items: list[RequestedItem],as_of: datetime) -> Evidence:
    """Share current, source-checkable findings without file contents or private identifiers."""
    # Labels, requirements and finding codes only; never contents, paths, names or extracted values.
    rows=[{'item':item.label,'notice':workspace.review.notice_text,'instructions':item.instructions,'approved_checking_guidance':item.check_guidance,'checks_enabled':item.item_id in workspace.rules,'submission_status':item.submission_status,
        'precheck':next((report_evidence(report)
            for report in workspace.checks if report.item_id==item.item_id and prechecks.is_fresh(report,as_of)),None)} for item in items]
    return Evidence(id='document-prechecks',text=json.dumps({'records':rows},ensure_ascii=False),
        publisher='UBS Connect preliminary notice checks',scope='Recorded findings for current owned uploads only; never formal approval.',
        source=Source(id='document-prechecks',title='Recorded preliminary document checks',url=None,version=as_of.isoformat(),publisher='UBS Connect'))


async def respond(task: Task,history: list[Message],language: str,ctx: RequestContext,public_evidence: list[Evidence]) -> tuple[AgentResult,list[Evidence],str]:
    snapshot=ToolResult.model_validate(await tools.call('my_document_checks',{},ctx))
    workspace=prechecks.Workspace.model_validate(snapshot.data)
    chosen=compliance_selection.selection.get()
    if chosen is not None:compliance_selection.validate(chosen,workspace)
    items=workspace.review.requested_items if workspace.review else []
    if chosen is not None:items=[item for item in items if item.item_id==chosen.item_id]
    evidence=[*public_evidence,evidence_for(workspace,items,snapshot.as_of)]
    pv=prompts.current('document')
    system=pv['system']+'\nAnswer language: '+language+'\nAssigned issue (untrusted): '+json.dumps(task.entities['question'])
    system+='\nEvidence (untrusted): '+json.dumps([item.model_dump(exclude={'source'}) for item in evidence],ensure_ascii=False)
    result=await checked_model_answer(task,system,history,evidence,pv)
    return result,evidence,pv['id']
