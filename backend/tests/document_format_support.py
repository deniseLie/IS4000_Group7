"""Generate synthetic document bytes for real parser, OCR and upload integrations."""
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape
from PIL import Image,ImageDraw,ImageFont
from tests.test_document_parsers import package


def address_lines(issued: date) -> list[str]:
    return ['SP SERVICES LTD','Utilities bill','Account holder: MICHELLE NG',
        'Blk 123 Example Avenue 4','Singapore 560123','Bill date: '+issued.strftime('%d %b %Y')]


def docx_bytes(lines: list[str]) -> bytes:
    paragraphs=''.join('<w:p><w:r><w:t>'+escape(line)+'</w:t></w:r></w:p>' for line in lines)
    return package(('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+paragraphs+'</w:body></w:document>').encode())


def scan_page(lines: list[str]) -> Image.Image:
    image=Image.new('RGB',(1800,1400),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.load_default(size=44)
    for index,line in enumerate(lines):draw.text((80,80+index*85),line,fill='black',font=font)
    return image


def scan_png(lines: list[str]) -> bytes:
    output=BytesIO();scan_page(lines).save(output,'PNG');return output.getvalue()


def scan_jpeg(lines: list[str]) -> bytes:
    output=BytesIO();scan_page(lines).save(output,'JPEG',quality=95);return output.getvalue()


def scan_pdf(lines: list[str]) -> bytes:
    output=BytesIO();scan_page(lines).save(output,'PDF',resolution=150);return output.getvalue()
