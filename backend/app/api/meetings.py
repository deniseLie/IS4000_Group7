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
from app.schemas.meeting_lifecycle import (Approval,CallTime,Claims,Command,Completion,DraftReceipt,Notes,SelectedActions,Start,GraphState,NotesContext,Model)
from app.services.meetings.database import ApplicationDatabase,MeetingStoreError,Parameter
from app.services.meetings.transcripts import ImportRequest,imported
from app.services.meetings.zoom import Zoom,refreshed_token,ZoomFailure
from app.services.privacy import redact

router=APIRouter()

class Proposal(Model):
    customer_id: int=Field(strict=True,ge=1)
    starts_at: AwareDatetime
    purpose: str=Field(min_length=1,max_length=200)
    topics: tuple[str,...]=Field(min_length=1,max_length=5)
    command_id: UUID

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
    except MeetingStoreError as error:
        status={'42501':403,'P0002':404,'P1001':409,'23505':409}.get(error.code)
        if status is None and error.code.startswith('22'):status=422
        if status in (403,409):db.record_refusal(name,error.code,claims)
        raise ApiError(status or 503,'meeting_store_error',str(error)) from error

@router.get('/meetings/capabilities')
def capabilities(ctx: Annotated[RequestContext,Depends(get_ctx)]) -> dict[str,bool]:
    if ctx.principal.jwt is None:raise ApiError(401,'auth_required','Sign in to view meeting capabilities.')
    return {'enabled':settings.meetings_enabled,'zoom_configured':settings.zoom_oauth_directory is not None}

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
async def generate_notes(meeting: UUID, body: Command, claims: Annotated[Claims, Depends(caller)]) -> DraftReceipt:
    from starlette.concurrency import run_in_threadpool
    from app.services.meetings.graph import notes_graph
    context = NotesContext.model_validate(await run_in_threadpool(call, 'meeting_notes_context', {'p_meeting_id': meeting}, claims))
    params = {'p_meeting_id': meeting, 'p_source_id': context.source_id, 'p_source_hash': context.source_hash,
        'p_revision': context.revision, 'p_consent_epoch': context.consent_epoch, 'p_command_id': body.command_id}
    replay = await run_in_threadpool(call, 'meeting_generation_receipt', params, claims)
    if replay is not None:
        return DraftReceipt.model_validate(replay)
    graph = notes_graph(context, db, claims, body.command_id)
    from app.services.llm_gateway import LLMError
    try:
        await graph.ainvoke(GraphState(meeting_id=meeting, source_id=context.source_id, draft_id=None))
    except LLMError as error:
        raise HTTPException(503, detail={'code': error.reason, 'message': 'Minutes generation failed: ' + str(error)}) from error
    except ValidationError as error:
        raise HTTPException(422, detail={'code': 'generated_draft_invalid',
            'message': 'Generated minutes exceed a required field bound. The reviewed client recap is limited to 500 characters; shorten the summary or actions and submit a revised draft.',
            'fields': ['.'.join(str(part) for part in item['loc']) for item in error.errors(include_input=False)]}) from error
    except MeetingStoreError as error:
        if error.code in ('P1001', '23505'):
            raise HTTPException(409, detail={'code': error.code, 'message': str(error)}) from error
        raise
    receipt = await run_in_threadpool(call, 'meeting_generation_receipt', params, claims)
    return DraftReceipt.model_validate(receipt)

@router.post('/meetings/{meeting}/approve-minutes')
def approve(meeting: UUID, body: Approval, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_approve_minutes', approval_params(meeting, body), claims)

@router.post('/meetings/{meeting}/followups')
def followups(meeting: UUID, body: SelectedActions, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    params = {**approval_params(meeting, body), 'p_action_ids': list(body.action_ids)}
    return call('meeting_create_followups', params, claims)

@router.post('/meetings/{meeting}/share-recap')
def share(meeting: UUID, body: Approval, claims: Annotated[Claims, Depends(caller)]) -> JsonValue:
    return call('meeting_share_recap', approval_params(meeting, body), claims)

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

@router.post('/meetings/{meeting}/transcript-import')
def transcript_import(meeting: UUID,body: ImportRequest,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    # Check current ownership before parsing user text; the SQL write independently checks it again.
    current=call('meeting_view',{'p_meeting_id':meeting},claims)
    if claims.app_metadata.demobank_role!='rm':
        raise HTTPException(403,detail='Only the assigned RM can import this meeting transcript')
    try:source=imported(body)
    except ValueError as error:raise HTTPException(422,detail=str(error)) from error
    receipt=call('meeting_import_transcript',{'p_meeting_id':meeting,'p_revision':body.revision,
        'p_cues':Jsonb([cue.model_dump(mode='json') for cue in source.cues]),'p_origin':source.origin,
        'p_file_hash':source.file_hash,'p_coverage':source.coverage,'p_command_id':body.command_id},claims)
    if not isinstance(receipt,dict):raise RuntimeError('Import SQL returned an invalid receipt')
    return {**receipt,'redaction_count':source.redaction_count,'cue_count':len(source.cues)}

@router.get('/meetings/{meeting}/zoom')
def link(meeting: UUID,claims: Annotated[Claims,Depends(caller)]) -> JsonValue:
    return call('meeting_link',{'p_meeting_id':meeting},claims)

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
