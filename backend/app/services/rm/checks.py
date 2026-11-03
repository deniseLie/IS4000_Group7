"""Deterministic checks on every RM assistant output, applied item by item (no model involved).

An item survives only if its figures, dates and TCK- references appear in the records it cites (or, failing that, in
another supplied record, whose id is then added as a citation), it contains no secrets or links, makes no claim that
something was done, and gives no investment advice or credit decision. Failing items are withheld and counted; the
rest of the reply is still shown. These are conservative format checks, not a proof that every sentence is true.
"""
import json
import re
import unicodedata
from dataclasses import dataclass,field
from app.services.privacy import redact
from app.services.source_checks import numbers,unit_values,record_units,unit_matches,dates,dates_supported
from app.services.rm.evidence import Bundle

TICKET=re.compile(r'\bTCK-\d{6,}\b')
URL=re.compile(r'https?://|www\.',re.I)
# The assistant is read-only: it never says it sent, booked or changed anything.
ACTION_CLAIM=re.compile(r"\b(?:I|we)(?:['’]ve|\s+have|\s+just|\s+already|\s+successfully)*\s+(?:sent|submitted|booked|scheduled|created|raised|"
    r"cancell?ed|approved|rejected|emailed|called|updated|closed|resolved|messaged|notified|filed|logged)\b",re.I)
# Not investment advice and not a credit or approval decision (RM4, RM8, R08).
ADVICE=re.compile(r"\b(?:I|we)\s+(?:would\s+)?(?:recommend|advise)\b|\brecommend(?:s|ed|ing)?\s+(?:that\s+)?(?:he|she|they|the client|you|[A-Z][a-z]+)\s+"
    r"(?:should\s+)?(?:buy|sell|invest|refinance|switch|place|put|move|take)\b|\b(?:he|she|they|the client|[A-Z][a-z]+)\s+should\s+"
    r"(?:buy|sell|invest|refinance|switch to|take up)\b|\b(?:you|we|I)\s+should\s+(?:approve|reject|decline)\b|\bshould\s+be\s+(?:approved|rejected|declined)\b|"
    r"\brecommend(?:s|ed|ing)?\s+(?:approving|rejecting|declining|approval|rejection)\b|\b(?:is|are)\s+likely\s+to\s+be\s+(?:approved|rejected)\b|"
    r"\bguaranteed?\s+(?:return|rate|approval)\b",re.I)


def _record_units(source: str) -> set:
    """Currency amounts per record. Sources are one JSON record per line (Evidence.text), often a list such as accounts;
    record_units reads a single object, so each line is wrapped. Currency still never crosses records."""
    found=set()
    for line in source.splitlines():
        try:data=json.loads(line)
        except ValueError:continue
        found|=record_units(json.dumps({'record':data},default=str))
    return found


def _supports(text: str,source: str) -> str|None:
    """None when every figure, unit, date and reference in text appears in source."""
    if not numbers(text)<=numbers(source):return 'unsupported_figure'
    units=unit_values(source)|_record_units(source)
    if not all(any(unit_matches(claim,item) for item in units) for claim in unit_values(text)):return 'unsupported_unit'
    if not dates_supported(dates(text),dates(source)):return 'unsupported_date'
    if not set(TICKET.findall(text))<=set(TICKET.findall(source)):return 'unknown_reference'
    return None


def safety(text: str,allow_claims: bool=False) -> str|None:
    if redact(text)[1]:return 'secret'
    if URL.search(text):return 'url'
    if ADVICE.search(text):return 'advice_or_decision'
    if not allow_claims and ACTION_CLAIM.search(text):return 'action_claim'
    return None


def has_facts(text: str) -> bool:return bool(numbers(text) or dates(text) or TICKET.search(text))


def grounded(text: str,cited: list[str],bundle: Bundle) -> tuple[str|None,list[str]]:
    """Check text against its cited records; if a figure is only in another supplied record, cite that record too."""
    valid=[i for i in dict.fromkeys(cited) if bundle.get(i)]
    if not has_facts(text):return (None,valid) if valid else ('no_source',valid)
    reason=_supports(text,'\n'.join(bundle.get(i).text for i in valid)) if valid else 'no_source'
    if reason is None:return None,valid
    if _supports(text,'\n'.join(e.text for e in bundle.items)) is None:
        single=[e.id for e in bundle.items if e.id not in valid and _supports(text,'\n'.join([*(bundle.get(i).text for i in valid),e.text])) is None][:1]
        extra=single or [e.id for e in bundle.items if e.id not in valid and (numbers(text)&numbers(e.text) or dates(text)&dates(e.text)
            or set(TICKET.findall(text))&set(TICKET.findall(e.text)))][:3]
        return None,[*valid,*extra]
    return reason,valid


@dataclass
class Report:
    checked: int=0
    withheld: list[str]=field(default_factory=list)

    def as_dict(self) -> dict:return {'checked':self.checked,'withheld':len(self.withheld),'reasons':sorted(set(self.withheld))}


SOURCED_KINDS={'record','client_stated'}


def check_sections(sections: list[dict],bundle: Bundle,report: Report,suggestions_need_sources: bool=False) -> list[dict]:
    clean=[]
    for section in sections:
        items=[]
        for item in section.get('items',[]):
            report.checked+=1
            text=(item.get('text') or '').strip()
            if not text:continue
            reason=safety(text)
            ids=item.get('source_ids') or []
            if reason is None:
                needs=item.get('kind') in SOURCED_KINDS or suggestions_need_sources and item.get('kind')=='suggestion'
                if needs or has_facts(text):reason,ids=grounded(text,ids,bundle)
                else:ids=[i for i in dict.fromkeys(ids) if bundle.get(i)]
            if reason:report.withheld.append(reason);continue
            items.append({**item,'text':text,'source_ids':ids})
        if items:clean.append({'heading':section.get('heading') or '','items':items})
    return clean


def check_summary(text: str,bundle: Bundle,report: Report) -> str:
    text=(text or '').strip()
    if not text:return ''
    reason=safety(text) or (grounded(text,bundle.ids(),bundle)[0] if has_facts(text) else None)
    if reason:
        report.withheld.append(reason)
        return ''
    return text


def shingles(text: str,size: int=6) -> set[tuple[str,...]]:
    words=re.findall(r"[a-z0-9']+",text.lower())
    return {tuple(words[i:i+size]) for i in range(max(0,len(words)-size+1))}


def check_draft(draft: str,bundle: Bundle,internal: list[str]) -> tuple[str|None,list[str]]:
    """RM9: a client draft may use only recorded facts and must not repeat internal notes. Claims that something was
    done are not refused (the RM writes in her own voice) but are listed as cautions for her to confirm."""
    cautions=[]
    reason=safety(draft,allow_claims=True)
    if reason:return reason,cautions
    every='\n'.join(e.text for e in bundle.items)
    reason=_supports(draft,every)
    if reason:return reason,cautions
    if any(shingles(draft)&shingles(note) for note in internal if note):return 'internal_note_leak',cautions
    if ACTION_CLAIM.search(draft):cautions.append('The draft says something has already been done. Confirm it is true before you send it.')
    return None,cautions


def normalise(text: str) -> str:
    return re.sub(r'\s+',' ',re.sub(r"[^a-z0-9$.,' ]+",' ',unicodedata.normalize('NFKC',text).lower())).strip()


def quoted(quote: str,sources: list[str]) -> bool:
    """RM5/RM10: an action must quote the RM's notes (or her correction); a paraphrase of eight words or more must overlap."""
    q=normalise(quote)
    if len(q)<4:return False
    body=' '.join(normalise(s) for s in sources)
    if q in body:return True
    words=q.split()
    return len(words)>=8 and bool(shingles(' '.join(words),5)&shingles(body,5))


def similar(a: str,b: str) -> bool:
    x,y=set(normalise(a).split())-{'the','a','to','of','for','and','on','his','her'},set(normalise(b).split())-{'the','a','to','of','for','and','on','his','her'}
    return bool(x and y) and len(x&y)/len(x|y)>=0.6
