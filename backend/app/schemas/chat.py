
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class RecordSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['document','information_request']
    case_id: UUID
    item_id: UUID | None=None
    document_id: UUID | None=None
    check_id: UUID | None=None
    request_id: UUID | None=None

    @model_validator(mode='after')
    def selected_record(self):
        if self.kind=='document' and (None in (self.item_id,self.document_id,self.check_id) or self.request_id is not None):raise ValueError('Select one document check')
        if self.kind=='information_request' and (self.request_id is None or any(x is not None for x in (self.item_id,self.document_id,self.check_id))):raise ValueError('Select one information request')
        return self


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=61)
    mode: Literal['public','personal'] = 'public'
    language: Literal['English','Chinese','Malay','Tamil'] = 'English'
    record_context: RecordSelection | None=None
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    revision: int | None = Field(default=None,strict=True,ge=0,le=9007199254740991)

    @model_validator(mode="after")
    def validate_last_message(self):
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        if self.record_context is not None and self.mode!='personal':raise ValueError('Record selection requires personal mode')
        fields=(self.conversation_id,self.turn_id,self.revision)
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError('Saved chats require conversation_id, turn_id and revision together')
        return self
