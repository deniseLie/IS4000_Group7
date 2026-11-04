"""Meeting lifecycle seam (migration 0105): the shared models between the client/consent/copilot side and the meeting
lifecycle side (services/meetings/). Change additively only and tell the other side; tests/test_meeting_contracts.py
round-trips an example of each model.

A meeting is an rm.appointments row: meeting_id is its appointment_id. Event payloads and brief sources carry ids, codes
and dates, never client text."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, JsonValue, model_validator
from app.schemas.contracts import Contract

MeetingStage = Literal['requested', 'scheduled', 'preparing', 'brief_review', 'brief_approved', 'awaiting_transcript',
    'minutes_review', 'done', 'cancelled', 'failed']
MeetingEventType = Literal['meeting.scheduled', 'meeting.rescheduled', 'meeting.cancelled', 'brief.requested', 'transcript.ready']  # = 0105 check
ConsentCategory = Literal['transaction_insights', 'chat_topics', 'meeting_recording']  # = rm.consent_active (0105)
BriefLabel = Literal['record', 'client_said', 'ai_suggestion', 'gap']
ApprovalStep = Literal['brief', 'minutes']
Waiting = Literal['rm', 'client', 'provider']
Source = str  # a record citation (tool call id, record id) or a transcript position such as 'transcript@754'


class MeetingRef(Contract):
    meeting_id: UUID
    customer_id: int
    advisor_id: UUID
    starts_at: datetime
    ends_at: datetime
    format: Literal['video', 'phone', 'in_person']
    purpose: str | None = Field(default=None, min_length=1, max_length=200)
    topics: list[str] = Field(default_factory=list, max_length=10)


class MeetingEvent(Contract):
    """A row of the private meetings.events queue. event_id and created_at are set by meetings.enqueue."""
    event_id: int | None = None
    type: MeetingEventType
    meeting_id: UUID
    payload: dict[str, JsonValue] = Field(default_factory=dict)  # ids, codes and dates only (at most 4 KB in SQL)
    dedupe_key: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: datetime | None = None


class BriefItem(Contract):
    label: BriefLabel
    text: str = Field(min_length=1, max_length=1000)
    sources: list[Source] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def cited(self):
        if self.label != 'gap' and not self.sources:
            raise ValueError('every brief item except a gap cites at least one source')
        return self


class BriefSection(Contract):
    heading: str = Field(min_length=1, max_length=120)
    items: list[BriefItem] = Field(max_length=30)


class BriefDocument(Contract):
    document_id: UUID
    kind: Literal['meeting_pack', 'term_sheet', 'product_comparison']
    title: str = Field(min_length=1, max_length=200)
    visibility: Literal['internal', 'client']
    status: Literal['draft', 'approved', 'shared']


class BriefCard(Contract):
    meeting_id: UUID
    version: int = Field(ge=1)
    status: Literal['draft', 'approved']
    sections: list[BriefSection] = Field(max_length=12)
    documents: list[BriefDocument] = Field(default_factory=list, max_length=5)
    consent: dict[ConsentCategory, bool] = Field(default_factory=dict)  # which gated categories the client shared at as_of
    withheld: int = Field(default=0, ge=0)  # items that failed checks: counted, never shown
    as_of: datetime


class ActionItem(Contract):
    text: str = Field(min_length=1, max_length=500)
    owner: Literal['rm', 'client']
    due: date | None = None
    quote: str = Field(min_length=1, max_length=500)  # the words in the transcript or notes that support the item
    at_seconds: int | None = Field(default=None, ge=0)  # transcript position; None for typed notes


class MinutesDraft(Contract):
    meeting_id: UUID
    version: int = Field(ge=1)
    status: Literal['draft', 'approved']
    source: Literal['transcript', 'rm_notes']
    summary: str = Field(min_length=1, max_length=4000)
    decisions: list[str] = Field(default_factory=list, max_length=20)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=20)
    client_recap: str | None = Field(default=None, max_length=4000)  # sent only after the RM approves it
    withheld: int = Field(default=0, ge=0)


class Decision(Contract):
    """The RM's answer at an approval pause (brief or minutes)."""
    outcome: Literal['approve', 'changes', 'reject']
    note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode='after')
    def changes_say_what(self):
        if self.outcome == 'changes' and not self.note:
            raise ValueError('asking for changes needs a note saying what to change')
        return self


class MeetingStatus(Contract):
    meeting_id: UUID
    stage: MeetingStage
    updated_at: datetime
    waiting_for: Waiting | None = None
    brief_version: int | None = Field(default=None, ge=1)
    minutes_version: int | None = Field(default=None, ge=1)
    error_code: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{0,39}$')  # e.g. provider_unavailable
