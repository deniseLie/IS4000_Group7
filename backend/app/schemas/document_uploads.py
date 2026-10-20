"""User-selected file descriptors and recorded upload results; no file contents belong in ledger targets."""
from pathlib import PurePath
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,model_validator

DocumentMime=Literal['application/pdf','image/jpeg','image/png','application/vnd.openxmlformats-officedocument.wordprocessingml.document']
EXTENSIONS: dict[DocumentMime,tuple[str,...]]={'application/pdf':('.pdf',),'image/jpeg':('.jpg','.jpeg'),
    'image/png':('.png',),'application/vnd.openxmlformats-officedocument.wordprocessingml.document':('.docx',)}
FileName=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=200,pattern=r'^[^/\\\x00-\x1f\x7f]+$')]
DocumentPath=Annotated[str,StringConstraints(strict=True,pattern=r'^[1-9][0-9]*/[0-9a-f-]{36}/[0-9a-f-]{36}$')]
Label=Annotated[str,StringConstraints(strict=True,min_length=1,max_length=2000,pattern=r'\S')]


class DocumentUploadTarget(BaseModel):
    model_config=ConfigDict(extra='ignore')
    document_id: UUID
    case_id: UUID
    item_id: UUID | None
    file_name: FileName
    mime_type: DocumentMime
    size_bytes: StrictInt=Field(ge=1,le=10485760)

    @model_validator(mode='after')
    def file_descriptor(self) -> Self:
        if self.file_name!=self.file_name.strip(' ') or PurePath(self.file_name).suffix.lower() not in EXTENSIONS[self.mime_type]:
            raise ValueError('Use a trimmed filename whose extension matches its declared PDF, DOCX, JPEG or PNG type')
        return self


class DocumentUploadContext(BaseModel):
    model_config=ConfigDict(extra='ignore')
    case_id: UUID
    case_title: Label
    item_id: UUID | None
    item_label: Label | None

    @model_validator(mode='after')
    def matching_item(self) -> Self:
        if (self.item_id is None)!=(self.item_label is None):raise ValueError('Upload item id and label must appear together')
        return self


class PreparedDocumentUpload(DocumentUploadTarget):
    storage_path: DocumentPath
    bucket: Literal['demobank-compliance-private']


class StoredDocumentUploadTarget(DocumentUploadTarget):
    case_title: Label
    item_label: Label | None
    storage_path: DocumentPath
    object_id: UUID
    uploaded_at: AwareDatetime

    @model_validator(mode='after')
    def matching_references(self) -> Self:
        parts=self.storage_path.split('/')
        if parts[1]!=str(self.case_id) or parts[2]!=str(self.document_id):raise ValueError('Upload path must identify its case and document')
        if (self.item_id is None)!=(self.item_label is None):raise ValueError('Upload item id and label must appear together')
        return self


class DocumentUploadResult(StoredDocumentUploadTarget):
    status: Literal['uploaded']


class DocumentLocation(BaseModel):
    model_config=ConfigDict(extra='ignore')
    document_id: UUID
    case_id: UUID
    storage_path: DocumentPath
    file_name: Label
    bucket: Literal['demobank-compliance-private']

    @model_validator(mode='after')
    def matching_path(self) -> Self:
        if self.storage_path.split('/')[1:]!=[str(self.case_id),str(self.document_id)]:
            raise ValueError('Document location must identify its case and document')
        return self
