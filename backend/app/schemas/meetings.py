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
    'minutes_review', 'following_up', 'done', 'cancelled', 'failed']
MeetingEventType = Literal['meeting.scheduled', 'meeting.rescheduled', 'meeting.cancelled', 'brief.requested',
    'meeting.started', 'meeting.ended', 'transcript.ready', 'transcript.fetch', 'transcript.discover', 'lifecycle.changed']
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


# Talking points (0106/0107 rm_talking_points; build plan phase 4 fills them). Only list values, dates and ids cross to the
# RM side: no field holds client text, and Contract forbids extra fields, so the client's words cannot travel in one.
TalkingTopic = Literal['home_loan', 'investments', 'fixed_deposit', 'insurance']  # the product codes of rm.meeting_topics() (0106)
TalkingStage = Literal['curious', 'exploring', 'planning', 'ready', 'concern']
Timeframe = Literal['now', 'within_3m', 'within_12m', 'later', 'unknown']
# 0143 (lane 1 v2) adds lock_in, down_payment, tenure and risk; the first four are 0108's.
ConcernTag = Literal['rates', 'fees', 'eligibility', 'timing', 'lock_in', 'down_payment', 'tenure', 'risk']
# 0143: what kind of each topic she means (= insights.kinds); null when she has not said. No health cover: a sensitive area.
TalkingKind = Literal['hdb_bto', 'hdb_resale', 'private_property', 'landed', 'refinance', 'etf', 'unit_trust', 'stocks', 'bonds', 'robo',
    'new_deposit', 'renewal', 'life', 'home', 'travel']
TalkingOrigin = Literal['inferred', 'confirmed']  # inferred from chat, or shared by the client herself
TOPIC_NAME = {'home_loan': 'Property financing', 'investments': 'Investments', 'fixed_deposit': 'Time deposit', 'insurance': 'Insurance'}  # labels since 0117 (codes unchanged)
KINDS = {'home_loan': ('hdb_bto', 'hdb_resale', 'private_property', 'landed', 'refinance'),
    'investments': ('etf', 'unit_trust', 'stocks', 'bonds', 'robo'), 'fixed_deposit': ('new_deposit', 'renewal'), 'insurance': ('life', 'home', 'travel')}
KIND_TEXT = {'hdb_bto': 'new launch property', 'hdb_resale': 'resale property', 'private_property': 'private property', 'landed': 'landed property', 'refinance': 'refinancing',
    'etf': 'ETFs', 'unit_trust': 'unit trusts', 'stocks': 'stocks', 'bonds': 'bonds', 'robo': 'managed portfolio', 'new_deposit': 'new deposit',
    'renewal': 'renewal', 'life': 'life', 'home': 'home', 'travel': 'travel'}
CONCERN_TEXT = {'rates': 'rates', 'fees': 'fees', 'eligibility': 'eligibility', 'timing': 'timing', 'lock_in': 'lock-in',
    'down_payment': 'down payment', 'tenure': 'tenure', 'risk': 'risk'}
TIMEFRAME_TEXT = {'now': 'now', 'within_3m': 'within 3 months', 'within_12m': 'within 12 months', 'later': 'later'}
ORIGIN_TEXT = {'inferred': 'inferred from chat', 'confirmed': 'shared by the client'}


class TalkingPoint(Contract):
    point_id: UUID
    topic: TalkingTopic
    stage: TalkingStage
    timeframe: Timeframe = 'unknown'
    kind: TalkingKind | None = None  # 0143, additive: a point without one renders exactly as before
    concerns: list[ConcernTag] = Field(default_factory=list, max_length=4)
    origin: TalkingOrigin
    first_seen: date
    last_seen: date

    @model_validator(mode='after')
    def consistent(self):
        if len(set(self.concerns)) != len(self.concerns):
            raise ValueError('each concern once')
        if self.kind is not None and self.kind not in KINDS[self.topic]:
            raise ValueError('the kind is not one of this topic')
        if self.last_seen < self.first_seen:
            raise ValueError('last_seen is before first_seen')
        return self

    def line(self) -> str:
        """The one rendering every reader uses (RM copilot, meeting brief, the client's Data sharing page):
        'Property financing · planning · within 12 months · inferred from chat', with the kind after the stage when she
        named one ('Property financing · planning · resale property · within 12 months · asks about lock-in, rates · inferred from chat')."""
        parts = [TOPIC_NAME[self.topic], self.stage]
        if self.kind is not None:
            parts.append(KIND_TEXT[self.kind])
        if self.timeframe != 'unknown':
            parts.append(TIMEFRAME_TEXT[self.timeframe])
        if self.concerns:
            parts.append('asks about ' + ', '.join(CONCERN_TEXT[c] for c in self.concerns))
        return ' · '.join([*parts, ORIGIN_TEXT[self.origin]])

    def brief_label(self) -> BriefLabel:
        return 'ai_suggestion' if self.origin == 'inferred' else 'client_said'


class MeetingEvent(Contract):
    """A row of the private meetings.events queue. event_id and created_at are set by meetings.enqueue."""
    event_id: int | None = None
    type: MeetingEventType
    meeting_id: UUID
    payload: dict[str, JsonValue] = Field(default_factory=dict)  # ids, codes and dates only (at most 4 KB in SQL)
    dedupe_key: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: datetime | None = None
    claim_token: UUID | None = None
    attempts: int | None = Field(default=None,ge=1,le=5)
    available_at: datetime | None = None


class BriefItem(Contract):
    label: BriefLabel
    text: str = Field(min_length=1, max_length=1000)
    sources: list[Source] = Field(default_factory=list, max_length=10)
    category: ConsentCategory | None = None

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
    client_recap: str | None = Field(default=None, max_length=5000)  # sent only after the RM approves it
    withheld: int = Field(default=0, ge=0)


class Decision(Contract):
    """The RM's answer at an approval pause (brief or minutes)."""
    outcome: Literal['approve', 'changes', 'reject']
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    version: int | None = Field(default=None,ge=1)
    revision: int | None = Field(default=None,ge=1)
    content_hash: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    command_id: UUID | None = None
    draft_id: UUID | None = None
    consent_epoch: int | None = Field(default=None,ge=1)

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
