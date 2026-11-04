"""Meeting lifecycle: public API (seam, migration 0105). Owned by the meeting side; the RM copilot, the /rm routes and the
meeting runner call only these four functions.

Until the meeting graph is built, and whenever MEETINGS_ENABLED is false, every function raises MeetingsUnavailable.
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


async def prepare(meeting_id: UUID, ctx: RequestContext) -> BriefCard:
    """Start or re-read the brief for this meeting as the signed-in RM; returns the current draft or approved brief."""
    _unavailable()


async def decide(meeting_id: UUID, step: ApprovalStep, decision: Decision, ctx: RequestContext) -> MeetingStatus:
    """The RM's approve, changes or reject at the brief or minutes pause; resumes the meeting's thread."""
    _unavailable()


async def status(meeting_id: UUID, ctx: RequestContext) -> MeetingStatus:
    """Where this meeting is and who it is waiting for."""
    _unavailable()
