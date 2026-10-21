"""Consent scope and active-grant results. Categories describe actual data selected by backend code."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StringConstraints,model_validator

ScopeCode=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z][a-z0-9_]{0,39}$')]


class ConsentQuery(BaseModel):
    model_config=ConfigDict(extra='ignore')
    purpose: ScopeCode
    recipient: UUID
    categories: list[ScopeCode] = Field(min_length=1,max_length=20)

    @model_validator(mode='after')
    def unique_categories(self) -> Self:
        if len(set(self.categories))!=len(self.categories):raise ValueError('Consent categories must be unique')
        return self


class ConsentResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    granted: StrictBool
    grant_id: UUID | None
    expires_at: AwareDatetime | None
    checked_at: AwareDatetime

    @model_validator(mode='after')
    def consistent_grant(self) -> Self:
        if self.granted and self.grant_id is None:raise ValueError('Granted consent requires a grant id')
        if not self.granted and (self.grant_id is not None or self.expires_at is not None):
            raise ValueError('Denied consent cannot name an active grant')
        if self.expires_at is not None and self.expires_at<=self.checked_at:
            raise ValueError('An active grant must expire after the check time')
        return self


class ConsentRecipient(BaseModel):
    model_config=ConfigDict(extra='ignore')
    rm_id: UUID
    name: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'\S')]


class RmAssignment(BaseModel):
    model_config=ConfigDict(extra='ignore')
    current: ConsentRecipient | None


class RelationshipConsent(BaseModel):
    model_config=ConfigDict(extra='ignore')
    purpose: Literal['rm_brief']
    categories: list[Literal['profile']] = Field(min_length=1,max_length=1)
    recipient: ConsentRecipient | None
    consent: ConsentResult | None

    @model_validator(mode='after')
    def consistent_recipient(self) -> Self:
        if (self.recipient is None)!=(self.consent is None):raise ValueError('Consent status requires an assigned recipient')
        return self
