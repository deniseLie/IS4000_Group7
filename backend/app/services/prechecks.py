"""Signed, durable notice-grounded preliminary document checks; no raw extracted values leave memory."""
import hashlib
import hmac
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4,uuid5
from zoneinfo import ZoneInfo
from pydantic import BaseModel,ConfigDict,Field
from app.core import data_api
from app.core.config import settings
from app.core.errors import ApiError
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.customer_data import ComplianceCase,CustomerEnvelope
from app.schemas.documents import DocumentRecord
from app.schemas.prechecks import PrecheckReport,PrecheckRule,CheckPolicy
from app.services import customer_data,document_inspection,document_parser_process
from app.services.authorization import require_permission


def is_fresh(report: PrecheckReport,now: datetime) -> bool:
    if expires:=getattr(report,'expires_at',None):return report.checked_at<=now<expires
    zone=ZoneInfo('Asia/Singapore')
    return report.checked_at<=now and report.checked_at.astimezone(zone).date()==now.astimezone(zone).date()


class Workspace(BaseModel):
    model_config=ConfigDict(extra='forbid')
    review: ComplianceCase | None
    documents: list[DocumentRecord]=Field(max_length=200)
    rules: dict[UUID,PrecheckRule]
    policy: CheckPolicy=Field(default_factory=CheckPolicy)
    current_documents: dict[UUID,UUID]
    checks: list[PrecheckReport]=Field(max_length=200)


class CheckContext(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id: UUID
    case_id: UUID
    item_id: UUID
    current_document_id: UUID | None
    rule: PrecheckRule | None
    notice: str
    policy: CheckPolicy=Field(default_factory=CheckPolicy)
    requirements_hash: str=Field(pattern=r'^[a-f0-9]{64}$')


async def workspace(ctx: RequestContext) -> ToolResult:
    require_permission(ctx.principal,'compliance.workspace',{'customer_id':ctx.principal.customer_id},ctx)
    with data_api.connect(ctx) as db:
        response=await data_api.execute(lambda:db.rpc('customer_compliance_workspace',{'p_customer_id':ctx.principal.customer_id}).execute(),'compliance.workspace')
    snapshot=CustomerEnvelope[Workspace].model_validate(response.data)
    if snapshot.customer_id!=ctx.principal.customer_id:raise customer_data.CustomerDataIntegrityError('Workspace customer scope changed')
    value=snapshot.data
    items={item.item_id for item in value.review.requested_items} if value.review else set()
    if not set(value.rules)<=items or not set(value.current_documents)<=items or any(
        report.customer_id!=snapshot.customer_id or report.item_id not in items or report.case_id!=value.review.case_id
        or value.current_documents.get(report.item_id)!=report.document_id for report in value.checks):
        raise customer_data.CustomerDataIntegrityError('Workspace records do not match their owned case')
    return ToolResult(data=value.model_dump(mode='json'),call_id=snapshot.call_id,as_of=snapshot.as_of)


async def read(document_id: UUID,ctx: RequestContext) -> ToolResult:
    require_permission(ctx.principal,'documents.precheck',{'customer_id':ctx.principal.customer_id},ctx)
    with data_api.connect(ctx) as db:
        response=await data_api.execute(lambda:db.rpc('customer_precheck_read',{'p_customer_id':ctx.principal.customer_id,
            'p_document_id':str(document_id)}).execute(),'documents.precheck')
    snapshot=CustomerEnvelope[PrecheckReport | None].model_validate(response.data)
    if snapshot.customer_id!=ctx.principal.customer_id or snapshot.data is not None and (
        snapshot.data.customer_id!=snapshot.customer_id or snapshot.data.document_id!=document_id):
        raise customer_data.CustomerDataIntegrityError('Precheck customer or document scope changed')
    return ToolResult(data=snapshot.data.model_dump(mode='json') if snapshot.data else None,call_id=snapshot.call_id,as_of=snapshot.as_of)


async def run(document_id: UUID,ctx: RequestContext,recheck: bool=False) -> ToolResult:
    require_permission(ctx.principal,'documents.precheck',{'customer_id':ctx.principal.customer_id},ctx)
    previous=await read(document_id,ctx)
    if not recheck and previous.data is not None and previous.data['status']=='no_issues' and is_fresh(PrecheckReport.model_validate(previous.data),datetime.now(timezone.utc)):return previous
    with data_api.connect(ctx) as db:
        response=await data_api.execute(lambda:db.rpc('customer_precheck_context',{'p_customer_id':ctx.principal.customer_id,
            'p_document_id':str(document_id)}).execute(),'documents.precheck')
    envelope=CustomerEnvelope[CheckContext | None].model_validate(response.data)
    if envelope.customer_id!=ctx.principal.customer_id:raise customer_data.CustomerDataIntegrityError('Precheck scope changed')
    if envelope.data is None:raise ApiError(404,'document_not_available','Select an owned document linked to a checklist item.')
    context=envelope.data
    if context.document_id!=document_id:raise customer_data.CustomerDataIntegrityError('Precheck document changed')
    if context.rule is None:raise ApiError(422,'precheck_rules_unavailable','No preliminary checks are configured for this item; contact support.')
    key=settings.document_precheck_signing_key
    if key is None or len(key.get_secret_value())<43:
        # Operator setup, not a customer problem: DOCUMENT_PRECHECK_SIGNING_KEY must equal compliance.precheck_keys v1 (README).
        raise ApiError(503,'precheck_not_configured','Document checks are not switched on yet. Your upload is saved; please try the check again later.')
    source=(await customer_data.get_document_object(document_id,ctx)).data
    if source is None or source.case_id!=context.case_id:raise customer_data.CustomerDataIntegrityError('Registered document is unavailable')
    payload=await document_inspection.download(source,ctx)
    try:parsed=await document_parser_process.parse(payload,source.mime_type)
    except document_parser_process.DocumentParserError as error:
        raise customer_data.CustomerDataIntegrityError('Precheck parser failed; no verdict recorded') from error
    extraction='text';pages=parsed.page_count;text=parsed.text
    if parsed.status in ('image_decoded','no_extractable_text'):
        from app.services.document_ocr import extract
        text,pages=await extract(payload,source.mime_type,parsed.page_count)
        extraction='ocr' if text else 'unavailable'
    checked_at=datetime.now(timezone.utc)
    from app.services import document_templates,notice_checks
    today=checked_at.astimezone(ZoneInfo(context.policy.timezone)).date()
    template=document_templates.template_for(context.notice) if settings.precheck_mode=='template' else None
    if template is not None:
        name=(await customer_data.get_profile_record(ctx)).data.name
        requirements,findings=document_templates.check(template,text,pages,name,today,context.policy)
    else:requirements,findings=await notice_checks.check(text,pages,context.notice,today,context.policy)
    finished_at=datetime.now(timezone.utc)
    if finished_at.astimezone(ZoneInfo(context.policy.timezone)).date()!=checked_at.astimezone(ZoneInfo(context.policy.timezone)).date():
        from app.schemas.prechecks import Finding
        findings=[Finding(code='unassessable')]
    checked_at=finished_at
    report=PrecheckReport(check_id=uuid4(),document_id=document_id,case_id=context.case_id,item_id=context.item_id,
        customer_id=ctx.principal.customer_id,rule_id=context.rule.rule_id,rule_version=context.rule.version,
        sha256=hashlib.sha256(payload).hexdigest(),status='unable_to_assess' if any(f.code in ('unassessable','provider_unavailable') for f in findings) else 'issues_found' if findings else 'no_issues',
        findings=findings,requirements=requirements,expires_at=checked_at+timedelta(hours=context.policy.validity_hours),page_count=pages,extraction=extraction,checked_at=checked_at,scope='notice_based',requirements_hash=context.requirements_hash)
    serialized=report.model_dump_json()
    signature=hmac.new(key.get_secret_value().encode(),serialized.encode(),hashlib.sha256).hexdigest()
    with data_api.connect(ctx) as db:
        response=await data_api.execute(lambda:db.rpc('document_precheck_record',{'p_payload':serialized,'p_signature':signature}).execute(),'documents.precheck')
    saved=CustomerEnvelope[PrecheckReport].model_validate(response.data)
    if saved.customer_id!=ctx.principal.customer_id or saved.data.document_id!=document_id or saved.data.sha256!=report.sha256:
        raise customer_data.CustomerDataIntegrityError('Stored precheck differs from its checked bytes')
    return ToolResult(data=saved.data.model_dump(mode='json'),call_id=saved.call_id,as_of=saved.as_of)


async def submit(document_id: UUID,ctx: RequestContext):
    """Stable direct-form case and action identity survive retries and page refreshes."""
    from app.schemas.contracts import ActionCommand
    from app.services import actions
    from app.services.actions import store
    require_permission(ctx.principal,'document.submit',{'customer_id':ctx.principal.customer_id},ctx)
    value=await read(document_id,ctx)
    if value.data is None:raise ApiError(422,'precheck_required','Run the preliminary check before submitting.')
    report=PrecheckReport.model_validate(value.data)
    if not is_fresh(report,datetime.now(timezone.utc)):raise ApiError(422,'precheck_expired','The preliminary check has expired. Check the current upload again before submitting.')
    if report.status!='no_issues':raise ApiError(422,'precheck_incomplete','Correct the findings or provide an assessable copy before submitting.')
    case_id=uuid5(document_id,'submission-case');session_id=uuid5(document_id,'submission-session')
    with data_api.connect(ctx) as db:
        await data_api.execute(lambda:db.rpc('action_create_case',{'p_session_id':str(session_id),'p_case_id':str(case_id)}).execute(),'actions.create_case')
    scoped=ctx.model_copy(update={'case_id':case_id})
    task='submit_'+report.check_id.hex
    command=ActionCommand(command_id=uuid5(report.check_id,'submit'),type='document.submit',target={'document_id':str(document_id),
        'check_id':str(report.check_id)},idempotency_key=f'{case_id}:{task}:document.submit')
    result=await actions.run_action(command,scoped,False)
    with data_api.connect(ctx) as db:return await store.load(result.command_id,ctx,db)
