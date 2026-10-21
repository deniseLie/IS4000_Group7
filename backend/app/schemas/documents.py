"""Owned document metadata; a record or preliminary check does not establish bank acceptance or object availability."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,model_validator
from app.schemas.customer_data import Text
from app.schemas.document_uploads import DocumentMime

DocumentText=Annotated[Text,StringConstraints(pattern=r'\S')]


class DocumentRecord(BaseModel):
    model_config=ConfigDict(extra='ignore')
    document_id: UUID
    case_id: UUID
    case_title: DocumentText
    item_id: UUID | None
    item_label: DocumentText | None
    file_name: DocumentText
    mime_type: DocumentMime
    size_bytes: StrictInt=Field(ge=1,le=10485760)
    precheck_status: Literal['not_checked','pending','passed','issues_found']
    uploaded_at: AwareDatetime

    @model_validator(mode='after')
    def matching_item(self) -> Self:
        if (self.item_id is None)!=(self.item_label is None):raise ValueError('Document item id and label must appear together')
        return self
