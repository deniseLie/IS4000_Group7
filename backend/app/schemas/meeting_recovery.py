"""Exact reviewed recovery requests; neither callers nor workers can replace stored evidence."""
from typing import Literal
from pydantic import AwareDatetime,Field
from app.schemas.meeting_lifecycle import Command,Model,Text

RetryProblem=Literal['meeting_revision_changed','job_not_failed','job_type_not_retryable','call_not_ended',
 'transcript_permission_unavailable','authorization_refusal_requires_new_source','review_and_correct_call_times_first',
 'worker_claim_active','source_changed']

class TimeReview(Command):
    revision: int=Field(strict=True,ge=1)
    expected_start: AwareDatetime|None
    expected_end: AwareDatetime|None
    reason: Text=Field(max_length=300)

class TimeCorrection(TimeReview):
    started_at: AwareDatetime
    ended_at: AwareDatetime

class JobRetry(Command):
    revision: int=Field(strict=True,ge=1)
    reason: Text=Field(max_length=300)

class ProviderTimes(Model):
    started_at: AwareDatetime
    ended_at: AwareDatetime|None
    conflict: bool

class FailedJob(Model):
    event_id: int=Field(strict=True,gt=0)
    type: str
    error_code: str|None
    retry_problem: RetryProblem|None

class RecoveryView(Model):
    revision: int=Field(strict=True,ge=1)
    started_at: AwareDatetime|None
    ended_at: AwareDatetime|None
    evidence_saved: bool
    provider_times: ProviderTimes|None
    failed_jobs: tuple[FailedJob,...]
