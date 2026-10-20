"""Technical inspection contracts. Extracted text stays in memory and never enters the API response or audit."""
from typing import Annotated,Literal,Self
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictInt,StringConstraints,model_validator
from app.schemas.document_uploads import DocumentLocation,DocumentMime,Label

InspectionStatus=Literal['text_extracted','no_extractable_text','image_decoded','encrypted','invalid_file','limits_exceeded','unsupported_content']
InspectionIssue=Literal['no_text_layer','ocr_unavailable','encrypted_file','invalid_structure','format_mismatch','file_limits','unsupported_content']
Count=Annotated[StrictInt,Field(ge=0,le=32000)]
Dimension=Annotated[StrictInt,Field(ge=1,le=16000000)]
PageCount=Annotated[StrictInt,Field(ge=1,le=20)]


def validate_facts(status: InspectionStatus,issue: InspectionIssue | None,page_count: int | None,width: int | None,height: int | None,characters: int) -> None:
    expected: dict[InspectionStatus,tuple[InspectionIssue | None,...]]={'text_extracted':(None,),'no_extractable_text':('no_text_layer',),
        'image_decoded':('ocr_unavailable',),'encrypted':('encrypted_file',),'invalid_file':('invalid_structure','format_mismatch'),
        'limits_exceeded':('file_limits',),'unsupported_content':('unsupported_content',)}
    if issue not in expected[status]:raise ValueError('Technical status and issue disagree')
    if (width is None)!=(height is None):raise ValueError('Image dimensions must appear together')
    if width is not None and (status!='image_decoded' or width*height>16000000):raise ValueError('Image dimensions exceed scope or pixel budget')
    if status=='image_decoded' and (width is None or page_count is not None):raise ValueError('Decoded image requires dimensions and no PDF page count')
    if (status=='text_extracted')!=(characters>0):raise ValueError('Only successful text extraction can report extracted characters')


class DocumentObject(DocumentLocation):
    object_id: UUID
    mime_type: DocumentMime
    size_bytes: StrictInt=Field(ge=1,le=10485760)
    uploaded_at: AwareDatetime


class ParsedDocument(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: InspectionStatus
    issue: InspectionIssue | None
    page_count: PageCount | None
    width: Dimension | None
    height: Dimension | None
    text: Annotated[str,StringConstraints(strict=True,max_length=32000)]=Field(repr=False)

    @model_validator(mode='after')
    def consistent_output(self) -> Self:
        validate_facts(self.status,self.issue,self.page_count,self.width,self.height,len(self.text))
        if self.status=='text_extracted' and not self.text.strip():raise ValueError('Extracted text must contain non-whitespace characters')
        return self


class DocumentInspection(BaseModel):
    model_config=ConfigDict(extra='ignore')
    document_id: UUID
    file_name: Label
    mime_type: DocumentMime
    size_bytes: StrictInt=Field(ge=1,le=10485760)
    checked_at: AwareDatetime
    sha256: Annotated[str,StringConstraints(strict=True,pattern=r'^[0-9a-f]{64}$')]
    status: InspectionStatus
    issue: InspectionIssue | None
    page_count: PageCount | None
    width: Dimension | None
    height: Dimension | None
    extracted_characters: Count
    scope: Literal['technical_only']
    ocr: Literal['not_performed']
    bank_rules: Literal['not_configured']


    @model_validator(mode='after')
    def consistent_inspection(self) -> Self:
        validate_facts(self.status,self.issue,self.page_count,self.width,self.height,self.extracted_characters)
        if self.mime_type in ('image/png','image/jpeg'):
            if self.page_count is not None or self.status in ('text_extracted','no_extractable_text'):raise ValueError('Images cannot report PDF pages or text extraction without OCR')
        elif self.width is not None or self.status=='image_decoded':raise ValueError('Text documents cannot report image decoding')
        if self.mime_type=='application/pdf' and self.status in ('text_extracted','no_extractable_text') and self.page_count is None:
            raise ValueError('An opened PDF must report its page count')
        if self.mime_type!='application/pdf' and self.page_count is not None:raise ValueError('Only PDF inspection reports a computed page count')
        return self
