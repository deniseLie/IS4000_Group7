"""Michelle's answer to an open bank request on her review (0080). The bank decides; the answer never does."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,StringConstraints

Answer=Annotated[str,StringConstraints(strict=True,strip_whitespace=True,min_length=1,max_length=2000)]


class RequestAnswerTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    case_id: UUID
    request_id: UUID
    answer: Answer


class StoredRequestAnswerTarget(RequestAnswerTarget):
    question: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=500)]


class RequestAnswerResult(StoredRequestAnswerTarget):
    status: Literal['responded']
    responded_at: AwareDatetime
