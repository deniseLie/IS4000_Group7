"""RM-only preparation routes, using the existing verified caller and audited failure boundary."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID,uuid5
from fastapi import APIRouter,Depends,Query
from pydantic import ValidationError
from app.api.meetings import caller
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.schemas.contracts import RequestContext
from app.schemas.meeting_lifecycle import Approval,Claims,Command,Hash
from app.schemas.meeting_reviews import MinutesReview,Regeneration,SavedReview
from app.schemas.meetings import Decision,MeetingStatus
from app.schemas.meeting_preparation import BriefDecision,PreparedBrief,StoredBrief
from app.services.llm_gateway import LLMError
from app.services.meetings import preparation
from app.services.meetings.database import ApplicationDatabase,MeetingStoreError
from app.services.privacy import redact
from app.schemas.meeting_packs import PackApproval,PackRequest,PackView,StoredPack
from app.services.meetings import packs
from app.services import meetings
from fastapi.responses import Response
from app.services.meetings import documents
from app.schemas.meeting_reminders import Reminder
from pydantic import TypeAdapter

router=APIRouter()

@router.get('/meetings/reminders')
async def read_reminders(ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> tuple[Reminder,...]:
    async with preparation_errors(ctx,claims):
        value=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_reminders',{},claims)
        return TypeAdapter(tuple[Reminder,...]).validate_python(value)

@router.post('/meetings/reminders/{reminder}/acknowledge')
async def acknowledge_reminder(reminder: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> None:
    async with preparation_errors(ctx,claims):await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_acknowledge_reminder',{'p_reminder':reminder},claims)

@asynccontextmanager
async def preparation_errors(ctx: RequestContext,claims: Claims) -> AsyncIterator[None]:
    try:yield
    except MeetingStoreError as error:
        status={'42501':403,'P0002':404,'P1001':409,'23505':409}.get(error.code)
        if status is None and error.code.startswith('22'):status=422
        if status in (403,409):await asyncio.to_thread(ApplicationDatabase(ctx).record_refusal,'meeting_preparation',error.code,claims)
        raise ApiError(status or 503,'meeting_preparation_failed',str(error)) from error
    except LLMError as error:raise ApiError(503,error.reason,'Meeting preparation model failed: '+str(error)) from error
    except ValidationError as error:
        details=[('.'.join(map(str,item['loc'])) or 'document')+': '+item['msg'] for item in error.errors(include_input=False,include_context=False)]
        raise ApiError(422,'meeting_preparation_invalid','Preparation failed validation. '+ '; '.join(details)) from error
    except PermissionError as error:raise ApiError(403,'meeting_preparation_refused',str(error)) from error
    except ValueError as error:raise ApiError(422,'brief_reference_invalid',str(error)) from error

@router.get('/meetings/{meeting}/preparation')
async def read_preparation(meeting: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> PreparedBrief|None:
    async with preparation_errors(ctx,claims):return await preparation.read(meeting,ctx)

def pdf_response(content: bytes,filename: str) -> Response:
    return Response(content,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{filename}"',
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.get('/meetings/{meeting}/preparation/pdf')
async def brief_pdf(meeting: UUID,version: Annotated[int,Query(ge=1)],content_hash: Hash,
    ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> Response:
    async with preparation_errors(ctx,claims):
        prepared=await preparation.read(meeting,ctx)
        if prepared is None:raise ApiError(404,'meeting_brief_unavailable','Prepare the saved brief before downloading it.')
        if prepared.card.version!=version or prepared.content_hash!=content_hash or prepared.sources_changed:
            raise ApiError(409,'meeting_brief_changed','The selected brief or its source access changed. Refresh the brief before downloading.')
        return pdf_response(await asyncio.to_thread(documents.pdf,documents.internal_brief(prepared)),f'meeting-brief-{meeting}-v{version}.pdf')

@router.post('/meetings/{meeting}/preparation')
async def prepare(meeting: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> PreparedBrief:
    async with preparation_errors(ctx,claims):return await preparation.prepare(meeting,ctx)

@router.post('/meetings/{meeting}/preparation/refresh')
async def refresh(meeting: UUID,body: Command,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> PreparedBrief:
    async with preparation_errors(ctx,claims):return await preparation.refresh(meeting,ctx,body.command_id)

@router.post('/meetings/{meeting}/preparation/decision')
async def decide(meeting: UUID,body: BriefDecision,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> StoredBrief:
    async with preparation_errors(ctx,claims):
        value=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_decide_brief',{'p_meeting_id':meeting,'p_version':body.version,
            'p_content_hash':body.content_hash,'p_revision':body.revision,'p_outcome':body.outcome,
            'p_note':redact(body.note)[0] if body.note else None,'p_command_id':body.command_id},claims)
        return StoredBrief.model_validate(value)

@router.get('/meetings/{meeting}/pack')
async def read_pack(meeting: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> PackView|None:
    async with preparation_errors(ctx,claims):return await packs.read(meeting,ctx)

@router.get('/meetings/{meeting}/pack/pdf')
async def pack_pdf(meeting: UUID,document_id: UUID,version: Annotated[int,Query(ge=1)],content_hash: Hash,
    ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> Response:
    async with preparation_errors(ctx,claims):
        pack=await packs.read(meeting,ctx)
        if pack is None:raise ApiError(404,'meeting_pack_unavailable','No discussion pack is available to this participant.')
        if pack.document_id!=document_id or pack.version!=version or pack.content_hash!=content_hash or pack.sources_changed:
            raise ApiError(409,'meeting_pack_changed','The selected pack or its source terms changed. Refresh and review the current document before downloading.')
        content=await asyncio.to_thread(documents.pdf,documents.discussion_pack(pack))
        return pdf_response(content,f'meeting-pack-{meeting}-v{version}.pdf')

@router.get('/meetings/{meeting}/recap')
async def read_recap(meeting: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> documents.Recap|None:
    async with preparation_errors(ctx,claims):
        value=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_recap_document',{'p_meeting_id':meeting},claims)
        return None if value is None else documents.Recap.model_validate(value)

@router.get('/meetings/{meeting}/recap/pdf')
async def recap_pdf(meeting: UUID,document_id: UUID,version: Annotated[int,Query(ge=1)],content_hash: Hash,
    ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> Response:
    async with preparation_errors(ctx,claims):
        value=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_recap_document',{'p_meeting_id':meeting},claims)
        if value is None:raise ApiError(404,'meeting_recap_unavailable','The RM must approve and separately share the recap before it can be downloaded.')
        recap=documents.Recap.model_validate(value)
        if recap.document_id!=document_id or recap.version!=version or recap.content_hash!=content_hash:
            raise ApiError(409,'meeting_recap_changed','A different approved recap is now shared. Refresh before downloading.')
        return pdf_response(await asyncio.to_thread(documents.pdf,documents.shared_recap(recap)),f'meeting-recap-{meeting}-v{version}.pdf')

@router.post('/meetings/{meeting}/pack/draft')
async def draft_pack(meeting: UUID,body: PackRequest,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> PackView:
    async with preparation_errors(ctx,claims):return await packs.draft(meeting,body,ctx)

def pack_approval_params(meeting: UUID,body: PackApproval) -> dict[str,UUID|int|str]:
    return {'p_meeting_id':meeting,'p_document_id':body.document_id,'p_version':body.version,'p_revision':body.revision,'p_content_hash':body.content_hash,'p_command_id':body.command_id}

@router.post('/meetings/{meeting}/pack/approve')
async def approve_pack(meeting: UUID,body: PackApproval,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> StoredPack:
    async with preparation_errors(ctx,claims):return StoredPack.model_validate(await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_approve_pack',pack_approval_params(meeting,body),claims))

@router.post('/meetings/{meeting}/pack/share')
async def share_pack(meeting: UUID,body: PackApproval,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> StoredPack:
    async with preparation_errors(ctx,claims):return StoredPack.model_validate(await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_share_pack',pack_approval_params(meeting,body),claims))

@router.get('/meetings/{meeting}/lifecycle')
async def lifecycle(meeting: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> MeetingStatus:
    async with preparation_errors(ctx,claims):return await meetings.status(meeting,ctx)

@router.get('/meetings/{meeting}/minutes/review')
async def read_minutes_review(meeting: UUID,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> SavedReview|None:
    async with preparation_errors(ctx,claims):
        result=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_minutes_review',{'p_meeting_id':meeting},claims)
        return None if result is None else SavedReview.model_validate(result)

@router.post('/meetings/{meeting}/minutes/review')
async def review_minutes(meeting: UUID,body: MinutesReview,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> MeetingStatus:
    async with preparation_errors(ctx,claims):return await meetings.decide(meeting,'minutes',Decision.model_validate(body.model_dump()),ctx)

@router.post('/meetings/{meeting}/minutes/regenerate')
async def regenerate_minutes(meeting: UUID,body: Approval,ctx: Annotated[RequestContext,Depends(get_ctx)],claims: Annotated[Claims,Depends(caller)]) -> Regeneration:
    from app.api.meetings import approval_params,generate_notes
    async with preparation_errors(ctx,claims):
        result=Regeneration.model_validate(await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_regenerate_minutes',approval_params(meeting,body),claims))
        if result.source_kind=='rm_notes':await generate_notes(meeting,Command(command_id=uuid5(body.command_id,'reviewed-notes-generation')),claims)
        return result
