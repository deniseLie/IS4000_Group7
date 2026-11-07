"""Exact-version minutes review and durable lifecycle graph inputs."""
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime,Field
from app.schemas.meeting_lifecycle import Approval,Model,Text
from app.schemas.meetings import MeetingStatus

class MinutesReview(Approval):
    outcome: Literal['changes','reject']
    note: Text=Field(max_length=1000)

class SavedReview(Model):
    review_id: UUID
    draft_id: UUID
    meeting_id: UUID
    outcome: Literal['changes','reject']
    note: Text
    actor: UUID
    created_at: AwareDatetime

class Regeneration(Model):
    source_kind: Literal['transcript','rm_notes']
    event_id: int|None
    review_id: UUID

class LifecycleState(Model):
    meeting_id: UUID
    event_id: int
    status: MeetingStatus|None

class GraphRun(Model):
    meeting_id: UUID
    event_id: int=Field(strict=True,gt=0)
    run_id: UUID
