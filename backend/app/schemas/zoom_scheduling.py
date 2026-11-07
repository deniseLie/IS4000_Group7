"""Runtime-only scheduling context. Meeting agenda text and provider IDs never enter the durable parent state."""
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime,ConfigDict,Field
from app.schemas.meeting_lifecycle import Model


class ProviderMeeting(Model):
    model_config=ConfigDict(extra='forbid',frozen=True,hide_input_in_errors=True)
    meeting_id: UUID
    provider_id: int|None=Field(strict=True,gt=0)
    account_id: str|None=Field(min_length=1,max_length=128)
    host_id: str|None=Field(min_length=1,max_length=128)
    original_uuid: str|None=Field(min_length=1,max_length=200)


class ScheduledMeeting(ProviderMeeting):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    purpose: str=Field(min_length=1,max_length=200)
    topics: tuple[str,...]=Field(max_length=10)
    linked_current: bool


Cancellation=Literal['deleted','already_absent']
