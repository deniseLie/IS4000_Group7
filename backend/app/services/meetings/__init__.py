"""Public meeting seam: caller-scoped preparation and decisions; fenced events use a separate restricted runner.

SQL owns durable versions, approvals, waits and effects. The three model graphs receive disjoint allowlisted inputs.
The parent graph routes queue events and resumes persisted chunks; it cannot read client banking records.
"""
import asyncio
from uuid import UUID
from app.core.config import settings
from app.schemas.contracts import RequestContext
from app.schemas.meeting_lifecycle import Claim
from app.schemas.meetings import ApprovalStep,BriefCard,Decision,MeetingEvent,MeetingStatus
from app.services.meetings.database import ApplicationDatabase,Parameter,WorkerDatabase


class MeetingsUnavailable(RuntimeError):
    code = 'meetings_unavailable'


def require_enabled() -> None:
    if not settings.meetings_enabled:raise MeetingsUnavailable('Meeting preparation is switched off (MEETINGS_ENABLED=false)')


async def on_event(event: MeetingEvent) -> None:
    """Resume only a live claimed queue row. A plain webhook or a caller's invented event is insufficient."""
    require_enabled()
    if event.event_id is None or event.claim_token is None or event.attempts is None or event.available_at is None:
        raise ValueError('Meeting events require event_id, claim_token, attempts and available_at from a fenced queue claim')
    if settings.meetings_database_url is None:raise MeetingsUnavailable('Configure the restricted MEETINGS_DATABASE_URL for meeting events')
    from app.services.meetings.lifecycle import dispatch
    database=WorkerDatabase(settings.meetings_database_url)
    await asyncio.to_thread(database.verify_identity)
    await dispatch(database,Claim(event_id=event.event_id,event_type=event.type,meeting_id=event.meeting_id,
        claim_token=event.claim_token,attempts=event.attempts,available_at=event.available_at))


async def prepare(meeting_id: UUID, ctx: RequestContext) -> BriefCard:
    """Prepare or re-read a currently scoped brief under the assigned RM's own JWT."""
    require_enabled()
    from app.services.meetings import preparation
    return (await preparation.prepare(meeting_id,ctx)).card


async def decide(meeting_id: UUID, step: ApprovalStep, decision: Decision, ctx: RequestContext) -> MeetingStatus:
    """Approve or return one exact reviewed version. Never infer a missing version from the latest document."""
    require_enabled()
    from app.services.meetings.preparation import caller
    from app.services.privacy import redact
    if decision.version is None or decision.revision is None or decision.content_hash is None or decision.command_id is None:
        raise ValueError('Meeting decisions require the reviewed version, revision, content_hash and a command_id')
    params: dict[str,Parameter]={'p_meeting_id':meeting_id,'p_version':decision.version,'p_revision':decision.revision,
        'p_content_hash':decision.content_hash,'p_command_id':decision.command_id}
    if step=='brief':
        name='meeting_decide_brief';params.update(p_outcome=decision.outcome,p_note=redact(decision.note)[0] if decision.note else None)
    elif step=='minutes':
        if decision.draft_id is None or decision.consent_epoch is None:raise ValueError('Minutes decisions also require draft_id and consent_epoch')
        params.update(p_draft_id=decision.draft_id,p_consent_epoch=decision.consent_epoch)
        name='meeting_approve_minutes'
        if decision.outcome!='approve':
            if decision.note is None:raise ValueError('Explain the requested minutes changes or rejection')
            name='meeting_review_minutes';params.update(p_outcome=decision.outcome,p_note=redact(decision.note)[0])
    else:raise ValueError('Meeting approval step must be brief or minutes')
    await asyncio.to_thread(ApplicationDatabase(ctx).call,name,params,caller(ctx))
    return await status(meeting_id,ctx)


async def status(meeting_id: UUID, ctx: RequestContext) -> MeetingStatus:
    """Return the durable current lifecycle stage and participant it is waiting for."""
    require_enabled()
    from app.services.meetings.preparation import caller
    result=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_lifecycle_status',{'p_meeting_id':meeting_id},caller(ctx))
    return MeetingStatus.model_validate(result)
