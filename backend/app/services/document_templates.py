"""Template-based preliminary document checks (PRECHECK_MODE=template, the default).

Each requested item is matched to one template by its label and instructions (proof of address, bank statements,
source-of-wealth declaration, tax residency form, identity document). A template lists what the document must show,
and each check is a plain rule over the extracted text: the client's name, a Singapore address with postal code, a
recognised issuer, a recent date, statement months, a signature line. No model call, so results are repeatable.

Like the notice checks, this is preliminary: it never accepts a document for the bank. Findings carry only template
labels (never extracted values). Items no template fits fall back to the notice checks.
"""
import calendar
import hashlib
import re
from dataclasses import dataclass,field
from datetime import date,timedelta
from app.schemas.prechecks import CheckPolicy,Finding,NoticeRequirement
from app.services.notice_checks import needs_translation,shift

MONTHS={name:i for i,names in enumerate(calendar.month_name) for name in ([names.lower(),names[:3].lower()] if names else [])}
MON=r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
DAY_MONTH_YEAR=re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*'+MON+r'[\s\-/.,]*(\d{4}|\d{2})\b',re.I)
MONTH_DAY_YEAR=re.compile(r'\b'+MON+r'\s+(\d{1,2}),?\s+(\d{4})\b',re.I)
ISO=re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
NUMERIC=re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b')  # Singapore documents print day first
MONTH_YEAR=re.compile(r'\b'+MON+r"[\s\-'/,]*(\d{4})\b",re.I)
RANGE=re.compile(r'(\d{1,2}[\s\-/.]*[A-Za-z]{3,9}[\s\-/.,]*\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\s*(?:-|–|to|until)\s*'
    r'(\d{1,2}[\s\-/.]*[A-Za-z]{3,9}[\s\-/.,]*\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})',re.I)
POSTCODE=re.compile(r'\b(?:singapore|s)\s*\(?\s*\d{6}\s*\)?|\b\d{6}\b(?=[^\n]{0,20}\bsingapore\b)|\bsingapore\b[^\n]{0,20}\b\d{6}\b',re.I)
UTILITIES=('sp services','sp group','singapore power','geneco','senoko','tuas power','keppel electric','pacificlight','sembcorp power',
    'city energy','pub ','singtel','starhub','m1 limited','myrepublic','simba','circles.life','town council')
BANKS=('dbs','posb','ocbc','uob','united overseas bank','maybank','standard chartered','hsbc','citibank','bank of china','icbc',
    'rhb','cimb','trust bank','gxs','boc','bank')
SIGNATURE=re.compile(r'\bsign(?:ed|ature)\b|\bsigned\s+by\b',re.I)
# A certifier's statement on a copy or a translation (CR5). "Self-certification" (a form's title) is not one.
CERTIFIED=re.compile(r'\bcertified\s+(?:true\s+|as\s+a\s+true\s+)?(?:copy|translation)\b|\b(?<!self-)certifi(?:ed|cation)\s+by\b'
    r'|\bI\s+(?:hereby\s+)?certify\b|\bnotar(?:y|ised|ized|ial)\b|\bcommissioner\s+for\s+oaths\b|\bsworn\s+translat|\battested\s+by\b',re.I)
TRANSLATED=re.compile(r'\btranslat(?:ed|ion)\s+(?:from|of)\b|\benglish\s+translation\b|\btranslator\b',re.I)
CERTIFICATION='A certification on the copy (for example “certified true copy”, signed and dated by the certifier)'
TRANSLATOR='The translator’s certification that the translation is true and complete'


def all_dates(text: str) -> list[date]:
    found=[]
    def add(year: int,month: int,day: int) -> None:
        if year<100:year+=2000
        try:found.append(date(year,month,day))
        except ValueError:pass
    for d,m,y in DAY_MONTH_YEAR.findall(text):add(int(y),MONTHS[m.lower()[:3]],int(d))
    for m,d,y in MONTH_DAY_YEAR.findall(text):add(int(y),MONTHS[m.lower()[:3]],int(d))
    for y,m,d in ISO.findall(text):add(int(y),int(m),int(d))
    for d,m,y in NUMERIC.findall(text):add(int(y),int(m),int(d))
    return found


def months_covered(text: str) -> set[tuple[int,int]]:
    months={(int(y),MONTHS[m.lower()[:3]]) for m,y in MONTH_YEAR.findall(text)}
    months|={(value.year,value.month) for value in all_dates(text)}
    for start,end in RANGE.findall(text):
        first,last=all_dates(start),all_dates(end)
        if len(first)==1 and len(last)==1 and first[0]<=last[0]<=first[0]+timedelta(days=400):
            year,month=first[0].year,first[0].month
            while (year,month)<=(last[0].year,last[0].month):
                months.add((year,month));year,month=(year+1,1) if month==12 else (year,month+1)
    return months


def name_tokens(name: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z][A-Za-z'\-]+",re.sub(r'\(DEMO\)','',name,flags=re.I)) if len(token)>1]


def has_name(text: str,name: str) -> bool:
    tokens=name_tokens(name)
    return bool(tokens) and all(re.search(r'\b'+re.escape(token)+r'\b',text,re.I) for token in tokens)


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    match: re.Pattern[str]
    must_show: tuple[str,...]
    example: tuple[str,...]
    tips: tuple[str,...]=field(default=())
    certified: bool=False  # the bank needs a certified copy, not a plain scan


TEMPLATES=(
    Template('proof_address','Proof of residential address (Singapore)',re.compile(r'\baddress\b|\butility\b',re.I),
        ('Your full name, as on your bank records','Your Singapore residential address, including the 6-digit postal code',
         'The company that issued it (a utility, telco, town council or bank)','A bill or statement date within the last 3 months'),
        ('SP SERVICES LTD  ·  Utilities bill','Account holder: MS TAN MEI LING','Blk 123 Example Avenue 4  #05-67  Singapore 560123',
         'Bill date: 05 Oct 2026  ·  Bill period: 01 Sep 2026 – 30 Sep 2026','Electricity · Water · Gas charges …'),
        ('Download the PDF from the provider’s website or app; a phone photo may not be readable.',
         'Older than 3 months? Download the latest bill instead.','A bank statement also works if it shows your address.')),
    Template('bank_statements','Bank statements (last 3 full months)',re.compile(r'\bstatements?\b',re.I),
        ('Your full name','The bank’s name','One statement for each of the last 3 full calendar months'),
        ('EXAMPLE BANK  ·  Statement of account','Account holder: TAN MEI LING','Statement period: 01 Jul 2026 – 30 Sep 2026',
         'Opening balance … Closing balance …'),
        ('Upload one PDF that combines the three months, or a single statement that covers all of them.',)),
    Template('wealth_declaration','Source of wealth declaration',re.compile(r'\bwealth\b',re.I),
        ('Your full name','A description of where your wealth comes from','The date you completed it','Your signature'),
        ('Source of wealth declaration','Name: TAN MEI LING','My wealth comes from: salary and bonuses since 2012; sale of property in 2021',
         'Date: 20 Oct 2026','Signature: ____________'),
        ('Sign and date the form before you scan or save it as a PDF.',)),
    Template('tax_residency','Tax residency self-certification',re.compile(r'\btax\b',re.I),
        ('Your full name','Each country where you are tax resident','The date you completed it','Your signature'),
        ('Tax residency self-certification','Name: TAN MEI LING','Country of tax residence: Singapore  ·  TIN: (your tax number)',
         'Date: 20 Oct 2026','Signature: ____________'),
        ('List every country where you pay tax.',)),
    Template('identity','Identity document',re.compile(r'\b(?:passport|identity|nric|id card|identification)\b',re.I),
        ('Your full name','An expiry date that has not passed',CERTIFICATION),
        ('REPUBLIC OF EXAMPLE  ·  Passport','Name: TAN MEI LING','Date of expiry: 14 Mar 2031',
         'Certified true copy of the original — signed and dated by (certifier’s name and role), 20 Oct 2026'),
        ('Scan the photo page in colour as a PDF.','Have the copy certified (for example by a lawyer or notary) before you upload it.'),certified=True),
)


def template_for(notice: str) -> Template | None:
    """Match on the item's own label and instructions (the last two lines of the notice), not the whole case letter."""
    lines=[line for line in notice.splitlines() if line.strip()]
    own=' '.join(lines[-2:]) if len(lines)>=2 else notice
    return next((template for template in TEMPLATES if template.match.search(own)),None)


def requirement(quote: str,kind: str='presence',count: int=0,unit: str='none') -> NoticeRequirement:
    reference=hashlib.sha256(f'{kind}:{count}:{unit}:{quote}'.encode()).hexdigest()[:16]
    return NoticeRequirement(reference=reference,quote=quote,kind=kind,count=count,unit=unit)


def check(template: Template,text: str,pages: int | None,name: str,today: date,policy: CheckPolicy | None=None) -> tuple[list[NoticeRequirement],list[Finding]]:
    policy=policy or CheckPolicy()
    requirements=[requirement(item) for item in template.must_show]
    if not text.strip():return requirements,[Finding(code='unassessable',field='No readable text')]
    if re.search(r'ignore (?:all |the |previous )?(?:rules|instructions)|system prompt|approve (?:me|this)',text,re.I):
        return requirements,[Finding(code='unassessable')]
    lower=text.lower()
    findings: list[Finding]=[]
    def missing(label: str) -> None:findings.append(Finding(code='missing_field',field=label))
    if not has_name(text,name):missing('Your full name, as on your bank records')
    dates=[value for value in all_dates(text) if value<=today]
    if template.key=='proof_address':
        if not POSTCODE.search(text):missing('Your Singapore address with its 6-digit postal code')
        if not any(issuer in lower for issuer in UTILITIES+BANKS):missing('The company that issued it')
        if not dates:missing('A bill or statement date')
        elif max(dates)<shift(today,3,'months'):findings.append(Finding(code='date_outdated',field='A bill or statement date within the last 3 months'))
    elif template.key=='bank_statements':
        if not any(bank in lower for bank in BANKS):missing('The bank’s name')
        end=today.replace(day=1)-timedelta(days=1) if policy.statement_period_mode=='completed_months' else today
        needed=[]
        year,month=end.year,end.month
        for _ in range(3):needed.append((year,month));year,month=(year-1,12) if month==1 else (year,month-1)
        absent=[pair for pair in reversed(needed) if pair not in months_covered(text)]
        if absent:findings.append(Finding(code='date_outdated',field='Statements for '+', '.join(f'{calendar.month_abbr[m]} {y}' for y,m in absent)))
    elif template.key in ('wealth_declaration','tax_residency'):
        if template.key=='wealth_declaration' and not re.search(r'\bwealth\b|\bsource of (?:funds|income)\b',text,re.I):missing('Where your wealth comes from')
        if template.key=='tax_residency' and not re.search(r'\btax\b[^\n]{0,40}\b(?:residen|country)|\bTIN\b',text,re.I):missing('Your country of tax residence')
        if not dates:missing('The date you completed it')
        if not SIGNATURE.search(text):missing('Your signature')
    elif template.key=='identity':
        future=[value for value in all_dates(text) if value>today]
        if not future:findings.append(Finding(code='date_outdated',field='An expiry date that has not passed'))
    labels=[(int(a),int(b)) for a,b in re.findall(r'\bpage\s+(\d{1,2})\s+of\s+(\d{1,2})\b',text,re.I)]
    if labels and len({b for _,b in labels})==1 and pages is not None and pages<labels[0][1]<=20:
        absent_pages=set(range(1,labels[0][1]+1))-{a for a,_ in labels}
        if absent_pages:findings.append(Finding(code='missing_pages',field=', '.join(str(n) for n in sorted(absent_pages))))
    certified=bool(CERTIFIED.search(text))
    if template.certified and not certified:missing(CERTIFICATION)
    elif TRANSLATED.search(text) and not certified:missing(TRANSLATOR)
    if needs_translation(text):findings.append(Finding(code='translation_needed'))
    return requirements,findings[:12]


def describe() -> list[dict[str,object]]:
    """Public, synthetic guidance for the upload screen: what each template must show and a made-up example."""
    return [{'key':t.key,'title':t.title,'match':t.match.pattern,'must_show':list(t.must_show),'example':list(t.example),'tips':list(t.tips)} for t in TEMPLATES]
