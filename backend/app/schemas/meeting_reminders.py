"""Current RM's durable meeting deadlines; no client text is stored in timer payloads."""
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime,Field
from app.schemas.meeting_lifecycle import Model


class Reminder(Model):
    reminder_id: UUID
    meeting_id: UUID
    kind: Literal['preparation','missing_transcript']
    due_at: AwareDatetime
    client: str
    starts_at: AwareDatetime
    error_code: str|None=Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
