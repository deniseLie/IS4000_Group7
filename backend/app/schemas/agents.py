"""Validated per-turn agent inputs and outputs for the initial public v2 pipeline."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.contracts import AnswerKind, Domain
from app.schemas.events import Source


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    text: str
    publisher: str
    scope: str
    source: Source


class PlannedIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    domain: Domain
    question: str = Field(min_length=1, max_length=4000)


class PlannerDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    issues: list[PlannedIssue] = Field(min_length=1, max_length=5)


class AgentDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(min_length=1, max_length=4000)
    source_ids: list[str]
    answer_kind: AnswerKind
    status: Literal['ok', 'needs_clarification', 'needs_human']
    clarification_question: str

    @model_validator(mode='after')
    def _question(self):
        if self.status == 'needs_clarification':
            if not self.clarification_question.strip() or self.clarification_question != self.answer or self.answer_kind != 'conversation':
                raise ValueError('clarification must be a conversational answer containing the question')
        elif self.clarification_question:
            raise ValueError('question must be empty unless clarification is needed')
        return self
