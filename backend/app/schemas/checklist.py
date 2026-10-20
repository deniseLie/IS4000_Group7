"""Customer preparation marks are separate from bank submission and review decisions."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,StringConstraints

PreparationMark=Literal['prepared','not_prepared']


class ChecklistTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    case_id: UUID
    item_id: UUID
    client_mark: PreparationMark
    expected_client_mark: PreparationMark
    expected_client_marked_at: AwareDatetime | None


class StoredChecklistTarget(ChecklistTarget):
    case_title: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000,pattern=r'\S')]
    item_label: Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000,pattern=r'\S')]
    case_status: Literal['review_opened','documents_requested','documents_partially_received','awaiting_client_documents','under_review','information_requested','completed','closed']
    submission_status: Literal['not_submitted','submitted_awaiting_review','accepted','rejected','unknown']


class ChecklistResult(StoredChecklistTarget):
    client_marked_at: AwareDatetime
