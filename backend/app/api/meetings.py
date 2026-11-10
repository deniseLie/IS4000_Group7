"""Meeting lifecycle under the existing gateway and caller-JWT Data API. No lab identities or owner database access.

Scheduling remains in the client action ledger and RM Calendar. Minutes Writer receives only this meeting's redacted
source. Approving minutes, creating selected follow-ups and sharing the client recap are separate explicit requests.
"""
from collections.abc import Mapping
from datetime import datetime,timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import JsonValue,ValidationError,Field,AwareDatetime
from psycopg.types.json import Jsonb
from app.core import auth
from app.core.config import settings
from app.core.errors import ApiError
from app.core.gateway import current_ctx,get_ctx
from app.schemas.contracts import RequestContext
from app.schemas.meeting_lifecycle import (Approval,CallTime,Claims,Command,Completion,DraftReceipt,Notes,RecapShare,SelectedActions,Start,GraphState,NotesContext,Model)
from app.services.agents.registry import MINUTES_WRITER
from app.services.meetings.database import ApplicationDatabase,MeetingStoreError,Parameter
from app.services.meetings.transcripts import ImportRequest,imported
from app.services.meetings.zoom import Zoom,refreshed_token,ZoomFailure
from app.services.privacy import redact
from app.schemas.zoom_capture import SpeakerMapping,ZoomEvidence
from app.schemas.meeting_recovery import JobRetry,RecoveryView,TimeCorrection,TimeReview

router=APIRouter()

class Proposal(Model):
    customer_id: int=Field(strict=True,ge=1)
    starts_at: AwareDatetime
    purpose: str=Field(min_length=1,max_length=200)
    topics: tuple[str,...]=Field(min_length=1,max_length=5)
    command_id: UUID

class RevisionCommand(Command):
    revision: int=Field(strict=True,ge=1)

class RequestDatabase:
    """Stateless connector; resolve the verified context separately on every call."""
    def call(self,name: str,params: Mapping[str,Parameter],claims: Claims) -> JsonValue:
        ctx=current_ctx.get()
        if not isinstance(ctx,RequestContext):raise RuntimeError('Meeting API requires the verified gateway context')
        return ApplicationDatabase(ctx).call(name,params,claims)

    def record_refusal(self,name: str,code: str,claims: Claims) -> None:
        self.call('meeting_record_refusal',{'p_operation':name,'p_code':code},claims)

db=RequestDatabase()

def caller(ctx: Annotated[RequestContext,Depends(get_ctx)]) -> Claims:
    if not settings.meetings_enabled:raise ApiError(503,'meetings_unavailable','Meeting lifecycle is switched off.')
    if ctx.principal.jwt is None:raise ApiError(401,'auth_required','Sign in to your meeting workspace.')
    return Claims.model_validate(auth.verify_token(ctx.principal.jwt.get_secret_value()))

def call(name: str,params: Mapping[str,Parameter],claims: Claims) -> JsonValue:
    try:return db.call(name,params,claims)
    except MeetingStoreError as error:raise store_error(name,error,claims) from error


def store_error(name: str,error: MeetingStoreError,claims: Claims) -> ApiError:
    status={'42501':403,'P0002':404,'P1001':409,'23505':409}.get(error.code)
    if status is None and error.code.startswith('22'):status=422
    if status in (403,409):db.record_refusal(name,error.code,claims)
    return ApiError(status or 503,'meeting_store_error',str(error))

@router.get('/meetings/capabilities')
def capabilities(ctx: Annotated[RequestContext,Depends(get_ctx)]) -> dict[str,bool]:
    if ctx.principal.jwt is None:raise ApiError(401,'auth_required','Sign in to view meeting capabilities.')
    return {'enabled':settings.meetings_enabled,'zoom_configured':settings.zoom_oauth_directory is not None,'zoom_automation_enabled':settings.zoom_capture_enabled}

class TranscriptStanding(Model):
    on: bool

# 0149: the client's one yes for every meeting's transcript (Data sharing › Meeting recording). Before the {meeting} routes.
@router.get('/meetings/transcript-standing')
def transcript_standing(claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_transcript_standing',{},claims)

@router.post('/meetings/transcript-standing')
def set_transcript_standing(body: TranscriptStanding,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_set_transcript_standing',{'p_on':body.on},claims)

@router.get('/meetings/session')
def session(claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_identity',{},claims)

@router.get('/meetings/slots/{customer}')
def slots(customer: int,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_slots',{'p_customer_id':customer},claims)

@router.get('/meetings/{meeting}/appointment')
def appointment(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_appointment',{'p_meeting_id':meeting},claims)

@router.post('/meetings/proposals')
def propose(body: Proposal,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_rm_propose',{'p_customer_id':body.customer_id,'p_starts_at':body.starts_at.isoformat(),
        'p_purpose':redact(body.purpose)[0],'p_topics':Jsonb(list(body.topics)),'p_command_id':body.command_id},claims)



def approval_params(meeting: UUID, body: Approval) -> dict[str, Parameter]:
    return {'p_meeting_id': meeting, 'p_draft_id': body.draft_id, 'p_version': body.version,
        'p_content_hash': body.content_hash, 'p_revision': body.revision,
        'p_consent_epoch': body.consent_epoch, 'p_command_id': body.command_id}

@router.post('/meetings/{meeting}/start')
def start(meeting: UUID, body: Start, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_start_workflow', {'p_meeting_id': meeting, 'p_notice_id': body.notice_id,
        'p_purpose': redact(body.purpose)[0], 'p_outcome': redact(body.outcome)[0], 'p_command_id': body.command_id}, claims)

@router.get('/meetings/{meeting}')
def view(meeting: UUID, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_view', {'p_meeting_id': meeting}, claims)

@router.post('/meetings/{meeting}/documented-start')
def document_start(meeting: UUID, body: CallTime, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_document_call_start', {'p_meeting_id': meeting, 'p_revision': body.revision,
        'p_at': body.at.isoformat(), 'p_command_id': body.command_id}, claims)

@router.post('/meetings/{meeting}/documented-end')
def document_end(meeting: UUID, body: CallTime, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_document_call_end', {'p_meeting_id': meeting, 'p_revision': body.revision,
        'p_at': body.at.isoformat(), 'p_command_id': body.command_id}, claims)

@router.post('/meetings/{meeting}/notes')
def notes(meeting: UUID, body: Notes, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_submit_notes', {'p_meeting_id': meeting, 'p_revision': body.revision,
        'p_cues': Jsonb([{**cue.model_dump(mode='json'),'text':redact(cue.text)[0]} for cue in body.cues]), 'p_command_id': body.command_id}, claims)

@router.post('/meetings/{meeting}/generate-notes-draft')
async def generate_notes(meeting: UUID, body: Command, ctx: Annotated[RequestContext, Depends(get_ctx)], claims: Annotated[Claims, Depends(caller)]) -> DraftReceipt:
    from starlette.concurrency import run_in_threadpool
    from app.services.meetings.graph import notes_graph
    database = ApplicationDatabase(ctx)
    try:
        raw = await database.agent_read(MINUTES_WRITER.name, 'meeting_notes_context', meeting, {'p_meeting_id': meeting}, claims)
    except MeetingStoreError as error:
        raise await run_in_threadpool(store_error, 'meeting_notes_context', error, claims) from error
    context = NotesContext.model_validate(raw)
    params = {'p_meeting_id': meeting, 'p_source_id': context.source_id, 'p_source_hash': context.source_hash,
        'p_revision': context.revision, 'p_consent_epoch': context.consent_epoch, 'p_command_id': body.command_id}
    replay = await run_in_threadpool(call, 'meeting_generation_receipt', params, claims)
    if replay is not None:
        return DraftReceipt.model_validate(replay)
    graph = notes_graph(context, database, claims, body.command_id)
    from app.services.llm_gateway import LLMError
    try:
        await graph.ainvoke(GraphState(meeting_id=meeting, source_id=context.source_id, draft_id=None))
    except LLMError as error:
        raise HTTPException(503, detail={'code': error.reason, 'message': 'Minutes generation failed: ' + str(error)}) from error
    except ValidationError as error:
        raise HTTPException(422, detail={'code': 'generated_draft_invalid',
            'message': 'Generated minutes do not satisfy the required field bounds. Review the listed fields before submitting a revised draft.',
            'fields': ['.'.join(str(part) for part in item['loc']) for item in error.errors(include_input=False)]}) from error
    except MeetingStoreError as error:
        if error.code in ('P1001', '23505'):
            raise HTTPException(409, detail={'code': error.code, 'message': str(error)}) from error
        raise await run_in_threadpool(store_error, 'meeting_save_reviewed_draft', error, claims) from error
    receipt = await run_in_threadpool(call, 'meeting_generation_receipt', params, claims)
    return DraftReceipt.model_validate(receipt)

@router.post('/meetings/{meeting}/approve-minutes')
def approve(meeting: UUID, body: Approval, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_approve_minutes', approval_params(meeting, body), claims)

@router.post('/meetings/{meeting}/followups')
def followups(meeting: UUID, body: SelectedActions, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    edits = body.edit_params()  # 0141: her changes; free text is redacted like every other RM-typed field
    if edits: edits = {k: {**e, **({'text': redact(e['text'])[0]} if e.get('text') else {})} for k, e in edits.items()}
    params = {**approval_params(meeting, body), 'p_action_ids': list(body.action_ids), 'p_edits': edits}
    return call('meeting_create_followups', params, claims)

@router.post('/meetings/{meeting}/share-recap')
def share(meeting: UUID, body: RecapShare, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_share_recap', {**approval_params(meeting, body), 'p_text': redact(body.text)[0] if body.text else None}, claims)

@router.post('/meetings/{meeting}/followups/{followup}/complete')
def complete(meeting: UUID, followup: UUID, body: Completion, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_complete_followup', {'p_meeting_id': meeting, 'p_followup_id': followup,
        'p_evidence': redact(body.evidence)[0], 'p_command_id': body.command_id}, claims)

class NoticeSeen(Model):
    notice_id: UUID
    revision: int=Field(strict=True,ge=1)

@router.post('/meetings/{meeting}/notice-seen')
def notice_seen(meeting: UUID,body: NoticeSeen,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_acknowledge_notice',{'p_meeting_id':meeting,'p_notice_id':body.notice_id,'p_revision':body.revision},claims)

@router.post('/meetings/{meeting}/transcript-revoke')
def transcript_revoke(meeting: UUID,body: Command,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_transcript_revoke',{'p_meeting_id':meeting,'p_command_id':body.command_id},claims)

@router.get('/meetings/{meeting}/recovery')
def recovery(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> RecoveryView:
    return RecoveryView.model_validate(call('meeting_recovery',{'p_meeting_id':meeting},claims))

def time_review_params(meeting: UUID,body: TimeReview) -> dict[str,Parameter]:
    return {'p_meeting_id':meeting,'p_revision':body.revision,'p_expected_start':body.expected_start.isoformat() if body.expected_start else None,
        'p_expected_end':body.expected_end.isoformat() if body.expected_end else None,'p_reason':redact(body.reason)[0],'p_command_id':body.command_id}

@router.post('/meetings/{meeting}/correct-call-times')
def correct_call_times(meeting: UUID,body: TimeCorrection,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_correct_call_times',{**time_review_params(meeting,body),'p_started_at':body.started_at.isoformat(),'p_ended_at':body.ended_at.isoformat()},claims)

@router.post('/meetings/{meeting}/reconcile-zoom-times')
def reconcile_zoom_times(meeting: UUID,body: TimeReview,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_reconcile_zoom_times',time_review_params(meeting,body),claims)

@router.post('/meetings/{meeting}/jobs/{event}/retry')
def retry_job(meeting: UUID,event: int,body: JobRetry,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_retry_job',{'p_meeting_id':meeting,'p_event_id':event,'p_revision':body.revision,
        'p_reason':redact(body.reason)[0],'p_command_id':body.command_id},claims)

def fit_to_call(source,recovery):
    """SQL (0121) refuses a cue that starts after the recorded call ended. A demo call is often ended early, so cue
    positions are scaled proportionally into the recorded length; order and text are unchanged."""
    from datetime import datetime
    try:
        length=int((datetime.fromisoformat(str(recovery['ended_at']))-datetime.fromisoformat(str(recovery['started_at']))).total_seconds())
    except (KeyError,TypeError,ValueError):return source
    last=max((cue.start_seconds or 0 for cue in source.cues),default=0)
    if length<0 or last<=length:return source
    cues=tuple(cue.model_copy(update={'start_seconds':None if cue.start_seconds is None else cue.start_seconds*length//last}) for cue in source.cues)
    return source.model_copy(update={'cues':cues})


@router.post('/meetings/{meeting}/transcript-import')
def transcript_import(meeting: UUID,body: ImportRequest,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    # Check current ownership before parsing user text; the SQL write independently checks it again.
    current=call('meeting_view',{'p_meeting_id':meeting},claims)
    if claims.app_metadata.demobank_role!='rm':
        raise HTTPException(403,detail='Only the assigned RM can import this meeting transcript')
    try:source=imported(body)
    except ValueError as error:raise HTTPException(422,detail=str(error)) from error
    try:source=fit_to_call(source,call('meeting_recovery',{'p_meeting_id':meeting},claims))
    except Exception:pass  # SQL still enforces the rule if the call times can't be read
    receipt=call('meeting_import_transcript',{'p_meeting_id':meeting,'p_revision':body.revision,
        'p_cues':Jsonb([cue.model_dump(mode='json') for cue in source.cues]),'p_origin':source.origin,
        'p_file_hash':source.file_hash,'p_coverage':source.coverage,'p_command_id':body.command_id},claims)
    if not isinstance(receipt,dict):raise RuntimeError('Import SQL returned an invalid receipt')
    return {**receipt,'redaction_count':source.redaction_count,'cue_count':len(source.cues)}

@router.get('/meetings/{meeting}/zoom')
def link(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_link',{'p_meeting_id':meeting},claims)

@router.get('/meetings/{meeting}/zoom/evidence')
def zoom_evidence(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> ZoomEvidence|None:
    value=call('meeting_zoom_evidence',{'p_meeting_id':meeting},claims)
    return None if value is None else ZoomEvidence.model_validate(value)

@router.post('/meetings/{meeting}/zoom/check-transcript')
def check_transcript(meeting: UUID,body: RevisionCommand,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_request_discovery',{'p_meeting_id':meeting,'p_revision':body.revision,'p_command_id':body.command_id},claims)

@router.post('/meetings/{meeting}/zoom/request-link')
def request_zoom_link(meeting: UUID,body: RevisionCommand,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    if not settings.zoom_capture_enabled or settings.zoom_oauth_directory is None:
        raise ApiError(503,'zoom_not_configured','Connect the approved private Zoom grant and restricted worker before requesting a provider link.')
    return call('meeting_request_zoom',{'p_meeting_id':meeting,'p_revision':body.revision,'p_command_id':body.command_id},claims)

@router.post('/meetings/{meeting}/zoom/speakers')
def zoom_speakers(meeting: UUID,body: SpeakerMapping,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_zoom_map_speakers',{'p_meeting_id':meeting,'p_source':body.source_id,'p_hash':body.source_hash,
        'p_revision':body.revision,'p_epoch':body.consent_epoch,'p_mapping':Jsonb(body.mapping),'p_command':body.command_id},claims)

@router.post('/meetings/{meeting}/zoom/automated')
def create_automated_zoom(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    if not settings.zoom_capture_enabled:raise ApiError(503,'zoom_automation_unavailable','Automatic Zoom intake is not enabled.')
    if claims.app_metadata.demobank_role!='rm':raise HTTPException(403,detail='Only the assigned RM can create the meeting host link')
    appointment=call('meeting_appointment',{'p_meeting_id':meeting},claims)
    if not isinstance(appointment,dict) or appointment.get('status')!='confirmed':raise HTTPException(409,detail='Confirm the appointment first')
    at=datetime.fromisoformat(str(appointment['starts_at']))
    if at<=datetime.now(timezone.utc):raise HTTPException(409,detail='Create the bound Zoom link before the call starts')
    current=call('meeting_view',{'p_meeting_id':meeting},claims)
    previous=call('meeting_link',{'p_meeting_id':meeting},claims)
    if previous is not None:
        if isinstance(current,dict) and current.get('zoom_capture') is not None:return previous
        raise HTTPException(409,detail='Existing Zoom link has no original occurrence binding; reconcile it explicitly before automatic intake')
    if settings.zoom_oauth_directory is None:raise ApiError(503,'zoom_not_configured','Configure the private own-user OAuth directory first.')
    provider=Zoom(refreshed_token(settings.zoom_oauth_directory))
    try:
        owner=provider.owner()
        call('meeting_zoom_reserve',{'p_meeting_id':meeting},claims)
        remote=provider.create_bound(meeting,at,owner)
        call('meeting_zoom_link_bind',{'p_meeting_id':meeting,'p_provider_id':remote.id,'p_join_url':remote.join_url,
            'p_starts_at':remote.start_time.isoformat(),'p_account_id':owner.account_id,'p_host_id':owner.id,'p_original_uuid':remote.uuid},claims)
        return call('meeting_link',{'p_meeting_id':meeting},claims)
    except ZoomFailure as error:raise HTTPException(502,detail=str(error)) from error
    finally:provider.close()

@router.post('/meetings/{meeting}/zoom')
def create_zoom(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    if claims.app_metadata.demobank_role!='rm':raise HTTPException(403,detail='Only the assigned RM can create the host meeting')
    appointment=call('meeting_appointment',{'p_meeting_id':meeting},claims)
    if not isinstance(appointment,dict) or appointment.get('status')!='confirmed':raise HTTPException(409,detail='Confirm the appointment first')
    if datetime.fromisoformat(str(appointment['starts_at']))<=datetime.now(timezone.utc):raise ApiError(409,'zoom_meeting_past','Create a Zoom link before the scheduled call starts.')
    previous=call('meeting_link',{'p_meeting_id':meeting},claims)
    if previous is not None:return previous
    if settings.zoom_oauth_directory is None:raise ApiError(503,'zoom_not_configured','Configure the private Zoom OAuth directory; no meeting was created.')
    api=Zoom(refreshed_token(settings.zoom_oauth_directory))
    try:
        call('meeting_zoom_reserve',{'p_meeting_id':meeting},claims)
        remote=api.create(meeting,datetime.fromisoformat(str(appointment['starts_at'])))
        call('meeting_link_save',{'p_meeting_id':meeting,'p_provider_id':remote.id,'p_join_url':remote.join_url,'p_starts_at':remote.start_time.isoformat()},claims)
        return call('meeting_link',{'p_meeting_id':meeting},claims)
    except ZoomFailure as error:raise HTTPException(502,detail=str(error)) from error
    finally:api.close()
