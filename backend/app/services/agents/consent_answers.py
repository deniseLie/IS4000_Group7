"""Consent changes in the chat (consent v2, RM copilot build plan 6e). Rules and records only, never the model:

1. "Stop sharing my transactions with Sophie" offers consent.revoke for her active grant of that category, with what
   changes at once. "Share my chat topics with Sophie for 3 months" offers consent.grant with a scope and an end: every
   account and 6 months of history, or every topic area (or the areas she named), until the meeting or for the period she
   named (at most 12 months), else for 3 months. Other choices are on Data sharing.
2. "Delete the home loan topic" offers insight.delete for that topic as her RM sees it (Undo for 7 days on Data sharing).
3. A meeting request (meeting_answers) gets optional "share until this request is answered" grants for what she does not
   share yet: switches in the offer, off unless she switches them on (0114). A typed yes sends the request only.
Each offer waits for her yes (services/offers.py). A question about sharing ("do you share …?", "how do I …?") is left to
relationship/v7, which explains and never claims a change; a share with someone other than her RM is not ours to offer.
"""
import json
import re
from datetime import timedelta
from typing import NamedTuple
from app.schemas.agents import Evidence
from app.schemas.contracts import AgentResult,Citation,OfferProposal,RequestContext
from app.schemas.consent_sharing import ConsentSharing
from app.schemas.events import Source
from app.services import customer_data,talking_points

CATEGORY_WORDS=(
    ('chat_topics',re.compile(r"\b(?:chat\s+topics?|topics?\s+(?:from|in|of)\s+(?:my\s+)?chats?|my\s+chats?|what\s+i\s+(?:talk|chat)\s+about)\b")),
    ('transaction_insights',re.compile(r'\b(?:transactions?|transaction\s+insights?|spending|money\s+in\s+and\s+out)\b')),
)
LABEL={'transaction_insights':'transaction insights','chat_topics':'chat insights'}  # Chat insights on her pages (talking points in the code)
STOP=re.compile(r"\b(?:stop|withdraw|revoke|end|turn\s+off|switch\s+off|no\s+longer)\s+(?:\w+\s+){0,3}?(?:shar(?:e|ing)|access)\b"
    r"|\bunshare\b|\b(?:stop|don'?t)\s+let(?:ting)?\s+(?:\w+\s+){0,3}?see\b|\bwithdraw\s+(?:my\s+)?consent\b")
SHARE=re.compile(r"\b(?:share|sharing)\b|\blet\s+(?:\w+\s+){0,3}?see\b|\b(?:turn|switch)\s+on\s+(?:\w+\s+){0,3}?sharing\b")
QUESTION=re.compile(r"^\s*(?:do|does|did|is|are|was|were|what|who|why|how|when|which|has|have)\b")
MEETING=re.compile(r'\b(?:meeting|meet|catch[\s-]?up|appointment)\b')  # meeting_answers drafts it, with the switches below
RECIPIENT=re.compile(r"\b(?:with|let)\s+(?:my\s+|the\s+|our\s+)?(\w+)")
RM_WORDS={'rm','relationship','banker','her','him','them','you'}
DELETE=re.compile(r'\b(?:delete|remove|forget|erase)\b')
TOPIC_WORDS={'home_loan':re.compile(r'\bhome\s+loan|\bmortgage|\bcondo|\bproperty\b'),'investments':re.compile(r'\binvest\w*\b'),
    'fixed_deposit':re.compile(r'\bfixed\s+deposits?\b|\bfd\b'),'insurance':re.compile(r'\binsurance\b')}
AREA_WORDS={'home_property':re.compile(r'\bhome|\bproperty|\bmortgage\b'),'investments':re.compile(r'\binvest\w*\b'),
    'deposits':re.compile(r'\bdeposits?\b'),'insurance':re.compile(r'\binsurance\b'),'cards':re.compile(r'\bcards?\b'),
    'everyday_banking':re.compile(r'\beveryday\s+banking\b')}
PERIOD=re.compile(r'\bfor\s+(\d{1,3}|a|one|two|three|six)\s+(day|week|month|year)s?\b')
NUMBERS={'a':1,'one':1,'two':2,'three':3,'six':6}
UNTIL_MEETING=re.compile(r'\buntil\s+(?:my|our|the)\s+(?:next\s+)?meeting\b')
DEFAULT_DAYS=90
NOTICE={'transaction_insights':'ti-2026-11','chat_topics':'ct-2026-11'}  # consent.categories (0109); SQL refuses any other version

NO_RM='You don’t have a relationship manager recorded right now, so there is no one to share with.'
NOT_SHARED='You are not sharing {label} with {rm}, so there is nothing to stop.'
ALREADY='You already share {label} with {rm}. You can change what and until when on Data sharing.'
NO_TOPIC='{rm} doesn’t see a {topic} insight from your chats, so there is nothing to delete.'
CONSEQUENCE={'transaction_insights':'If you stop, {rm} stops seeing your transaction insights at once, and briefs for your meetings say you have not shared them.',
    'chat_topics':'If you stop, {rm} stops seeing insights picked up from your chats, and they are removed at once. Insights you shared yourself stay.'}


class Reply(NamedTuple):
    result: AgentResult
    proposals: list[OfferProposal]
    evidence: list[Evidence]


def evidence_for(as_of,body: dict) -> list[Evidence]:
    """What the reply was built from: codes, ids and dates of her own settings (no free text)."""
    return [Evidence(id='consent-sharing',text=json.dumps(body,ensure_ascii=False,default=str),publisher='DemoBank consent records',
        scope='Her own data sharing settings; read for this request only.',source=Source(id='consent-sharing',
        title='Your data sharing settings',url=None,version=as_of.isoformat(),publisher='DemoBank consent records'))]


def reply(task_id: str,text: str,as_of,body: dict,proposals: list[OfferProposal]|None=None) -> Reply:
    return Reply(AgentResult(task_id=task_id,agent='relationship',status='ok',answer_kind='banking',text=text,
        citations=[Citation(source_id='consent-sharing')]),proposals or [],evidence_for(as_of,body))


def category_in(text: str) -> str|None:
    return next((code for code,rx in CATEGORY_WORDS if rx.search(text)),None)


def display(name: str) -> str:return re.sub(r'\s*\(DEMO\)\s*$','',name)


def for_her_rm(text: str,rm: str) -> bool:
    """No recipient named, or her RM (by role or first name): a share with anyone else is not a consent we hold."""
    named=[m.group(1) for m in RECIPIENT.finditer(text) if m.group(1) not in {'me','us','it','a'}]
    return not named or all(n in RM_WORDS or n==rm.split()[0].lower() for n in named)


def end_of(text: str,sharing: ConsentSharing) -> dict:
    """Until the meeting she named (her next confirmed one), for the period she named (at most 12 months), else 3 months."""
    if UNTIL_MEETING.search(text) and sharing.next_meeting:return {'until':'meeting','meeting_id':str(sharing.next_meeting.meeting_id)}
    days=DEFAULT_DAYS
    if (m:=PERIOD.search(text)):
        n=int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)]
        days=n*{'day':1,'week':7,'month':30,'year':365}[m.group(2)]
    return {'until':'date','expires_on':(sharing.today+timedelta(days=min(max(days,1),365))).isoformat()}


def scope_of(category: str,text: str) -> dict:
    if category=='transaction_insights':return {'accounts':'all','lookback_months':6}
    named=[a for a,rx in AREA_WORDS.items() if rx.search(text)]
    return {'topic_areas':named or list(AREA_WORDS)}


def day(iso: str) -> str:
    y,m,d=iso.split('-')
    return f"{int(d)} {('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')[int(m)-1]} {y}"


def grant_summary(category: str,target: dict,rm: str) -> str:
    what='every account and card, the last 6 months' if category=='transaction_insights' else \
        ', '.join(a.replace('_',' ') for a in target['scope']['topic_areas'])
    until={'date':lambda:f'until {day(target["expires_on"])}','meeting':lambda:'until your next meeting ends',
        'request':lambda:'until this meeting request is answered'}[target['until']]()
    return f'Share your {LABEL[category]} with {rm} ({what}) {until}'[:300]


class Wanted(NamedTuple):
    text: str
    deleting: bool
    category: str|None
    stopping: bool


def wanted(question: str) -> Wanted|None:
    """Her words ask for a consent change we can offer (also the planner's rule, orchestrator.fixed_task); her records
    decide the offer."""
    text=(question or '').lower().replace('’',"'")
    deleting=bool(DELETE.search(text) and re.search(r'\btopics?\b',text) and any(rx.search(text) for rx in TOPIC_WORDS.values()))
    category=category_in(text)
    stopping=bool(category and STOP.search(text))
    sharing=bool(category and not stopping and SHARE.search(text) and not QUESTION.search(text) and not MEETING.search(UNTIL_MEETING.sub('',text)))
    return Wanted(text,deleting,category,stopping) if deleting or stopping or sharing else None


async def respond(task_id: str,question: str,ctx: RequestContext) -> Reply|None:
    """A consent change she asked for in her own words, as one offer from her records; None when it is not one."""
    if (found:=wanted(question)) is None:return None
    text,deleting,category,stopping=found
    if deleting:
        mine=await talking_points.mine(ctx)
        topic=next(t for t,rx in TOPIC_WORDS.items() if rx.search(text))
        rm=mine.data.rm_first_name or 'Your relationship manager'
        point=next((p for p in mine.data.points if p.topic==topic),None)
        body={'topic':topic,'point_id':str(point.point_id) if point else None,'points':len(mine.data.points)}
        if point is None:return reply(task_id,NO_TOPIC.format(rm=rm,topic=talking_points.TOPIC_NAME[topic].lower()),mine.as_of,body)
        return reply(task_id,f'{rm} sees this topic: {talking_points.TOPIC_NAME[topic]}. If you delete it, {rm} stops seeing it at once; '
            'you can undo it on Data sharing for 7 days.',mine.as_of,body,
            [OfferProposal(task_id=task_id,agent='relationship',type='insight.delete',target={'point_id':str(point.point_id)},
                summary=f'Delete the {talking_points.TOPIC_NAME[topic].lower()} topic from what {rm} sees')])
    envelope=await customer_data.get_consent_sharing_record(ctx)
    sharing=envelope.data
    if sharing.rm is None:return reply(task_id,NO_RM,envelope.as_of,{'category':category,'rm':None})
    rm,active=display(sharing.rm.name),sharing.active(category)
    if not for_her_rm(text,rm):return None
    body={'category':category,'advisor_id':str(sharing.rm.advisor_id),'grant_id':str(active.grant_id) if active else None,
        'basis':active.basis if active else None}
    if stopping:
        if active is None or active.basis=='notice':return reply(task_id,NOT_SHARED.format(label=LABEL[category],rm=rm),envelope.as_of,body)
        return reply(task_id,CONSEQUENCE[category].format(rm=rm),envelope.as_of,body,[OfferProposal(task_id=task_id,agent='relationship',
            type='consent.revoke',target={'grant_id':str(active.grant_id)},summary=f'Stop sharing your {LABEL[category]} with {rm}')])
    if active is not None:return reply(task_id,ALREADY.format(label=LABEL[category],rm=rm),envelope.as_of,body)
    target={'category':category,'recipient':str(sharing.rm.advisor_id),'scope':scope_of(category,text),'notice_version':NOTICE[category],**end_of(text,sharing)}
    summary=grant_summary(category,target,rm)
    return reply(task_id,f'I can share this with {rm}: {summary[0].lower()+summary[1:]}. Nothing is shared until you confirm, and you can '
        'switch it off on Data sharing at any time.',envelope.as_of,body,
        [OfferProposal(task_id=task_id,agent='relationship',type='consent.grant',target=target,summary=summary)])


async def meeting_shares(task_id: str,ctx: RequestContext) -> list[OfferProposal]:
    """The optional grants a meeting request carries: each category she does not share yet, until the request is answered
    (chained after the meeting.request, 0109; a switch that is off by default, 0114). None when there is no RM."""
    try:sharing=(await customer_data.get_consent_sharing_record(ctx)).data
    except Exception:return []  # the meeting request stands without the switches
    if sharing.rm is None:return []
    rm=display(sharing.rm.name)
    return [OfferProposal(task_id=task_id,agent='relationship',type='consent.grant',chained=True,
        target={'category':category,'recipient':str(sharing.rm.advisor_id),'scope':scope_of(category,''),'notice_version':NOTICE[category],'until':'request'},
        summary=f'Also share your {LABEL[category]} with {rm} until this request is answered')
        for category in ('transaction_insights','chat_topics') if sharing.active(category) is None]
