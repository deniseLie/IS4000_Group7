"""SSE event payloads for POST /chat (docs/system-architecture.md §4). Additive to today's status, answer, done, error."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from app.schemas.contracts import AnswerKind, Domain, Priority

Origin = Literal['ai', 'safety', 'validation']


class StatusEvent(BaseModel):  # v2 pipeline; v1 still sends a plain status string
    stage: Literal['checking', 'planning', 'retrieving', 'validating']


class Source(BaseModel):  # same fields as services/knowledge.source()
    id: str
    title: str
    url: str
    version: str
    publisher: str | None = None
    reference_url: str | None = None
    checked_on: str | None = None
    review_due: str | None = None
    current: bool = True


class AnswerSection(BaseModel):  # one per task, priority order
    model_config = ConfigDict(extra='forbid')
    task_id: str
    domain: Domain
    priority: Priority
    answer_kind: AnswerKind = 'banking'
    answer: str
    sources: list[Source] = []
    needs_human: bool = False
    response_origin: Origin


class AnswerEvent(BaseModel):  # today's answer object + optional sections
    answer: str
    sources: list[Source] = []
    privacy_warning: bool = False
    history_trimmed: bool = False
    disclaimer: str
    needs_human: bool = False
    source_check: str
    prompt_version: str | None = None
    response_origin: Origin
    sections: list[AnswerSection] | None = None


class ActionRequiredEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_id: UUID
    type: str
    summary: str
    expires_at: datetime
