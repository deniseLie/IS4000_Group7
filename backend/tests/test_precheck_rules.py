"""Actual isolated extraction and OCR, independent of provider availability."""
import asyncio
from datetime import date
from io import BytesIO
from pathlib import Path
import pytest
from app.services.document_parser_process import parse
from app.services.document_ocr import extract
from app.core.config import settings


def test_review_letter_question_routes_to_recorded_case_without_inventing_requirements():
    from app.services.orchestrator import simple_issue,topic_domain
    question='I got a letter asking me to update my information. What do I need to send?'
    assert simple_issue(question,'English').domain=='compliance'
    assert topic_domain('Help me update my email address') is None
    assert topic_domain('A letter about my account balance')=='retail'


def test_pdf_with_an_unreadable_page_is_not_completely_extracted():
    from pypdf import PdfReader,PdfWriter
    source=Path(__file__).parents[2]/'frontend/tests/fixtures/documents/Synthetic DEMO.pdf'
    writer=PdfWriter();writer.add_page(PdfReader(source).pages[0]);writer.add_blank_page(width=200,height=200)
    data=BytesIO();writer.write(data)
    result=asyncio.run(parse(data.getvalue(),'application/pdf'))
    assert result.status=='no_extractable_text' and result.text=='' and result.page_count==2


def test_local_ocr_recognises_real_pixels_or_reports_unavailable(monkeypatch):
    from PIL import Image,ImageDraw,ImageFont
    executable=Path(__file__).parents[2]/'.venv/bin/demobank-document-ocr'
    if not executable.exists():pytest.skip('Build local OCR with python -m scripts.install_document_ocr')
    monkeypatch.setattr(settings,'document_ocr_command',str(executable))
    picture=Image.new('RGB',(1600,600),'white');draw=ImageDraw.Draw(picture)
    font=ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc',40)
    for index,line in enumerate(['Full name: Synthetic Demo','Address: Fictional Lane','Document date: 2026-10-01']):draw.text((60,70+index*90),line,font=font,fill='black')
    data=BytesIO();picture.save(data,'PNG')
    text,pages=asyncio.run(extract(data.getvalue(),'image/png',None))
    assert pages==1 and 'Full name' in text and 'Fictional Lane' in text
    blank=BytesIO();Image.new('RGB',(200,200),'white').save(blank,'PNG')
    assert asyncio.run(extract(blank.getvalue(),'image/png',None))[0]==''


def test_check_freshness_expires_at_singapore_midnight_not_after_24_hours():
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from app.services.prechecks import is_fresh
    report=SimpleNamespace(checked_at=datetime(2026,10,24,15,59,tzinfo=timezone.utc))
    assert is_fresh(report,datetime(2026,10,24,15,59,59,tzinfo=timezone.utc))
    assert not is_fresh(report,datetime(2026,10,24,16,0,tzinfo=timezone.utc))
    assert not is_fresh(report,datetime(2026,10,24,15,58,tzinfo=timezone.utc))
