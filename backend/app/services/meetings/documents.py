"""PDF documents rendered from authorised saved snapshots. No model, public file URL, or browser-supplied document text."""
from io import BytesIO
from functools import partial
from importlib.resources import as_file,files
from datetime import date
from typing import Literal
from uuid import UUID
from xml.sax.saxutils import escape
from pydantic import BaseModel,ConfigDict,Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable,Paragraph,SimpleDocTemplate,Spacer
from reportlab.pdfgen.canvas import Canvas
from app.schemas.meeting_packs import PackView
from app.schemas.meeting_lifecycle import Hash
from app.schemas.meeting_preparation import PreparedBrief

AGENDA={'goals':'Confirm the meeting goals','status':'Review the current status','documents':'Discuss the required documents',
 'options':'Discuss the available options','product_terms':'Review the listed product terms','next_steps':'Agree next steps and responsibilities','questions':'Answer your questions'}
QUESTIONS={'clarify_objective':'What would you like to achieve?','confirm_documents':'Which documents or information should we clarify?',
 'review_options':'Which options would you like to discuss?','confirm_next_steps':'What should happen next, and who will do it?',
 'ask_product_risks':'What risks, terms and charges should we clarify?','confirm_timeframe':'What timeframe matters to you?',
 'confirm_document_review':'Which documents has the bank received, which still need to be provided or corrected, and who will confirm the next step and due date?',
 'compare_catalogue_options':'Which of the listed product terms should we compare, including applicable rates, charges, minimum amounts and term lengths?',
 'review_catalogue_product':'Which terms of the listed product would you like to review, such as the rate, charges, minimum amount or term length where applicable?'}


class Section(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    heading: str
    paragraphs: tuple[str,...]


class Document(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    title: str
    reference: str|None
    status: str
    sections: tuple[Section,...]=Field(min_length=1)


class Followup(BaseModel):
    model_config=ConfigDict(frozen=True,extra='ignore')
    owner: Literal['rm','client']
    text: str
    status: Literal['open','done']
    due_on: date|None


class Recap(BaseModel):
    model_config=ConfigDict(frozen=True,extra='ignore')
    meeting_id: UUID
    document_id: UUID
    version: int=Field(strict=True,gt=0)
    content_hash: Hash
    purpose: str
    text: str
    followups: tuple[Followup,...]


def discussion_pack(pack: PackView) -> Document:
    sections=[Section(heading='Agreed purpose',paragraphs=(pack.content.purpose or 'No purpose recorded',)),
        Section(heading='Agenda',paragraphs=tuple(AGENDA[code] for code in pack.content.agenda))]
    for product in pack.content.products:
        terms=[f'Catalogue version {product.version}; effective {product.effective_from} to {product.effective_to}.']
        if product.annual_rate is not None:terms.append(f'Annual rate: {product.annual_rate}%.')
        if product.minimum_amount is not None:terms.append(f'Minimum amount: {product.currency} {product.minimum_amount:,.2f}.')
        if product.fee is not None:terms.append(f'Fee: {product.currency} {product.fee:,.2f}.')
        if product.term_months is not None:terms.append(f'Term: {product.term_months} months.')
        sections.append(Section(heading=product.name,paragraphs=tuple(terms)))
    if pack.content.stated_amounts:
        sections.append(Section(heading='Amounts stated for discussion',paragraphs=tuple(
            f'{amount.label}: {amount.currency} {amount.amount:,.2f}. Entered by the RM for discussion.' for amount in pack.content.stated_amounts)))
    if pack.content.questions:sections.append(Section(heading='Questions to discuss',paragraphs=tuple(QUESTIONS[code] for code in pack.content.questions)))
    sections.append(Section(heading='About this document',paragraphs=(
        'Discussion material. The meeting does not execute a transaction or establish suitability.',
        f'Version {pack.version}.')))
    return Document(title=pack.content.title,reference=None,status=pack.status,sections=tuple(sections))


def internal_brief(prepared: PreparedBrief) -> Document:
    sections=tuple(Section(heading=section.heading,paragraphs=tuple(
        item.label.replace('_',' ')+': '+item.text+(' Sources: '+', '.join(item.sources) if item.sources else '') for item in section.items))
        for section in prepared.card.sections)
    return Document(title='Internal meeting brief',reference=str(prepared.card.meeting_id),status=prepared.decision,
        sections=(Section(heading='Internal - assigned RM only',paragraphs=(f'Saved brief {prepared.brief_id}. Version {prepared.card.version}.',)),*sections))


def shared_recap(recap: Recap) -> Document:
    sections=[Section(heading='Meeting purpose',paragraphs=(recap.purpose,)),Section(heading='Approved and shared recap',paragraphs=(recap.text,))]
    if recap.followups:sections.append(Section(heading='Tracked follow-ups',paragraphs=tuple(
        f'{item.owner.upper()}: {item.text}. Status: {item.status}. Due: {item.due_on or "not agreed"}.' for item in recap.followups)))
    sections.append(Section(heading='Document reference',paragraphs=(f'Approved minutes {recap.document_id}. Version {recap.version}.',)))
    return Document(title='Meeting recap and follow-up',reference=str(recap.meeting_id),status='approved and shared',sections=tuple(sections))


def pdf(document: Document) -> bytes:
    """Escape all source text, wrap long values, and use Unicode CJK text for multilingual meeting content."""
    with as_file(files('app').joinpath('assets/fonts/NotoSansSC-Regular.ttf')) as path:
        font=TTFont('DemoBankNoto',str(path))
    text='\n'.join((document.title,document.status,*[value for section in document.sections for value in (section.heading,*section.paragraphs)]))
    unsupported=sorted({ord(character) for character in text if not character.isspace() and ord(character) not in font.face.charToGlyph})
    if unsupported:raise ValueError('The PDF font cannot display these characters: '+', '.join(f'U+{value:04X}' for value in unsupported)+'. Use supported text or extend the packaged font before downloading.')
    pdfmetrics.registerFont(font)
    output=BytesIO();red=colors.HexColor('#b60013');ink=colors.HexColor('#242424')
    body=ParagraphStyle('meeting-body',fontName='DemoBankNoto',fontSize=10.5,leading=16,textColor=ink,spaceAfter=9,alignment=TA_LEFT,wordWrap='CJK')
    heading=ParagraphStyle('meeting-heading',parent=body,fontName='DemoBankNoto',fontSize=12,leading=18,spaceBefore=14,textColor=red)
    title=ParagraphStyle('meeting-title',parent=body,fontName='DemoBankNoto',fontSize=23,leading=29,spaceAfter=10)
    note=ParagraphStyle('meeting-note',parent=body,fontSize=8,leading=12,textColor=colors.HexColor('#666666'))
    story: list[Flowable]=[Paragraph('DEMOBANK / MEETING',heading),Paragraph(escape(document.title),title),
        Paragraph(escape('Status: '+document.status.replace('_',' ')),note),Spacer(1,8)]
    for section in document.sections:
        story.append(Paragraph(escape(section.heading),heading))
        story.extend(Paragraph(escape(text).replace('\n','<br/>'),body) for text in section.paragraphs)
    def footer(canvas: Canvas,template: SimpleDocTemplate) -> None:
        canvas.saveState();canvas.setStrokeColor(red);canvas.line(42,42,A4[0]-42,42)
        canvas.setFont('DemoBankNoto',7);canvas.setFillColor(ink)
        if document.reference:canvas.drawString(42,29,'Meeting '+document.reference)
        canvas.drawRightString(A4[0]-42,29,'Page '+str(template.page))
        canvas.restoreState()
    SimpleDocTemplate(output,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=32,bottomMargin=58,
        title=document.title,author='UBS Connect',invariant=1).build(story,onFirstPage=footer,onLaterPages=footer,
            canvasmaker=partial(Canvas,initialFontName='DemoBankNoto'))
    return output.getvalue()
