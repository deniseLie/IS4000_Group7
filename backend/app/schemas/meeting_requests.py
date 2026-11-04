"""Meeting requests (migration 0106; RM copilot build plan 2a). The client asks her relationship manager for a meeting with
a purpose, 1-5 topics and up to 3 preferred times; she withdraws an open request, or accepts or declines a time her RM
proposed. Each is a confirm-type action on the ledger (meeting.request, meeting.withdraw, meeting.counter_reply).
Topic codes are rm.meeting_topics(); the purpose is her own redacted words and never goes into meeting events."""
import re
from datetime import timezone
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StringConstraints,field_validator,model_validator

MeetingTopic=Literal['home_loan','investments','fixed_deposit','insurance','term_sheet','compliance_review','account_review',
    'card_review','other']  # = rm.meeting_topics() (0106)
TOPIC_LABEL: dict[str,str]={'home_loan':'Home loan','investments':'Investments','fixed_deposit':'Fixed deposit','insurance':'Insurance',
    'term_sheet':'Loan terms (term sheet)','compliance_review':'Compliance review','account_review':'Account review',
    'card_review':'Card review','other':'Something else'}
MeetingFormat=Literal['video','phone','in_person']
FORMAT_LABEL: dict[str,str]={'video':'video call','phone':'phone call','in_person':'in person'}
MeetingRequestStatus=Literal['requested','countered','scheduled','declined','withdrawn']
Purpose=Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=200)]
CREDENTIALS=re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+')


class MeetingRequestTarget(BaseModel):
    """What she asks for. SQL checks her current RM, the redacted purpose and every time (rm.slot_problem) again."""
    model_config=ConfigDict(extra='ignore')
    advisor_id: UUID
    purpose: Purpose
    topics: list[MeetingTopic]=Field(min_length=1,max_length=5)
    format: MeetingFormat
    preferred_times: list[AwareDatetime]=Field(min_length=1,max_length=3)

    @field_validator('purpose')
    @classmethod
    def no_credentials(cls,value: str) -> str:
        if CREDENTIALS.search(value):raise ValueError('Remove credentials from the meeting purpose')
        return value

    @model_validator(mode='after')
    def canonical(self) -> Self:
        """The order SQL stores (actions.normalise_meeting_request): topics and times sorted once each, times in UTC."""
        self.topics=sorted(set(self.topics))
        self.preferred_times=sorted({t.astimezone(timezone.utc) for t in self.preferred_times})
        return self


StoredMeetingRequestTarget=MeetingRequestTarget


class MeetingRequestResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    request_id: UUID
    status: Literal['requested']
    respond_by: AwareDatetime
    respond_by_text: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=60)]


class MeetingRefTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    request_id: UUID


class StoredMeetingRefTarget(MeetingRefTarget):
    advisor_id: UUID


class CounterReplyTarget(MeetingRefTarget):
    reply: Literal['accept','decline']


class StoredCounterReplyTarget(CounterReplyTarget):
    advisor_id: UUID


class MeetingWithdrawResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    request_id: UUID
    status: Literal['withdrawn']


class CounterReplyResult(BaseModel):
    """Accepting confirms the held time (appointment_id, starts_at, when); declining closes the request as withdrawn."""
    model_config=ConfigDict(extra='ignore')
    request_id: UUID
    status: Literal['scheduled','withdrawn']
    reply: Literal['accept','decline']
    appointment_id: UUID | None=None
    starts_at: AwareDatetime | None=None
    when: str | None=None

    @model_validator(mode='after')
    def consistent(self) -> Self:
        if (self.reply=='accept')!=(self.status=='scheduled') or (self.reply=='accept')!=(self.appointment_id is not None and self.starts_at is not None):
            raise ValueError('An accepted time confirms its meeting; a declined one closes the request')
        return self


class MeetingRequestRecord(BaseModel):
    """One of her own requests (customer_read_meeting_requests, 0106). A countered request's proposed time is the held
    appointment named by appointment_id (her appointments read shows its time and how long it is held)."""
    model_config=ConfigDict(extra='ignore')
    request_id: UUID
    purpose: Purpose
    topics: list[MeetingTopic]=Field(min_length=1,max_length=5)
    format: MeetingFormat
    status: MeetingRequestStatus
    respond_by: AwareDatetime
    preferred_times: list[AwareDatetime]=Field(min_length=1,max_length=3)
    decline_reason: Literal['answered_by_message','redirected','not_needed'] | None
    appointment_id: UUID | None
    created_at: AwareDatetime
