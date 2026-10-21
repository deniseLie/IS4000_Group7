"""Reviewed consent scopes and receipt snapshots; a grant never performs data sharing itself."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints,field_validator,model_validator
from app.schemas.consent import ScopeCode

RecipientName=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'\S')]
ConsentCategory=Literal['profile','agenda','accounts','transactions','cards','loans','compliance']


class ConsentScope(BaseModel):
    model_config=ConfigDict(extra='ignore')
    purpose: ScopeCode
    recipient: UUID
    categories: list[ScopeCode]=Field(min_length=1,max_length=20)

    @field_validator('categories')
    @classmethod
    def canonical_categories(cls,value: list[str]) -> list[str]:
        if len(set(value))!=len(value):raise ValueError('Consent categories must be unique')
        return sorted(value)


class ConsentRequestTarget(ConsentScope):
    purpose: Literal['rm_brief']
    categories: list[ConsentCategory]=Field(min_length=1,max_length=7)
    duration_days: Annotated[StrictInt,Field(ge=1,le=365)]


class StoredConsentRequestTarget(ConsentRequestTarget):
    recipient_name: RecipientName


class ConsentGrantTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    request_id: UUID


class StoredConsentGrantTarget(ConsentScope,ConsentGrantTarget):
    recipient_name: RecipientName | None
    expires_at: AwareDatetime


class ConsentRevokeTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    grant_id: UUID


class StoredConsentRevokeTarget(ConsentScope,ConsentRevokeTarget):
    recipient_name: RecipientName | None
    expires_at: AwareDatetime | None


class ConsentRequestResult(StoredConsentRequestTarget):
    request_id: UUID
    expires_at: AwareDatetime
    status: Literal['pending']


class ConsentGrantResult(StoredConsentGrantTarget):
    grant_id: UUID
    status: Literal['active']


class ConsentRevokeResult(StoredConsentRevokeTarget):
    revoked_at: AwareDatetime
    status: Literal['revoked']


class ConsentRequestRecord(ConsentScope,ConsentGrantTarget):
    recipient_name: RecipientName | None
    grant_expires_at: AwareDatetime | None
    status: Literal['pending','granted','declined','expired']
    case_id: UUID | None


class ConsentGrantRecord(StoredConsentRevokeTarget):
    request_id: UUID | None
    granted_at: AwareDatetime
    revoked_at: AwareDatetime | None
    active: StrictBool


class ConsentRegistry(BaseModel):
    model_config=ConfigDict(extra='ignore')
    requests: list[ConsentRequestRecord]=Field(max_length=200)
    grants: list[ConsentGrantRecord]=Field(max_length=200)
    requests_total: Annotated[StrictInt,Field(ge=0)]
    grants_total: Annotated[StrictInt,Field(ge=0)]

    @model_validator(mode='after')
    def complete_counts(self) -> Self:
        if len(self.requests)!=min(self.requests_total,200) or len(self.grants)!=min(self.grants_total,200):
            raise ValueError('Consent snapshot counts disagree with their bounded records')
        return self
