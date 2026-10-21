
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, model_validator


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=61)
    mode: Literal['public','personal'] = 'public'
    language: Literal['English','Chinese','Malay','Tamil'] = 'English'
    conversation_id: UUID | None = None
    turn_id: UUID | None = None
    revision: int | None = Field(default=None,strict=True,ge=0,le=9007199254740991)

    @model_validator(mode="after")
    def validate_last_message(self):
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        fields=(self.conversation_id,self.turn_id,self.revision)
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError('Saved chats require conversation_id, turn_id and revision together')
        return self
