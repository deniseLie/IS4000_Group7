"""Notice-based preliminary checks (legacy reports remain readable). Findings never contain extracted document values."""
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,model_validator


class CheckPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    timezone: str='Asia/Singapore'
    validity_hours: int=Field(default=24,ge=1,le=168)
    status_stale_days: int=Field(default=7,ge=1,le=365)
    reminders_are_service: bool=True
    notification_fallback_locale: str='en'
    reminder_days: int=Field(default=7,ge=0,le=90)
    statement_period_mode: Literal['completed_months','rolling_months']='completed_months'
    date_formats: list[str]=Field(default_factory=lambda:['%Y-%m-%d','%Y/%m/%d','%d %B %Y','%d %b %Y','%B %d, %Y','%b %d, %Y','%B %d %Y','%b %d %Y','%d/%m/%Y','%m/%d/%Y','%Y-%m','%B %Y','%b %Y'],max_length=30)
    numeric_date_order: Literal['reject_ambiguous','day_first','month_first']='reject_ambiguous'


class NoticeRequirement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reference: str=Field(pattern=r'^[a-f0-9]{16}$')
    quote: str=Field(min_length=1,max_length=2000)
    kind: Literal['presence','date','period','page_count','unsupported']
    count: int=Field(default=0,ge=0,le=366)
    unit: Literal['none','days','months','years']='none'


class PrecheckRule(BaseModel):
    model_config=ConfigDict(extra='forbid')
    rule_id: str=Field(pattern=r'^[a-z_]{1,40}$')
    version: Literal[1]
    title: str
    fields: list[str]=Field(min_length=1,max_length=10)
    expected_pages: StrictInt=Field(ge=1,le=20)
    recent_date: bool=False
    scope: str


class Finding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code: Literal['missing_field','page_count','date_invalid','date_outdated','unassessable','rules_unavailable','provider_unavailable','missing_pages','translation_needed']
    field: str | None=None


class PrecheckReport(BaseModel):
    model_config=ConfigDict(extra='forbid')
    check_id: UUID
    document_id: UUID
    case_id: UUID
    item_id: UUID
    customer_id: StrictInt
    rule_id: str
    rule_version: Literal[1]
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['no_issues','issues_found','unable_to_assess']
    findings: list[Finding]=Field(max_length=12)
    page_count: StrictInt | None=Field(default=None,ge=1,le=20)
    extraction: Literal['text','ocr','unavailable']
    checked_at: AwareDatetime
    expires_at: AwareDatetime | None=None
    requirements: list[NoticeRequirement]=Field(default_factory=list,max_length=20)
    scope: Literal['synthetic_template_only','notice_based']='synthetic_template_only'
    requirements_hash: str | None=Field(default=None,pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def result_matches(self):
        if self.scope=='notice_based' and self.requirements_hash is None:raise ValueError('Notice checks require a requirements hash')
        if (self.status=='no_issues')!= (not self.findings):raise ValueError('Only complete checks may have no findings')
        return self


class SubmitDocumentTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    document_id: UUID
    check_id: UUID


class StoredSubmission(SubmitDocumentTarget):
    case_id: UUID
    item_id: UUID
    item_label: str
    file_name: str
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')


class SubmissionResult(StoredSubmission):
    status: Literal['submitted_awaiting_review']


class SupportSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    session_id: UUID
    case_id: UUID
    review_case_id: UUID
    item_id: UUID | None=None
    request_id: UUID | None=None
