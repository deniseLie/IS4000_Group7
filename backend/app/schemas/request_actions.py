"""Explicit customer intake for the currently assigned synthetic relationship manager."""
from datetime import date
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,StringConstraints

RequestText=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=1000,pattern=r'\S')]


class RequestTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    advisor_id: UUID
    what: RequestText
    urgency: Literal['low','normal','high']
    deadline: date | None
    country: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{2}$')] | None
    currency: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z]{3}$')] | None


class StoredRequestTarget(RequestTarget):
    who: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000,pattern=r'\S')]
    recipient_name: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'\S')]


class RequestResult(StoredRequestTarget):
    request_id: UUID
    status: Literal['sent']
    created_at: AwareDatetime
