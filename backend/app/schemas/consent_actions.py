"""Reviewed consent scopes and receipt snapshots; a grant never performs data sharing itself.

Consent v2 (migration 0109): a grant of one copilot category to her current RM with a scope and an end, confirmed by
her (ConsentGrantV2Target). The 0037 request-based grant ({request_id}) still works."""
from datetime import date
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


# Consent v2 (0109) ------------------------------------------------------------------------------------------------
CopilotCategory=Literal['transaction_insights','chat_topics']
TOPIC_AREAS=('home_property','investments','deposits','insurance','cards','everyday_banking')
TopicArea=Literal['home_property','investments','deposits','insurance','cards','everyday_banking']
NoticeVersion=Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z0-9.-]{1,20}$')]


class TransactionShare(BaseModel):
    """transaction_insights: "all" accounts (cards too) or 1-20 of her account ids, and a 3, 6 or 12 month look-back."""
    model_config=ConfigDict(extra='forbid')
    accounts: Literal['all'] | Annotated[list[Annotated[StrictInt,Field(gt=0)]],Field(min_length=1,max_length=20)]
    lookback_months: Literal[3,6,12]

    @field_validator('accounts')
    @classmethod
    def canonical_accounts(cls,value: object) -> object:
        return sorted(set(value)) if isinstance(value,list) else value


class TopicShare(BaseModel):
    """chat_topics: the topic areas inferred topics may come from (health, family and legal are never a choice)."""
    model_config=ConfigDict(extra='forbid')
    topic_areas: list[TopicArea]=Field(min_length=1,max_length=6)

    @field_validator('topic_areas')
    @classmethod
    def canonical_areas(cls,value: list[str]) -> list[str]:
        return sorted(set(value),key=TOPIC_AREAS.index)


class ConsentGrantV2Target(BaseModel):
    """until: date (expires_on, tomorrow up to 12 months), meeting (her upcoming confirmed meeting) or request (chained
    after the meeting.request it waits for; the offer carries after_receipt)."""
    model_config=ConfigDict(extra='ignore')
    category: CopilotCategory
    recipient: UUID
    scope: TransactionShare | TopicShare
    notice_version: NoticeVersion
    until: Literal['date','meeting','request']
    expires_on: date | None=None
    meeting_id: UUID | None=None

    @model_validator(mode='after')
    def consistent(self) -> Self:
        if isinstance(self.scope,TransactionShare)!=(self.category=='transaction_insights'):raise ValueError('The scope does not fit the category')
        if (self.until=='date')!=(self.expires_on is not None) or (self.until=='meeting')!=(self.meeting_id is not None):
            raise ValueError('A dated grant needs expires_on and a meeting grant needs meeting_id, and nothing else')
        return self


class StoredConsentGrantV2Target(ConsentGrantV2Target):
    purpose: Literal['rm_brief']
    categories: list[CopilotCategory]=Field(min_length=1,max_length=1)
    category_label: RecipientName
    recipient_name: RecipientName
    replaces: UUID | None
    expires_at: AwareDatetime | None
    meeting_starts_at: AwareDatetime | None


class ConsentGrantV2Result(StoredConsentGrantV2Target):
    grant_id: UUID
    status: Literal['active']
    active_until: AwareDatetime | None
    until_request_id: UUID | None


def grant_target(value: object) -> 'ConsentGrantTarget | ConsentGrantV2Target':
    return ConsentGrantTarget.model_validate(value) if isinstance(value,dict) and 'request_id' in value else ConsentGrantV2Target.model_validate(value)


def stored_grant_target(value: object) -> 'StoredConsentGrantTarget | StoredConsentGrantV2Target':
    if isinstance(value,dict) and 'request_id' in value:return StoredConsentGrantTarget.model_validate(value)
    return StoredConsentGrantV2Target.model_validate(value)


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
