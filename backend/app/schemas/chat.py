
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_last_message(self):
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        return self
