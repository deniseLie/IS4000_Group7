"""Validated notification records and worker outcomes; delivery text comes only from stored templates."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints
from app.schemas.contracts import Priority

Text=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=1500)]


class Notification(BaseModel):
    model_config=ConfigDict(extra='ignore')
    notification_id: UUID
    customer_id: Annotated[StrictInt,Field(gt=0)]
    event_id: Annotated[StrictInt,Field(gt=0)] | None
    template_key: Annotated[str,StringConstraints(strict=True,pattern=r'^[a-z][a-z_.]{1,60}$')]
    template_version: Annotated[StrictInt,Field(gt=0)]
    locale: Annotated[str,StringConstraints(strict=True,min_length=2,max_length=20)]
    priority: Priority
    title: Text
    body: Text
    is_service: StrictBool
    read_at: AwareDatetime | None
    created_at: AwareDatetime


class Inbox(BaseModel):
    model_config=ConfigDict(extra='forbid')
    customer_id: Annotated[StrictInt,Field(gt=0)]
    items: list[Notification]=Field(max_length=50)


class ProcessResult(BaseModel):
    model_config=ConfigDict(extra='forbid')
    event_id: Annotated[StrictInt,Field(gt=0)]
    status: Literal['pending','done','failed']
    attempts: Annotated[StrictInt,Field(ge=0,le=5)]
    disposition: Literal['delivered','suppressed','deferred','stale','duplicate'] | None
    error_code: Annotated[str,StringConstraints(strict=True,pattern=r'^[A-Z0-9]{5}$')] | None
    notification_id: UUID | None
