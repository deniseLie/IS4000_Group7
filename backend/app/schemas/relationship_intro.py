"""Workflow 3 relationship payloads: advisor introduction with approved change wording, joining details and handover drafts."""
from datetime import date
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,model_validator
from app.schemas.agendas import ProfileField,Topic
from app.schemas.appointments import AppointmentFormat
from app.schemas.customer_data import AdvisorContact,Text

Anchor=Literal['team-restructure','continuity','unrecorded','no-advisor']


class IntroAdvisor(BaseModel):
    model_config=ConfigDict(extra='ignore')
    rm_id: UUID
    name: Text
    role: Text
    team: Text
    contact: AdvisorContact
    started_on: date


class PreviousAdvisor(BaseModel):
    model_config=ConfigDict(extra='ignore')
    name: Text
    role: Text
    team: Text
    started_on: date
    ended_on: date


class NoticeSection(BaseModel):
    """Approved DemoBank wording; never generated or paraphrased by this service."""
    model_config=ConfigDict(extra='ignore')
    cite: Annotated[str,StringConstraints(strict=True,pattern=r'^kb:rm-change@v[1-9][0-9]*#[a-z0-9-]+$')]
    article_id: Literal['rm-change']
    version: Annotated[StrictInt,Field(ge=1)]
    anchor: Anchor
    title: Text
    heading: Text
    body: Text
    publisher: Text


class Introduction(BaseModel):
    model_config=ConfigDict(extra='ignore')
    current: IntroAdvisor | None
    previous: PreviousAdvisor | None
    notice: list[NoticeSection]=Field(max_length=2)

    @model_validator(mode='after')
    def approved_wording(self) -> Self:
        anchors=[item.anchor for item in self.notice]
        if self.current is None:expected_ok=anchors==['no-advisor']
        elif self.previous is None:expected_ok=anchors==[]
        else:expected_ok=len(anchors)==2 and anchors[0] in ('team-restructure','unrecorded') and anchors[1]=='continuity'
        if not expected_ok:raise ValueError('The advisor introduction does not carry its approved change wording')
        if self.current is not None and self.previous is not None and self.previous.ended_on>self.current.started_on:
            raise ValueError('The previous assignment ends after the current one starts')
        return self


class MeetingOption(BaseModel):
    model_config=ConfigDict(extra='ignore')
    advisor_id: UUID
    format: AppointmentFormat
    joining_details: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=500,pattern=r'\S')]


class DraftSource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tool: Literal['customer.get_profile','requests.list','rm.get_introduction']
    call_id: UUID
    as_of: AwareDatetime


class HandoverDraft(BaseModel):
    """Suggested note content from authorised records only; saving and sharing remain separate customer actions."""
    model_config=ConfigDict(extra='forbid')
    advisor_id: UUID
    recipient_name: Text
    profile_fields: list[ProfileField]=Field(max_length=3)
    topics: list[Topic]=Field(min_length=1,max_length=8)
    sources: list[DraftSource]=Field(min_length=1,max_length=3)
