"""Meeting lifecycle: the seam API for the RM copilot (migration 0105). Owned by the meeting side.

Minutes are built (0120-0124): api/meetings.py and worker.py call graph.py, database.py, transcripts.py and zoom.py
directly, not these functions. The Brief Writer is built (0118, brief.py): prepare, decide (the brief step) and status
work with the RM's own context; the RM workspace calls brief.py through /rm/meetings/* without needing MEETINGS_ENABLED.
on_event still raises: the restricted worker has no RM JWT, so it never prepares a brief (it rejects brief.requested).
All four raise MeetingsUnavailable while MEETINGS_ENABLED is false.
Callers report that plainly ("meeting preparation isn't available yet") and never substitute a brief or minutes.
prepare, decide and status run with the RM's own RequestContext (her JWT: rm.current_advisor and rm.in_book apply);
on_event runs in the meeting runner, which has no user JWT and must not read client records.
"""
from typing import NoReturn
from uuid import UUID
from app.core.config import settings
from app.schemas.contracts import RequestContext
from app.schemas.meetings import ApprovalStep, BriefCard, Decision, MeetingEvent, MeetingStatus


class MeetingsUnavailable(RuntimeError):
    code = 'meetings_unavailable'


def _unavailable() -> NoReturn:
    if not settings.meetings_enabled:raise MeetingsUnavailable('Meeting preparation is switched off (MEETINGS_ENABLED=false)')
    raise MeetingsUnavailable('Meeting preparation is not built yet')


async def on_event(event: MeetingEvent) -> None:
    """Advance the meeting's thread for one claimed meetings.events row (meeting.scheduled, transcript.ready, ...)."""
    _unavailable()


def _enabled() -> None:
    if not settings.meetings_enabled:raise MeetingsUnavailable('Meeting preparation is switched off (MEETINGS_ENABLED=false)')


async def prepare(meeting_id: UUID, ctx: RequestContext) -> BriefCard:
    """Start or re-read the brief for this meeting as the signed-in RM; returns the current draft or approved brief."""
    _enabled()
    from datetime import datetime, timezone
    from app.services.meetings import brief
    viewed = (await brief.prepare(meeting_id, ctx))['brief']
    return brief.card(meeting_id, viewed, datetime.now(timezone.utc))


async def decide(meeting_id: UUID, step: ApprovalStep, decision: Decision, ctx: RequestContext) -> MeetingStatus:
    """The RM's approve, changes or reject at the brief pause (minutes are approved through api/meetings.py)."""
    _enabled()
    if step != 'brief':raise MeetingsUnavailable('Minutes are approved in the meeting workspace, not through decide')
    from uuid import uuid4
    from app.services.meetings import brief
    current = (await brief.read(meeting_id, ctx))['brief']
    if current is None:raise MeetingsUnavailable('No brief is saved for this meeting yet')
    if decision.outcome == 'approve':await brief.approve(meeting_id, UUID(str(current['brief_id'])), current['version'], uuid4(), ctx)
    else:await brief.prepare(meeting_id, ctx, regenerate=True)  # changes or reject: a new draft version for review
    return await status(meeting_id, ctx)


async def status(meeting_id: UUID, ctx: RequestContext) -> MeetingStatus:
    """Where this meeting is and who it is waiting for (the brief part; minutes progress is in meeting_view)."""
    _enabled()
    from datetime import datetime, timezone
    from app.services.meetings import brief
    current = (await brief.read(meeting_id, ctx))['brief']
    stage = 'scheduled' if current is None else 'brief_approved' if current['brief_status'] == 'approved' else 'brief_review'
    return MeetingStatus(meeting_id=meeting_id, stage=stage, updated_at=datetime.now(timezone.utc),
        waiting_for=None if stage == 'brief_approved' else 'rm', brief_version=current['version'] if current else None)
