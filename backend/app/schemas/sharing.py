"""Reviewable sharing of an owned saved agenda under one exact consent grant."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,model_validator
from app.schemas.agendas import AgendaText,ProfileField
from app.schemas.consent_actions import ConsentCategory


class AgendaShareTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    agenda_id: UUID
    agenda_revision: Annotated[StrictInt,Field(ge=1,le=2147483647)]
    advisor_id: UUID
    consent_grant_id: UUID


class StoredAgendaShareTarget(AgendaShareTarget):
    agenda_command_id: UUID
    recipient_name: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'\S')]
    content: AgendaText
    profile_fields: list[ProfileField]=Field(max_length=3)
    categories: list[Literal['agenda','profile']]=Field(min_length=1,max_length=2)
    consent_categories: list[ConsentCategory]=Field(min_length=1,max_length=7)
    grant_expires_at: AwareDatetime | None

    @model_validator(mode='after')
    def canonical_scope(self) -> Self:
        for values in (self.profile_fields,self.consent_categories):
            if values!=sorted(set(values)):raise ValueError('Reviewed sharing selections must be unique and sorted')
        if self.categories!=(['agenda','profile'] if self.profile_fields else ['agenda']):
            raise ValueError('Sharing categories must match the saved profile selections')
        return self


class AgendaShareResult(StoredAgendaShareTarget):
    summary_id: UUID
    shared_at: AwareDatetime
    status: Literal['shared']
