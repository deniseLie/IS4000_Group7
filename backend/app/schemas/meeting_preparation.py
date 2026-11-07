"""Preparation plans contain only source IDs and vocabulary codes. Live source text is never persisted in a brief."""
from typing import Literal,TypeAlias
from datetime import date
from uuid import UUID
from pydantic import AwareDatetime,ConfigDict,Field,model_validator
from app.schemas.meeting_lifecycle import Hash,Model,Text
from app.schemas.meetings import BriefCard,BriefLabel,ConsentCategory,TalkingPoint

Heading=Literal['profile','compliance','requests','history','shared_topics','transaction_insights','questions']
Question=Literal['confirm_goal','review_status','clarify_next_step','discuss_shared_topic','check_information','compare_product_terms','confirm_document_next_step','review_collateral_next_step']

class HiddenInput(Model):
    model_config=ConfigDict(hide_input_in_errors=True)

class BriefSource(HiddenInput):
    source_id: UUID
    label: BriefLabel
    text: Text=Field(max_length=1000)
    reference: Text=Field(max_length=200)
    category: ConsentCategory|None
    grant_id: UUID|None

class PointReference(HiddenInput):
    source_id: UUID
    point: TalkingPoint
    reference: Text=Field(max_length=200)
    category: ConsentCategory|None
    grant_id: UUID|None

class BriefGap(HiddenInput):
    category: ConsentCategory
    text: Text=Field(max_length=1000)

class RemovedSource(HiddenInput):
    source_id: UUID
    removed_on: date|None
    reason: Literal['removed','access_ended']

class PreparationInputs(HiddenInput):
    meeting_id: UUID
    revision: int=Field(strict=True,gt=0)
    purpose: str|None
    topics: tuple[str,...]=Field(max_length=10)
    sources: tuple[BriefSource,...]=Field(max_length=128)
    talking_points: tuple[PointReference,...]=Field(max_length=50)
    gaps: tuple[BriefGap,...]
    removed_sources: tuple[RemovedSource,...]
    source_hash: Hash
    consent: dict[ConsentCategory,bool]
    grants_used: dict[ConsentCategory,UUID|None]
    as_of: AwareDatetime

class SourceReference(HiddenInput):
    source_id: UUID

class RecordReference(SourceReference):
    kind: Literal['record']
    question: None

class QuestionReference(SourceReference):
    kind: Literal['question']
    question: Question

BriefReference: TypeAlias=RecordReference|QuestionReference

class SelectedRecord(RecordReference):
    heading: Heading

class SelectedQuestion(QuestionReference):
    heading: Heading

class BriefSelection(HiddenInput):
    items: tuple[SelectedRecord|SelectedQuestion,...]=Field(min_length=1,max_length=30)

    @model_validator(mode='after')
    def unique_references(self) -> 'BriefSelection':
        references=[(item.source_id,item.kind) for item in self.items]
        if len(references)!=len(set(references)):raise ValueError('The generated brief repeats a record or question; each should appear once')
        return self

class PlannedSection(HiddenInput):
    heading: Heading
    items: tuple[BriefReference,...]=Field(min_length=1,max_length=30)

class BriefPlan(HiddenInput):
    sections: tuple[PlannedSection,...]=Field(min_length=1,max_length=7)

    @model_validator(mode='after')
    def bounded_unique(self) -> 'BriefPlan':
        refs=[(item.source_id,item.kind) for section in self.sections for item in section.items]
        if len(refs)>30:raise ValueError('The whole brief allows at most thirty reference items')
        if len(refs)!=len(set(refs)):raise ValueError('The brief repeats a record or clarification question across sections')
        return self

class SourceManifest(HiddenInput):
    source_id: UUID
    grant_id: UUID|None
    category: ConsentCategory|None
    point_id: UUID|None

class StoredBrief(HiddenInput):
    brief_id: UUID
    meeting_id: UUID
    version: int=Field(strict=True,gt=0)
    revision: int=Field(strict=True,gt=0)
    source_hash: Hash
    content_hash: Hash
    plan: BriefPlan
    source_manifest: tuple[SourceManifest,...]
    grants_used: dict[ConsentCategory,UUID|None]
    status: Literal['draft','approved','changes_requested','rejected']
    review_note: str|None
    approved_at: AwareDatetime|None
    created_at: AwareDatetime

class PreparedBrief(HiddenInput):
    card: BriefCard
    brief_id: UUID
    revision: int
    content_hash: Hash
    sources_changed: bool
    decision: Literal['draft','approved','changes_requested','rejected']
    review_note: str|None

class BriefGraphState(HiddenInput):
    meeting_id: UUID
    plan: BriefPlan|None
    brief_id: UUID|None

class BriefDecision(HiddenInput):
    version: int=Field(strict=True,gt=0)
    revision: int=Field(strict=True,gt=0)
    content_hash: Hash
    outcome: Literal['approve','changes','reject']
    note: str|None=Field(max_length=1000)
    command_id: UUID

    @model_validator(mode='after')
    def explain_changes(self) -> 'BriefDecision':
        if self.outcome=='changes' and (self.note is None or not self.note.strip()):raise ValueError('Explain the requested brief changes')
        return self
