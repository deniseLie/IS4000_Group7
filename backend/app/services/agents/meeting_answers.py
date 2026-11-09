"""Meeting requests in the chat (RM copilot build plan 2a; decision 1: requests only, the chat never books directly).

1. "can I meet Sophie next week about a home loan?" drafts a meeting request from the records: the purpose is her own
   redacted words (at most 200 characters), the topics come from a fixed word list (TOPIC_WORDS, never model text;
   `other` when nothing matches), the format is video unless she says phone or in person, and up to 3 preferred times
   are her RM's free slots: the time she named when it is free, otherwise spread-out free times in the window she named
   (a day, "next week", "afternoon"), otherwise the next free days.
2. The reply shows the draft and ends with one meeting.request offer (services/offers.py): nothing is sent until she
   confirms by button or a typed yes (orchestrator.act). Her RM then accepts a time, proposes another or declines.
3. A reply to the draft ("Thursday afternoon", "by phone", "it's about my fixed deposit", "other times") redrafts it;
   an availability question ("when is my RM free?") lists free times first and asks what the meeting is about.
Times come only from the records, never from the model, so the answer is never withheld. Confirmed meetings show in
the Meetings page.
"""
import re
from datetime import date,datetime,timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo
from app.schemas.chat import Message
from app.schemas.contracts import AgentResult,Citation,OfferProposal,RequestContext
from app.schemas.meeting_requests import FORMAT_LABEL,TOPIC_LABEL,MeetingRequestRecord
from app.services import tools
from app.services.privacy import redact
from app.services.typos import normalise

SGT=ZoneInfo('Asia/Singapore')
MAX_OPEN=3  # open requests per client (actions.execute_meeting_request, 0106)
LEAD=timedelta(hours=2)  # SQL refuses times within an hour (rm.slot_problem); the offer may wait 30 minutes for her yes
MEET=(r'\bmeet(?:\s+up)?\s+(?:with\s+)?(?!(?:the|your|all|these|those|this|that|it|its|our|any|requirements?|criteria|deadlines?'
    r'|targets?|conditions?|minimum|expectations?)\b)[a-z]+')
ASK=re.compile(r'\b(?:book|schedule|set up|setup|arrange|make|fix|get|request|want|like|need)\b[^.?!]{0,40}\b(?:meeting|appointment|(?:a|phone|video)\s+call|catch[- ]up)\b'
    r'|\b(?:meet|meeting|appointment|(?:a|phone|video)\s+call|catch[- ]?up)\b[^.?!]{0,40}\b(?:rm|relationship manager|advisor|adviser|banker|manager)\b|'+MEET,re.I)
AVAILABILITY=re.compile(r'\b(?:free|available)\s+(?:times?|slots?)\b|\bavailability\b'
    r'|\bwhen\b[^.?!]{0,30}\b(?:rm|relationship manager|manager|advisor|adviser|banker)\b[^.?!]{0,20}\b(?:free|available)\b',re.I)
# Questions about meetings she already has stay with her appointment records (intent `appointments`), never a new request.
EXISTING=re.compile(r'\b(?:my|the)\s+(?:next|upcoming|current|existing|booked|confirmed|scheduled)\s+(?:meeting|appointment)s?\b'
    r'|\bwhen\s+is\s+my\s+(?:meeting|appointment)\b|\b(?:do|did)\s+i\s+have\s+(?:a|any)\s+(?:meeting|appointment)s?\b|\bwhen\s+am\s+i\s+meeting\b|\b(?:cancel|move|reschedule|rearrange|postpone)\b[^.?!]{0,30}\b(?:meeting|appointment)s?\b'
    r'|\b(?:meeting|appointment)\b[^.?!]{0,20}\b(?:cancel(?:led)?|moved?|rescheduled?|postponed?)\b',re.I)
# Fixed topic codes (rm.meeting_topics(), 0106) from her words, in display order. A loan question adds term_sheet.
TOPIC_WORDS: tuple[tuple[str,re.Pattern[str]],...]=(
    ('home_loan',re.compile(r'\b(?:home|housing|hdb|condo|house|flat|apartment)\s+(?:loans?|financing)\b|\bmortgages?\b|\brefinanc\w*|\bfinancing\b'
        r'|\bproperty\b|\b(?:buy(?:ing)?|purchas\w+)\s+(?:a\s+|an\s+|my\s+)?(?:new\s+)?(?:home|house|flat|condo|apartment|hdb)\b',re.I)),
    ('term_sheet',re.compile(r'\b(?:loans?|mortgages?|borrow(?:ing)?|refinanc\w*|term\s+sheets?)\b',re.I)),
    ('investments',re.compile(r'\binvest\w*|\bunit\s+trusts?\b|\b(?:mutual|bond|index|equity)\s+funds?\b|\bstocks?\b|\bshares\b|\bbonds?\b'
        r'|\bportfolio\b|\bwealth\b|\bretirement\b|\betfs?\b|\blombard\b|\bborrow(?:ing)?\s+against\b',re.I)),
    ('fixed_deposit',re.compile(r'\b(?:fixed|time)\s+deposits?\b|\bFDs?\b',re.I)),
    ('insurance',re.compile(r'\b(?:insurance|insure[ds]?|life\s+cover|coverage)\b',re.I)),
    ('compliance_review',re.compile(r'\b(?:compliance|kyc)\b|\bmy\s+review\b|\breview\s+(?:documents?|deadline)\b|\btax\s+residency\b',re.I)),
    ('card_review',re.compile(r'\b(?:credit\s+)?cards?\b|\bcredit\s+limit\b',re.I)),
    ('account_review',re.compile(r'\baccounts?\b|\bsavings\b|\bstatements?\b',re.I)))
FORMAT={'video':'video','zoom':'video','teams':'video','in person':'in_person','in-person':'in_person','face to face':'in_person',
    'branch':'in_person','office':'in_person','phone':'phone','call me':'phone'}  # checked in order: "video call" is video
MONTH=r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*'
DAY=re.compile(r'\b(\d{1,2})\s*'+MONTH+r'\b|\b'+MONTH+r'\s+(\d{1,2})\b',re.I)
TIME=re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b',re.I)
WEEKDAY=re.compile(r'\b(monday|tuesday|wednesday|thursday|friday|mon|tues?|wed|thur?s?|fri)\b',re.I)
PART=re.compile(r'\b(morning|afternoon|evening|lunch(?:time)?)\b',re.I)
MORE=re.compile(r'\b(?:other|another|more|different|later|earlier)\b[^.?!]{0,20}\b(?:times?|slots?|days?|dates?)\b|\bnone of (?:these|those|them)\b',re.I)
ABOUT=re.compile(r'^\s*(?:it[’\']?s\s+|it\s+is\s+|this\s+is\s+)?(?:about|regarding|re|for|to discuss)\b',re.I)
# Fixed wording; the markers let a reply to it come back here (awaiting) and let a redraft read the draft it changes.
DRAFTED='To change anything, just tell me'
LISTED='Tell me which times suit you and what you’d like to discuss'
SUMMARY='Send this meeting request to your relationship manager'
NO_RM=('You don’t have a relationship manager recorded right now, so I can’t send a meeting request. '
    'Use Talk to a person and someone will help you.')
NO_TIMES=('Your relationship manager has no free times in the next two weeks, so I can’t suggest times for a meeting request. '
    'Please ask again in a few days, or use Talk to a person.')
TOO_MANY=('You already have {count} open meeting requests with your relationship manager, which is the most at one time. '
    'You can follow or withdraw them in the Meetings page.')


class Reply(NamedTuple):
    result: AgentResult
    proposal: OfferProposal | None=None


def slot_label(start: datetime) -> str:
    local=start.astimezone(SGT)
    return f'{local:%a} {local.day} {local:%b}, {local.hour%12 or 12}:{local:%M} {"am" if local.hour<12 else "pm"}'


def local(slot) -> datetime:return slot.starts_at.astimezone(SGT)


def next_week(today: date) -> tuple[date,date]:
    monday=today+timedelta(days=7-today.weekday())
    return monday,monday+timedelta(days=4)


def chosen_format(text: str) -> str | None:
    return next((value for key,value in FORMAT.items() if re.search(rf'\b{re.escape(key)}\b',text,re.I)),None)


def topics_for(text: str) -> list[str]:
    """The fixed topic codes her words name, in display order (at most 5); `other` when none does."""
    found=[code for code,pattern in TOPIC_WORDS if pattern.search(text)]
    return found[:5] or ['other']


def clock(text: str) -> tuple[int,int] | None:
    found=TIME.search(text)
    if not found:return None
    if found.group(3):return int(found.group(1))%12+(12 if found.group(3).lower()=='pm' else 0),int(found.group(2) or 0)
    return int(found.group(4)),int(found.group(5))


def matching_slot(text: str,slots):
    """The free slot at the day and time she typed ("Mon 2 Nov, 10:00 am", "2 nov 10am"), or None."""
    day=DAY.search(text);at=clock(text)
    if not day or not at:return None
    number=int(day.group(1) or day.group(4));month=(day.group(2) or day.group(3))[:3].lower()
    return next((slot for slot in slots if (when:=local(slot)).day==number and f'{when:%b}'.lower()==month
        and (when.hour,when.minute)==at),None)


def spread(slots,limit: int=3,per_day: int=1) -> list:
    """Up to `limit` times across days (mornings and afternoons in turn), in time order."""
    days: dict[date,list]={}
    for slot in sorted(slots,key=lambda item:item.starts_at):days.setdefault(local(slot).date(),[]).append(slot)
    chosen=[]
    for i,day_slots in enumerate(days.values()):
        morning=[s for s in day_slots if local(s).hour<12];afternoon=[s for s in day_slots if local(s).hour>=12]
        first=(afternoon+morning) if i%2 else (morning+afternoon)
        chosen+=first[:per_day] if per_day==1 else [*morning[:1],*afternoon[:1],*[s for s in day_slots if s not in morning[:1]+afternoon[:1]]][:per_day]
    chosen+=[s for s in sorted(slots,key=lambda item:item.starts_at) if s not in chosen][:max(0,limit-len(chosen))]
    return sorted(chosen[:limit],key=lambda item:item.starts_at)


def window(text: str,slots,today: date) -> tuple[list,bool]:
    """The free slots in the day, week, part of day and time she named, and whether she named any of them."""
    pool,named=list(slots),False
    def keep(test) -> None:
        nonlocal pool,named
        named=True;pool=[s for s in pool if test(local(s))]
    if found:=DAY.search(text):
        number=int(found.group(1) or found.group(4));month=(found.group(2) or found.group(3))[:3].lower()
        keep(lambda when:when.day==number and f'{when:%b}'.lower()==month)
    elif found:=WEEKDAY.search(text):
        wanted=['mon','tue','wed','thu','fri'].index(found.group(1)[:3].lower())
        target=today+timedelta(days=(wanted-today.weekday())%7 or 7)
        if re.search(r'\bnext\s+week\b',text,re.I) and target<next_week(today)[0]:target+=timedelta(days=7)
        keep(lambda when:when.date()==target)
    elif re.search(r'\btomorrow\b',text,re.I):keep(lambda when:when.date()==today+timedelta(days=1))
    elif re.search(r'\bnext\s+week\b',text,re.I):
        start,end=next_week(today);keep(lambda when:start<=when.date()<=end)
    elif re.search(r'\bthis\s+week\b',text,re.I):keep(lambda when:when.date()<=today+timedelta(days=6-today.weekday()))
    if found:=PART.search(text):
        part=found.group(1).lower()
        keep(lambda when:when.hour<12 if part=='morning' else 12<=when.hour<14 if part.startswith('lunch') else when.hour>=12 if part=='afternoon' else when.hour>=17)
    if (at:=clock(text)) is not None:keep(lambda when:(when.hour,when.minute)==at)
    return pool,named


def times_for(text: str,slots,today: date,shown: list[str]) -> tuple[list,str]:
    """Up to 3 preferred times from the free slots, and a note when what she named is not free."""
    if exact:=matching_slot(text,slots):return [exact],''
    usable=[s for s in slots if slot_label(s.starts_at) not in shown] if MORE.search(text) and shown else slots
    if shown and not MORE.search(text) and not (DAY.search(text) or WEEKDAY.search(text) or TIME.search(text) or PART.search(text)
            or re.search(r'\b(?:tomorrow|next week|this week)\b',text,re.I)):
        kept=[s for s in slots if slot_label(s.starts_at) in shown]  # only the format or the purpose changed
        if kept:return kept[:3],''
    pool,named=window(text,usable,today)
    if pool:return ([pool[0]] if clock(text) is not None and len(pool)==1 else spread(pool)),''
    return spread(usable or slots),'That time isn’t free with your relationship manager, so I’ve picked other free times.' if named else ''


def awaiting_reply(text: str) -> bool:
    """Her message is about the draft or the times we listed: a day or time, a format, other times or what it is about
    ("it's about my fixed deposit"). A topic word alone ("whats my card balance") is a new question, not a reply."""
    return bool(DAY.search(text) or TIME.search(text) or WEEKDAY.search(text) or PART.search(text) or MORE.search(text) or ABOUT.search(text)
        or re.search(r'\b(?:tomorrow|next week|this week)\b',text,re.I) or chosen_format(text))


def ours(message: Message) -> bool:
    return message.role=='assistant' and (DRAFTED in message.content or LISTED in message.content)


def awaiting(history: list[Message]) -> bool:
    """Only replies about our draft or listed times come back here; any other question goes to its usual place."""
    return (len(history)>=2 and history[-1].role=='user' and ours(history[-2]) and not EXISTING.search(history[-1].content)
        and awaiting_reply(normalise(history[-1].content)))


def thread_start(history: list[Message]) -> str:
    """The question that started this meeting conversation (the user message before our first meeting reply)."""
    i=len(history)-1
    while i>=2 and ours(history[i-1]) and history[i-2].role=='user':i-=2
    return history[i].content if 0<=i<len(history) else ''


def draft_of(text: str) -> dict:
    """What our previous draft said (our own fixed wording): purpose, format and times."""
    purpose=re.search(r'^• What it’s about: “(.*)”$',text,re.M)
    shown=re.search(r'^• Times that suit you \(Singapore time\): (.*)$',text,re.M)
    fmt=re.search(r'^• Format: (.*)$',text,re.M)
    return {'purpose':purpose.group(1) if purpose else None,'times':shown.group(1).split('; ') if shown else re.findall(r'^• (\w{3} \d{1,2} \w{3}, \d{1,2}:\d{2} [ap]m)$',text,re.M),
        'format':next((key for key,label in FORMAT_LABEL.items() if fmt and fmt.group(1)==label),None)}


def purpose_text(text: str) -> str:
    cleaned=' '.join(redact(text)[0].split())
    return cleaned if len(cleaned)<=200 else cleaned[:199].rsplit(' ',1)[0].rstrip(' ,.;:')+'…'


def result(task_id: str,text: str,sources: tuple[str,...]=('relationship-slots',)) -> AgentResult:
    return AgentResult(task_id=task_id,agent='relationship',status='ok',answer_kind='banking' if sources else 'conversation',text=text,
        citations=[Citation(source_id=s) for s in sources])


def display(name: str) -> str:return re.sub(r'\s*\(DEMO\)\s*$','',name)


async def open_requests(ctx: RequestContext) -> int:
    envelope=await tools.snapshot('my_meeting_requests',{},ctx,list[MeetingRequestRecord])
    return sum(row.status in ('requested','countered') for row in envelope.data)


async def respond(task_id: str,question: str,history: list[Message],records,ctx: RequestContext,intent: str | None=None) -> Reply | None:
    latest=history[-1].content if history and history[-1].role=='user' else question
    text=normalise(latest)
    previous=history[-2].content if awaiting(history) else ''
    if EXISTING.search(text) and not previous:return None  # her booked meetings: the relationship answer from records
    asked=intent=='meeting_request' or bool(ASK.search(text) or ASK.search(latest))
    if not (previous or asked or AVAILABILITY.search(text)):return None
    current=records.assignment.data.current if records.assignment.data else None
    if current is None:return Reply(result(task_id,NO_RM,('relationship-assignment',)))
    today=ctx.received_at.astimezone(SGT).date()
    slots=sorted((s for s in records.slots.data if s.advisor_id==current.rm_id and s.starts_at>ctx.received_at+LEAD),key=lambda s:s.starts_at)
    if not slots:return Reply(result(task_id,NO_TIMES))
    before=draft_of(previous) if previous else {'purpose':None,'times':[],'format':None}
    if not previous and not asked:  # "when is my RM free?": the free times first, then what it is about
        pool,_=window(text,slots,today)
        lines='\n'.join(f'• {slot_label(s.starts_at)}' for s in spread(pool or slots,limit=6,per_day=2))
        return Reply(result(task_id,f'Here are free 30-minute times with your relationship manager (Singapore time):\n{lines}\n'
            f'{LISTED}, and I’ll prepare a meeting request for you to confirm.'))
    if (count:=await open_requests(ctx))>=MAX_OPEN:return Reply(result(task_id,TOO_MANY.format(count=count),()))
    own=latest if not previous or topics_for(latest)!=['other'] else before['purpose'] or thread_start(history)
    purpose=purpose_text(own or question)
    chosen,note=times_for(text,slots,today,before['times'])
    meeting_format=chosen_format(text) or before['format'] or 'video'
    topics=topics_for(purpose)
    times='; '.join(slot_label(s.starts_at) for s in chosen)
    lead=f'{note} ' if note else ''
    text=(f'{lead}I can send your relationship manager, {display(current.name)}, this meeting request:\n'
        f'• What it’s about: “{purpose}”\n• Topics: {", ".join(TOPIC_LABEL[t] for t in topics)}\n'
        f'• Format: {FORMAT_LABEL[meeting_format]}\n• Times that suit you (Singapore time): {times}\n'
        f'They accept one of these times, suggest another or reply in a message, usually within one business day. '
        f'{DRAFTED} (for example “Thursday afternoon”, “by phone” or “other times”).')
    target={'advisor_id':str(current.rm_id),'purpose':purpose,'topics':topics,'format':meeting_format,
        'preferred_times':[s.starts_at.isoformat() for s in chosen]}
    return Reply(result(task_id,text,('relationship-slots','relationship-assignment')),
        OfferProposal(task_id=task_id,agent='relationship',type='meeting.request',target=target,summary=SUMMARY))
