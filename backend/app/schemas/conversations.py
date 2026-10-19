"""Owned conversation sessions and redacted, validated completed turns."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,SecretStr,StrictBool,StrictInt,StringConstraints,model_validator
from app.schemas.contracts import Priority
from app.schemas.events import AnswerEvent

Mode=Literal['public','personal']
Language=Literal['English','Chinese','Malay','Tamil']
Revision=Annotated[StrictInt,Field(ge=0)]
GuestToken=Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Za-z0-9_-]{43}$')]


class CreateConversation(BaseModel):
    model_config=ConfigDict(extra='ignore')
    session_id: UUID
    mode: Mode
    language: Language


class ConversationAccess(BaseModel):
    model_config=ConfigDict(extra='ignore')
    session_id: UUID
    guest_token: SecretStr | None = Field(repr=False)


class ConversationSession(CreateConversation):
    customer_id: Annotated[StrictInt,Field(gt=0)] | None
    started_at: AwareDatetime
    last_active_at: AwareDatetime
    expires_at: AwareDatetime | None
    revision: Revision
    active_turn_id: UUID | None

    @model_validator(mode='after')
    def guest_expiry(self) -> Self:
        if self.customer_id is None and self.expires_at is None:raise ValueError('A guest conversation must expire')
        return self


class StoredError(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code: Literal['assistant_unavailable','cancelled','interrupted']
    message: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=500)]


class StoredFailure(BaseModel):
    model_config=ConfigDict(extra='forbid')
    error: StoredError


class StoredMessage(BaseModel):
    model_config=ConfigDict(extra='ignore')
    message_id: UUID
    role: Literal['user','assistant']
    redacted_text: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=20008)]
    created_at: AwareDatetime
    sequence: Annotated[StrictInt,Field(gt=0)]
    payload: AnswerEvent | StoredFailure | None

    @model_validator(mode='after')
    def consistent_answer(self) -> Self:
        if self.role=='user' and (self.payload is not None or len(self.redacted_text)>4000):
            raise ValueError('A stored user message must be bounded text without an answer payload')
        reply=self.payload.error.message if isinstance(self.payload,StoredFailure) else self.payload.answer if self.payload is not None else None
        if self.role=='assistant' and reply!=self.redacted_text:
            raise ValueError('A stored answer must match its validated payload')
        return self


class ConversationHistory(BaseModel):
    model_config=ConfigDict(extra='ignore')
    session: ConversationSession
    messages: list[StoredMessage]
    history_trimmed: StrictBool
    active_turn: 'TurnRecord | None'


class TurnRecord(BaseModel):
    model_config=ConfigDict(extra='ignore')
    turn_id: UUID
    session_id: UUID
    customer_id: Annotated[StrictInt,Field(gt=0)] | None
    case_id: UUID
    user_message_id: UUID
    answer_message_id: UUID | None
    state: Literal['processing','completed','failed']
    run_id: UUID
    revision: Revision
    created_at: AwareDatetime


class TurnResponse(BaseModel):
    model_config=ConfigDict(extra='ignore')
    turn: TurnRecord
    response: AnswerEvent | StoredFailure | None

    @model_validator(mode='after')
    def complete_response(self) -> Self:
        if (self.turn.state=='processing')!=(self.response is None):raise ValueError('A completed turn requires its stored response')
        if (self.turn.state=='processing')!=(self.turn.answer_message_id is None):raise ValueError('Only a completed turn can name an answer message')
        if self.turn.state=='completed' and not isinstance(self.response,AnswerEvent):raise ValueError('A completed turn requires an answer')
        if self.turn.state=='failed' and not isinstance(self.response,StoredFailure):raise ValueError('A failed turn requires an explicit error')
        return self


class TurnInput(BaseModel):
    model_config=ConfigDict(extra='ignore')
    turn_id: UUID
    revision: Revision
    user_text: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=4000)]
    priority: Priority
