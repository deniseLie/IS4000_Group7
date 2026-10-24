"""Generic notice-grounded checks. Only notice quotes/codes/counts persist; extracted evidence is ephemeral."""
import calendar
import hashlib
import json
import re
from datetime import date,datetime,timedelta
from app.schemas.prechecks import CheckPolicy,Finding,NoticeRequirement
from app.services import llm_gateway,prompts


def object_schema(title,properties):
    return {'title':title,'type':'object','additionalProperties':False,'properties':properties,'required':list(properties)}


REQUIREMENT_SCHEMA=object_schema('Requirement',{
 'quote':{'type':'string'},'kind':{'type':'string','enum':['presence','date','period','page_count','unsupported']},
 'count':{'type':'integer'},'unit':{'type':'string','enum':['none','days','months','years']}})
PLAN_SCHEMA=object_schema('NoticeRequirements',{'complete':{'type':'boolean'},'requirements':{'type':'array','items':REQUIREMENT_SCHEMA}})
ROW_SCHEMA=object_schema('DocumentEvidence',{'reference':{'type':'string'},'state':{'type':'string','enum':['present','missing','uncertain']},
 'evidence':{'type':'string'},'dates':{'type':'array','items':{'type':'string'}},
 'intervals':{'type':'array','items':object_schema('PeriodInterval',{'start':{'type':'string'},'end':{'type':'string'}})}})
EVIDENCE_SCHEMA=object_schema('EvidenceByRequirement',{'rows':{'type':'array','items':ROW_SCHEMA}})
NUMBERS=dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split(),range(21)))


def requirements_from(data,notice):
    if not data['complete'] or not 1<=len(data['requirements'])<=20:return None
    result=[]
    for entry in data['requirements']:
        quote=entry['quote'];count=entry['count'];kind=entry['kind'];unit=entry['unit']
        if not quote.strip() or len(quote)>2000 or quote not in notice or not 0<=count<=366:return None
        tokens=re.findall(r'\b\w+\b',quote.lower())
        numbers={int(t) for t in tokens if t.isdecimal() and len(t)<5}|{NUMBERS[t] for t in tokens if t in NUMBERS}
        if kind=='page_count' and 'both' in tokens:numbers.add(2)
        if kind in ('presence','unsupported') and (count!=0 or unit!='none'):return None
        if kind=='date' and count==0 and unit!='none':return None
        if (kind in ('period','page_count') or kind=='date' and count>0) and (count<1 or count not in numbers):return None
        if kind=='page_count' and (count>20 or unit!='none'):return None
        if kind in ('date','period') and count and (unit=='none' or not any(t in (unit,unit[:-1]) for t in tokens)):return None
        reference=hashlib.sha256(json.dumps(entry,sort_keys=True).encode()).hexdigest()[:16]
        if any(r.reference==reference for r in result):return None
        result.append(NoticeRequirement(reference=reference,**entry))
    return result


def parsed_date(raw,policy,allow_month=False):
    candidates=set()
    for fmt in policy.date_formats:
        if not allow_month and '%d' not in fmt:continue
        if policy.numeric_date_order=='day_first' and fmt=='%m/%d/%Y':continue
        if policy.numeric_date_order=='month_first' and fmt=='%d/%m/%Y':continue
        try:
            value=datetime.strptime(raw.strip(),fmt).date()
            month_only='%d' not in fmt
            end=value.replace(day=calendar.monthrange(value.year,value.month)[1]) if month_only else value
            candidates.add((value,end))
        except ValueError:pass
    return next(iter(candidates)) if len(candidates)==1 else None


def shift(value,count,unit):
    if unit=='days':return value-timedelta(days=count)
    months=count*(12 if unit=='years' else 1)
    year,zero=divmod(value.year*12+value.month-1-months,12)
    return date(year,zero+1,min(value.day,calendar.monthrange(year,zero+1)[1]))


def assess_evidence(data,text,pages,requirements,today,policy):
    rows=data['rows'];expected={r.reference for r in requirements}
    if len(rows)!=len(expected) or {r['reference'] for r in rows}!=expected:return [Finding(code='unassessable')]
    findings=[]
    for requirement in requirements:
        row=next(r for r in rows if r['reference']==requirement.reference)
        label=requirement.quote;kind=requirement.kind
        if kind=='unsupported':findings.append(Finding(code='unassessable',field=label));continue
        if kind=='page_count':
            if pages is None:findings.append(Finding(code='unassessable',field=label))
            elif pages!=requirement.count:findings.append(Finding(code='page_count',field=label))
            continue
        if row['state']=='uncertain':findings.append(Finding(code='unassessable',field=label));continue
        if row['state']=='missing':
            if row['evidence'] or row['dates'] or row['intervals']:return [Finding(code='unassessable')]
            findings.append(Finding(code='missing_field',field=label));continue
        evidence=row['evidence']
        if not evidence.strip() or len(evidence)>8000 or evidence not in text or evidence.strip().lower() in {'unknown','n/a','[blank]','-'}:
            return [Finding(code='unassessable')]
        if len(row['dates'])>24 or len(row['intervals'])>12:return [Finding(code='unassessable')]
        if kind=='presence':continue
        intervals=[]
        for raw in row['dates']:
            if not raw or raw not in evidence:return [Finding(code='unassessable')]
            parsed=parsed_date(raw,policy,kind=='period')
            if parsed is None:findings.append(Finding(code='date_invalid',field=label));break
            intervals.append(parsed)
        else:
            for pair in row['intervals']:
                if not pair['start'] or not pair['end'] or pair['start'] not in evidence or pair['end'] not in evidence:return [Finding(code='unassessable')]
                start=parsed_date(pair['start'],policy,True);end=parsed_date(pair['end'],policy,True)
                if start is None or end is None or start[0]>end[1]:return [Finding(code='unassessable')]
                intervals.append((start[0],end[1]))
            if kind=='date':
                if len(intervals)!=1 or intervals[0][0]!=intervals[0][1]:findings.append(Finding(code='date_invalid',field=label));continue
                value=intervals[0][0]
                if value>today:findings.append(Finding(code='date_invalid',field=label))
                elif requirement.count and value<shift(today,requirement.count,requirement.unit):findings.append(Finding(code='date_outdated',field=label))
            else:
                if not intervals:findings.append(Finding(code='missing_field',field=label));continue
                # Explicit printed intervals or individually printed periods; transaction dates cannot be supplied as evidence by policy.
                end=today
                if policy.statement_period_mode=='completed_months' and requirement.unit=='months':end=today.replace(day=1)-timedelta(days=1)
                start=shift(end+timedelta(days=1),requirement.count,requirement.unit)
                intervals.sort();covered=start
                for first,last in intervals:
                    if first>covered:break
                    if last>=covered:covered=last+timedelta(days=1)
                if covered<=end:findings.append(Finding(code='date_outdated',field=label))
    # Grounded page numbering can reveal a missing internal page even without a notice count.
    labels=[(int(a),int(b)) for a,b in re.findall(r'\bpage\s+(\d{1,2})\s+of\s+(\d{1,2})\b',text,re.I)]
    if labels and len({b for a,b in labels})==1:
        total=labels[0][1]
        if 1<=total<=20:
            missing=set(range(1,total+1))-{a for a,b in labels}
            if missing and pages is not None and pages<total:findings.append(Finding(code='missing_pages',field=', '.join(str(n) for n in sorted(missing))))
    return list({(f.code,f.field):f for f in findings}.values())[:12]


async def check(text,pages,notice,today,policy=None):
    policy=policy or CheckPolicy()
    if not text.strip() or len(text)>32000 or not notice.strip():return [],[Finding(code='unassessable')]
    if re.search(r'ignore (?:all |the |previous )?(?:rules|instructions)|system prompt|approve (?:me|this)',text,re.I):return [],[Finding(code='unassessable')]
    plan=prompts.load('notice_requirements','v2');extract=prompts.load('document_evidence','v2')
    try:
        result=await llm_gateway.call(plan,plan['system'],[('user',json.dumps({'notice':notice}))],PLAN_SCHEMA)
        requirements=requirements_from(result.data,notice)
        if requirements is None:return [],[Finding(code='unassessable')]
        if all(r.kind=='unsupported' for r in requirements):return requirements,[Finding(code='unassessable')]
        payload={'requirements':[r.model_dump() for r in requirements],'document':text}
        for attempt in range(2):
            result=await llm_gateway.call(extract,extract['system'],[('user',json.dumps(payload,ensure_ascii=False))],EVIDENCE_SCHEMA)
            findings=assess_evidence(result.data,text,pages,requirements,today,policy)
            if not any(f.code=='unassessable' and f.field is None for f in findings) or attempt:return requirements,findings
    except llm_gateway.LLMError:return [],[Finding(code='provider_unavailable')]
    return [],[Finding(code='unassessable')]


async def assess(text,pages,notice,today):
    return (await check(text,pages,notice,today))[1]
