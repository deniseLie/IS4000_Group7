"""Owned agenda drafts; selected profile values and consent categories are derived by the database."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,field_validator,model_validator

ProfileField=Literal['name','preferred_language','employment_status']
Topic=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=300,pattern=r'\S')]
AgendaText=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=6000,pattern=r'\S')]


class AgendaSaveTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    agenda_id: UUID
    advisor_id: UUID
    expected_revision: Annotated[StrictInt,Field(ge=0,le=2147483646)]
    topics: list[Topic]=Field(min_length=1,max_length=12)
    profile_fields: list[ProfileField]=Field(max_length=3)

    @field_validator('profile_fields')
    @classmethod
    def canonical_fields(cls,value: list[ProfileField]) -> list[ProfileField]:
        if len(set(value))!=len(value):raise ValueError('Selected profile fields must be unique')
        return sorted(value)


class AgendaProfile(BaseModel):
    model_config=ConfigDict(extra='ignore')
    name: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000)] | None
    preferred_language: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000)] | None
    employment_status: Literal['employed','self_employed','student','retired','unemployed'] | None


class StoredAgendaSaveTarget(AgendaSaveTarget):
    recipient_name: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'\S')]
    profile_snapshot: AgendaProfile
    content: AgendaText
    categories: list[Literal['agenda','profile']]=Field(min_length=1,max_length=2)

    @model_validator(mode='after')
    def selected_scope(self) -> Self:
        if {key for key,value in self.profile_snapshot.model_dump().items() if value is not None}!=set(self.profile_fields):
            raise ValueError('Agenda contains profile data outside its selected fields')
        if self.categories!=(['agenda','profile'] if self.profile_fields else ['agenda']):
            raise ValueError('Agenda consent categories disagree with the selected data')
        return self


class AgendaSaveResult(StoredAgendaSaveTarget):
    revision: Annotated[StrictInt,Field(ge=1)]
    status: Literal['draft']
    updated_at: AwareDatetime

    @model_validator(mode='after')
    def next_revision(self) -> Self:
        if self.revision!=self.expected_revision+1:raise ValueError('Saved agenda revision must follow the reviewed draft')
        return self


class AgendaRegistry(BaseModel):
    model_config=ConfigDict(extra='ignore')
    agendas: list[AgendaSaveResult]=Field(max_length=200)
    agendas_total: Annotated[StrictInt,Field(ge=0)]

    @model_validator(mode='after')
    def complete_count(self) -> Self:
        if len(self.agendas)!=min(self.agendas_total,200):raise ValueError('Agenda count disagrees with its bounded records')
        if len({row.agenda_id for row in self.agendas})!=len(self.agendas):raise ValueError('Agenda records must be unique')
        return self
