"""Actual isolated parsing of synthetic bytes, malformed/encrypted files and decompression limits; no model or Storage fake."""
import asyncio
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED,ZipFile
import pytest
from pypdf import PdfWriter
from app.services.document_parser_process import parse
from app.schemas.document_uploads import DocumentMime

FIXTURES=Path(__file__).parents[2]/'frontend/tests/fixtures/documents'
DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document'


@pytest.mark.parametrize('extension,mime,status,pages',[('pdf','application/pdf','text_extracted',1),('docx',DOCX,'text_extracted',None),
    ('png','image/png','image_decoded',None),('jpg','image/jpeg','image_decoded',None)])
def test_real_fixtures_parse_in_credential_free_worker(extension: str,mime: DocumentMime,status: str,pages: int | None) -> None:
    result=asyncio.run(parse((FIXTURES/f'Synthetic DEMO.{extension}').read_bytes(),mime))
    assert result.status==status and result.page_count==pages
    if status=='text_extracted':assert 'Synthetic DEMO upload fixture' in result.text
    else:assert result.width==32 and result.height==32 and result.text=='' and result.issue=='ocr_unavailable'


@pytest.mark.parametrize('mime',['application/pdf',DOCX,'image/png','image/jpeg'])
def test_malformed_bytes_never_look_like_a_success(mime: DocumentMime) -> None:
    result=asyncio.run(parse(b'Synthetic invalid file content',mime))
    assert result.status=='invalid_file' and result.text==''


def test_encrypted_blank_and_oversized_page_tree() -> None:
    writer=PdfWriter();writer.add_blank_page(width=200,height=200)
    blank=BytesIO();writer.write(blank)
    result=asyncio.run(parse(blank.getvalue(),'application/pdf'))
    assert result.status=='no_extractable_text' and result.page_count==1
    writer.encrypt('synthetic-password');encrypted=BytesIO();writer.write(encrypted)
    assert asyncio.run(parse(encrypted.getvalue(),'application/pdf')).status=='encrypted'
    many=PdfWriter()
    for _ in range(21):many.add_blank_page(width=200,height=200)
    output=BytesIO();many.write(output)
    assert asyncio.run(parse(output.getvalue(),'application/pdf')).status=='limits_exceeded'


def package(document: bytes) -> bytes:
    with ZipFile(FIXTURES/'Synthetic DEMO.docx') as original:
        parts={name:original.read(name) for name in original.namelist()}
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name,data in parts.items():archive.writestr(name,document if name=='word/document.xml' else data)
    return output.getvalue()


def test_docx_entity_expansion_and_compressed_bomb_are_rejected() -> None:
    malicious=b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'
    result=asyncio.run(parse(package(malicious),DOCX))
    assert result.status=='invalid_file' and result.text==''
    assert asyncio.run(parse(package(b'x'*1000000),DOCX)).status=='limits_exceeded'
    assert asyncio.run(parse(b'',DOCX)).status=='limits_exceeded'


def test_large_image_and_long_docx_text_are_rejected_before_unbounded_decoding() -> None:
    from PIL import Image
    image=Image.new('L',(4096,4097));output=BytesIO();image.save(output,'PNG')
    assert asyncio.run(parse(output.getvalue(),'image/png')).status=='limits_exceeded'
    # Non-repeating text stays below the ZIP ratio cap but exceeds the text budget.
    text=' '.join(str(value) for value in range(10000))
    document=('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>').encode()
    assert asyncio.run(parse(package(document),DOCX)).status=='limits_exceeded'


def test_docx_does_not_accept_a_broken_or_external_main_relationship() -> None:
    with ZipFile(FIXTURES/'Synthetic DEMO.docx') as original:
        parts={name:original.read(name) for name in original.namelist()}
    for relationship,status in [(b'<broken/>','invalid_file'),
        (parts['_rels/.rels'].replace(b'Target="word/document.xml"',b'Target="https://example.com/remote.xml" TargetMode="External"'),'unsupported_content')]:
        output=BytesIO()
        with ZipFile(output,'w',ZIP_DEFLATED) as archive:
            for name,data in parts.items():archive.writestr(name,relationship if name=='_rels/.rels' else data)
        result=asyncio.run(parse(output.getvalue(),DOCX))
        assert result.status==status and result.text==''


def test_cancelled_inspection_reaps_its_real_child() -> None:
    import psutil
    async def cancel() -> None:
        parent=psutil.Process();before={child.pid for child in parent.children()}
        task=asyncio.create_task(parse((FIXTURES/'Synthetic DEMO.pdf').read_bytes(),'application/pdf'))
        async with asyncio.timeout(2):
            while not (created:={child.pid for child in parent.children()}-before):await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert all(not psutil.pid_exists(pid) for pid in created)
    asyncio.run(cancel())


def test_real_child_output_is_bounded_before_buffering_its_entire_stream() -> None:
    import sys
    from app.services.document_parser_process import DocumentParserError,read_output
    async def bounded() -> None:
        process=await asyncio.create_subprocess_exec(sys.executable,'-c','import sys; sys.stdout.buffer.write(b"x"*1048576)',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            with pytest.raises(DocumentParserError,match='exceeded 4096 bytes'):await read_output(process.stdout,4096,'diagnostics')
        finally:
            if process.returncode is None:process.kill()
            while await process.stdout.read(65536):pass
            await process.wait()
    asyncio.run(bounded())
