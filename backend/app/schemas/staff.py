"""Staff queue payloads. Staff identity and aal2 assurance are verified by the Data API and SQL boundary."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints
from app.schemas.contracts import Priority


class StaffHandoff(BaseModel):
    model_config=ConfigDict(extra='ignore')
    handoff_id: UUID
    customer_id: Annotated[StrictInt,Field(gt=0)]
    case_id: UUID
    reason: Literal['card_verification','card_dispute','general_support']
    priority: Priority
    summary: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=1000,pattern=r'\S')]
    status: Literal['open','claimed']
    claimed_by: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
