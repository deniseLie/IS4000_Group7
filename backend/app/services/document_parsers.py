"""Bounded technical readers, never OCR or bank approval. PDF text layers and DOCX body text only.

No macros, scripts, links or embedded files are executed. DOCX pagination cannot be inferred
without a layout engine. Successfully decoding pixels or extracting text does not prove legibility.
"""
from io import BytesIO
import warnings
from xml.etree.ElementTree import ParseError as XMLParseError
from zipfile import BadZipFile,ZipFile
from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from PIL import Image,UnidentifiedImageError
from pypdf import Configuration,PdfReader,apply_configuration
from pypdf.errors import LimitReachedError,PyPdfError
from app.schemas.document_inspection import InspectionIssue,InspectionStatus,ParsedDocument
from app.schemas.document_uploads import DocumentMime

MAX_TEXT=32000
MAX_XML=4*1024*1024
WORD='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
PACKAGE='http://schemas.openxmlformats.org/package/2006/'
DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
PDF_LIMITS=Configuration(maximum_declared_stream_length=1048576,array_based_stream_maximum_output_length=1048576,
    array_maximum_element_count=2000,dictionary_maximum_element_count=2000,zlib_maximum_output_length=1048576,
    lzw_maximum_output_length=1048576,run_length_maximum_output_length=1048576,brotli_maximum_output_length=1048576,
    jbig2_maximum_output_length=1048576,image_maximum_buffer_size=1048576,stream_decoding_work_maximum_length=4194304,
    stream_parsing_work_maximum_count=50000,page_tree_maximum_entries=50,page_tree_maximum_depth=20,
    xform_maximum_invocations_per_extraction=100,jbig2dec_binary=None)


def empty_result(status: InspectionStatus,issue: InspectionIssue,page_count: int | None) -> ParsedDocument:
    return ParsedDocument(status=status,issue=issue,page_count=page_count,width=None,height=None,text='')


def pdf_text(data: bytes) -> ParsedDocument:
    if not data.startswith(b'%PDF-'):return empty_result('invalid_file','format_mismatch',None)
    try:
        with apply_configuration(PDF_LIMITS):
            reader=PdfReader(BytesIO(data),strict=True)
            if reader.is_encrypted:return empty_result('encrypted','encrypted_file',None)
            count=len(reader.pages)
            if count<1:return empty_result('invalid_file','invalid_structure',None)
            if count>20:return empty_result('limits_exceeded','file_limits',None)
            pieces: list[str]=[]
            for page in reader.pages:
                text=page.extract_text()
                if sum(map(len,pieces))+len(text)+len(pieces)>MAX_TEXT:return empty_result('limits_exceeded','file_limits',count)
                pieces.append(text)
            text='\n'.join(pieces)
            if not text.strip():return empty_result('no_extractable_text','no_text_layer',count)
            return ParsedDocument(status='text_extracted',issue=None,page_count=count,width=None,height=None,text=text)
    except (LimitReachedError,RecursionError,MemoryError):return empty_result('limits_exceeded','file_limits',None)
    except PyPdfError:return empty_result('invalid_file','invalid_structure',None)


def docx_text(data: bytes) -> ParsedDocument:
    if not data.startswith(b'PK\x03\x04'):return empty_result('invalid_file','format_mismatch',None)
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries=archive.infolist(); names=[entry.filename for entry in entries]
            if len(entries)>128 or sum(entry.file_size for entry in entries)>8*1024*1024:
                return empty_result('limits_exceeded','file_limits',None)
            if len(names)!=len(set(names)) or not {'[Content_Types].xml','_rels/.rels','word/document.xml'}<=set(names):
                return empty_result('invalid_file','invalid_structure',None)
            if any(entry.flag_bits&1 for entry in entries):return empty_result('encrypted','encrypted_file',None)
            if any(entry.file_size>MAX_XML or entry.file_size>100*max(1,entry.compress_size) for entry in entries):
                return empty_result('limits_exceeded','file_limits',None)
            content=fromstring(archive.read('[Content_Types].xml'),forbid_dtd=True,forbid_entities=True,forbid_external=True)
            if content.tag!='{'+PACKAGE+'content-types}Types':return empty_result('invalid_file','invalid_structure',None)
            if not any(node.tag=='{'+PACKAGE+'content-types}Override' and node.attrib.get('PartName')=='/word/document.xml' and node.attrib.get('ContentType')==DOCX+'.main+xml'
                for node in content):return empty_result('invalid_file','format_mismatch',None)
            relationships=fromstring(archive.read('_rels/.rels'),forbid_dtd=True,forbid_entities=True,forbid_external=True)
            if relationships.tag!='{'+PACKAGE+'relationships}Relationships':return empty_result('invalid_file','invalid_structure',None)
            main=[node for node in relationships if node.tag=='{'+PACKAGE+'relationships}Relationship' and
                node.attrib.get('Type')=='http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument']
            if len(main)!=1:return empty_result('invalid_file','invalid_structure',None)
            if main[0].attrib.get('TargetMode','Internal')!='Internal' or main[0].attrib.get('Target') not in ('word/document.xml','/word/document.xml'):
                return empty_result('unsupported_content','unsupported_content',None)
            if any(name.lower().endswith('vbaproject.bin') for name in names):return empty_result('unsupported_content','unsupported_content',None)
            root=fromstring(archive.read('word/document.xml'),forbid_dtd=True,forbid_entities=True,forbid_external=True)
            if root.tag!='{'+WORD+'}document':return empty_result('invalid_file','invalid_structure',None)
            body=root.find('{'+WORD+'}body')
            if body is None:return empty_result('invalid_file','invalid_structure',None)
            paragraphs=[''.join(node.text or '' for node in paragraph.iter('{'+WORD+'}t')) for paragraph in body.iter('{'+WORD+'}p')]
            if sum(map(len,paragraphs))+max(0,len(paragraphs)-1)>MAX_TEXT:return empty_result('limits_exceeded','file_limits',None)
            text='\n'.join(paragraphs)
            if not text.strip():return empty_result('no_extractable_text','no_text_layer',None)
            return ParsedDocument(status='text_extracted',issue=None,page_count=None,width=None,height=None,text=text)
    except (BadZipFile,XMLParseError,DefusedXmlException,UnicodeDecodeError):return empty_result('invalid_file','invalid_structure',None)
    except (RecursionError,MemoryError):return empty_result('limits_exceeded','file_limits',None)


def image_pixels(data: bytes,mime_type: DocumentMime) -> ParsedDocument:
    expected={'image/png':'PNG','image/jpeg':'JPEG'}[mime_type]
    try:
        with warnings.catch_warnings(action='error',category=Image.DecompressionBombWarning),Image.open(BytesIO(data),formats=[expected]) as image:
            width,height=image.size
            if width*height>16000000:return empty_result('limits_exceeded','file_limits',None)
            if image.format!=expected:return empty_result('invalid_file','format_mismatch',None)
            if expected=='PNG' and image.n_frames!=1:return empty_result('unsupported_content','unsupported_content',None)
            image.verify()
        with Image.open(BytesIO(data),formats=[expected]) as image:image.load()
        return ParsedDocument(status='image_decoded',issue='ocr_unavailable',page_count=None,width=width,height=height,text='')
    except (UnidentifiedImageError,OSError,SyntaxError,ValueError):return empty_result('invalid_file','invalid_structure',None)
    except (Image.DecompressionBombError,Image.DecompressionBombWarning,MemoryError):return empty_result('limits_exceeded','file_limits',None)


def parse(data: bytes,mime_type: DocumentMime) -> ParsedDocument:
    if not 1<=len(data)<=10485760:return empty_result('limits_exceeded','file_limits',None)
    if mime_type=='application/pdf':return pdf_text(data)
    if mime_type==DOCX:return docx_text(data)
    return image_pixels(data,mime_type)
