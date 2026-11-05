"""Closed request, evidence and approval models for the meeting lifecycle."""
from datetime import date
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class AppMetadata(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    demobank_role: Literal['rm', 'support'] | None = None
    rm_id: UUID | None = None


class Claims(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    sub: UUID
    role: Literal['authenticated']
    aud: Literal['authenticated']
    exp: int
    app_metadata: AppMetadata = Field(default_factory=AppMetadata)


class Start(Model):
    notice_id: UUID
    purpose: Text = Field(max_length=200)
    outcome: Text = Field(max_length=300)
    command_id: UUID


class Command(Model):
    command_id: UUID


class Grant(Model):
    notice_id: UUID
    revision: int = Field(strict=True, ge=1)
    expires_at: AwareDatetime
    command_id: UUID


class CallTime(Model):
    revision: int = Field(strict=True, ge=1)
    at: AwareDatetime
    command_id: UUID


class Cue(Model):
    cue_id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    speaker: Literal['rm', 'client', 'unmapped']
    start_seconds: int | None = Field(strict=True, ge=0)
    text: Text = Field(max_length=2000)


class Notes(Model):
    revision: int = Field(strict=True, ge=1)
    cues: tuple[Cue, ...] = Field(min_length=1, max_length=100)
    command_id: UUID


class EvidenceClaim(Model):
    text: Text = Field(max_length=500)
    cue_id: str
    quote: Text = Field(max_length=1000)


class ActionFields(EvidenceClaim):
    text: Text = Field(max_length=200)
    owner: Literal['rm', 'client', 'unassigned']
    due_on: date | None
    due_quote: Text | None


class ConditionalAction(ActionFields):
    commitment: Literal['conditional']
    condition: Text


class UnconditionalAction(ActionFields):
    commitment: Literal['agreed', 'tentative', 'unclear']
    condition: None


ProposedAction: TypeAlias = ConditionalAction | UnconditionalAction


class Action(ActionFields):
    commitment: Literal['agreed', 'conditional', 'tentative', 'unclear']
    condition: Text | None
    action_id: UUID

    @model_validator(mode='after')
    def condition_matches_commitment(self) -> 'Action':
        if (self.commitment == 'conditional') != (self.condition is not None):
            raise ValueError('A conditional action requires condition evidence; all other commitments require condition=null')
        return self


class MinutesProposal(Model):
    title: Text = Field(max_length=160)
    summary: tuple[EvidenceClaim, ...] = Field(min_length=1, max_length=4)
    decisions: tuple[EvidenceClaim, ...] = Field(max_length=10)
    actions: tuple[ProposedAction, ...] = Field(max_length=10)


class MinutesContent(Model):
    title: Text = Field(max_length=160)
    summary: tuple[EvidenceClaim, ...] = Field(min_length=1, max_length=4)
    decisions: tuple[EvidenceClaim, ...] = Field(max_length=10)
    actions: tuple[Action, ...] = Field(max_length=10)
    client_recap: Text = Field(max_length=500)


class DraftInput(Model):
    source_id: UUID
    source_hash: Hash
    revision: int = Field(strict=True, ge=1)
    consent_epoch: int = Field(strict=True, ge=1)
    content: MinutesContent
    command_id: UUID


class Approval(Model):
    draft_id: UUID
    version: int = Field(strict=True, ge=1)
    content_hash: Hash
    revision: int = Field(strict=True, ge=1)
    consent_epoch: int = Field(strict=True, ge=1)
    command_id: UUID


class SelectedActions(Approval):
    action_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10)

    @field_validator('action_ids')
    @classmethod
    def unique(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError('action_ids must be distinct')
        return value


class Completion(Model):
    evidence: Text = Field(max_length=500)
    command_id: UUID


class GenerationContext(Model):
    meeting_id: UUID
    revision: int = Field(ge=1)
    consent_epoch: int = Field(ge=1)
    source_id: UUID
    source_hash: Hash
    source_kind: Literal['transcript', 'rm_notes']
    cues: tuple[Cue, ...]


class NotesContext(GenerationContext):
    purpose: str
    outcome: str


class JobContext(GenerationContext):
    draft_id: UUID | None


class DraftReceipt(Model):
    draft_id: UUID
    version: int
    content_hash: Hash
    revision: int
    consent_epoch: int


class Claim(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    event_id: int
    event_type: Literal['meeting.scheduled', 'meeting.rescheduled', 'meeting.cancelled', 'brief.requested', 'meeting.started', 'meeting.ended', 'transcript.ready']
    meeting_id: UUID
    claim_token: UUID
    attempts: int = Field(ge=1, le=5)
    available_at: AwareDatetime


class GraphState(Model):
    meeting_id: UUID
    source_id: UUID
    draft_id: UUID | None
