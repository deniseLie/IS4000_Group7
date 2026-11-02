"""Booking a meeting with the relationship manager inside the chat (CR17).

1. "can i book a meeting with my rm next week" lists the RM's free 30-minute times from the records, with one-tap
   "Book <day>, <time>" replies (typing a day and time works too).
2. Choosing a time holds it for 10 minutes, exactly as the Meetings tab does (appointment_hold).
3. "Confirm booking" books the held time through the same confirm-type action the Meetings tab uses
   (appointment.confirm): her own tap is the confirmation. The meeting then shows in My requests > Meetings.
Times come only from the records, never from the model, so the answer is never withheld.
"""
import re
from datetime import date,datetime,timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
from app.core import data_api
from app.schemas.chat import Message
from app.schemas.contracts import ActionCommand,AgentResult,Citation,Receipt,RequestContext
from app.services import actions
from app.services.authorization import require_permission
from app.services.typos import normalise

SGT=ZoneInfo('Asia/Singapore')
ASK=re.compile(r'\b(?:book|schedule|set up|setup|arrange|make|fix|get)\b[^.?!]{0,40}\b(?:meeting|appointment|call|catch up|time)\b'
    r'|\b(?:meet|meeting|appointment)\b[^.?!]{0,40}\b(?:rm|relationship manager|advisor|adviser|banker|manager)\b'
    r'|\b(?:free|available)\s+(?:times?|slots?)\b|\bwhen\b[^.?!]{0,30}\b(?:rm|relationship manager|manager)\b[^.?!]{0,20}\b(?:free|available)\b',re.I)
LISTED='Tap a time below, or type a day and time, and I’ll hold it for you.'
HELD='Tap “Confirm booking” to book it.'
CONFIRM=re.compile(r'^\s*(?:yes[, ]*)?(?:confirm|book it|yes book|ok confirm)',re.I)
MONTH=r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*'
DAY=re.compile(r'\b(\d{1,2})\s*'+MONTH+r'\b|\b'+MONTH+r'\s+(\d{1,2})\b',re.I)
TIME=re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b',re.I)
# Checked in order: "video call" must be video, not a phone call.
FORMAT={'video':'video','zoom':'video','in person':'in_person','in-person':'in_person','face to face':'in_person','branch':'in_person','phone':'phone','call me':'phone'}
LABEL={'video':'video call','phone':'phone call','in_person':'in person'}


def slot_label(start: datetime) -> str:
    local=start.astimezone(SGT)
    return f'{local:%a} {local.day} {local:%b}, {local.hour%12 or 12}:{local:%M} {"am" if local.hour<12 else "pm"}'


def next_week(today: date) -> tuple[date,date]:
    monday=today+timedelta(days=7-today.weekday())
    return monday,monday+timedelta(days=4)


def chosen_format(text: str) -> str:
    return next((value for key,value in FORMAT.items() if key in text),'video')


def matching_slot(text: str,slots):
    day=DAY.search(text); time=TIME.search(text)
    if not day or not time:return None
    number=int(day.group(1) or day.group(4)); month=(day.group(2) or day.group(3))[:3].lower()
    hour=int(time.group(1))%12+(12 if time.group(3).lower()=='pm' else 0); minute=int(time.group(2) or 0)
    return next((slot for slot in slots if (local:=slot.starts_at.astimezone(SGT)).day==number and f'{local:%b}'.lower()==month
        and local.hour==hour and local.minute==minute),None)


def spread(slots,limit: int=6,per_day: int=2) -> list:
    """Up to six times across several days (two a day: one morning, one afternoon where possible), in time order."""
    days: dict[date,list]={}
    for slot in sorted(slots,key=lambda item:item.starts_at):days.setdefault(slot.starts_at.astimezone(SGT).date(),[]).append(slot)
    chosen=[]
    for day_slots in days.values():
        morning=[slot for slot in day_slots if slot.starts_at.astimezone(SGT).hour<12]
        afternoon=[slot for slot in day_slots if slot.starts_at.astimezone(SGT).hour>=12]
        picks=[*(morning[:1]),*(afternoon[:1])] or day_slots[:1]
        chosen+=[*picks,*[slot for slot in day_slots if slot not in picks]][:per_day]
    if len(chosen)<limit:chosen+=[slot for slot in sorted(slots,key=lambda item:item.starts_at) if slot not in chosen][:limit-len(chosen)]
    return sorted(chosen[:limit],key=lambda item:item.starts_at)


NEXT_DAY = re.compile(r"\b(?:next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",re.I)
WEEKDAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
MORE=re.compile(r'\b(?:other|another|more|different|later|earlier)\b[^.?!]{0,20}\b(?:times?|slots?|days?|dates?)\b|\bnone of (?:these|those|them)\b',re.I)


def replying(text: str) -> bool:
    """Her message is about the times we offered: a day or time, a meeting format, a confirmation or other times."""
    return bool(DAY.search(text) or TIME.search(text) or NEXT_DAY.search(text) or CONFIRM.search(text) or MORE.search(text)
        or any(key in text for key in FORMAT))


def awaiting(history: list[Message]) -> bool:
    """Only replies about the offered times come back here; any other question goes to its usual place."""
    return (len(history)>=2 and history[-1].role=='user' and history[-2].role=='assistant'
        and (LISTED in history[-2].content or HELD in history[-2].content) and replying(normalise(history[-1].content)))


def asked_format(history: list[Message]) -> str | None:
    """The format she chose earlier in this booking ("in person"), carried into the hold."""
    for item in reversed(history[-6:]):
        found=next((value for key,value in FORMAT.items() if key in normalise(item.content)),None) if item.role=='user' else None
        if found:return found
        if item.role=='assistant' and (match:=re.search(r'It will be an? (in-person|phone|video) meeting',item.content)):
            return {'in-person':'in_person','phone':'phone','video':'video'}[match.group(1)]
    return None


def result(task_id: str,text: str,source: str='relationship-slots') -> AgentResult:
    return AgentResult(task_id=task_id,agent='relationship',status='ok',answer_kind='banking',text=text,
        citations=[Citation(source_id=source)])


async def hold(slot,meeting_format: str,ctx: RequestContext) -> None:
    current=ctx.model_copy(update={'agent':None})
    require_permission(current.principal,'appointment.hold',{'customer_id':current.principal.customer_id},current)
    target={'advisor_id':str(slot.advisor_id),'starts_at':slot.starts_at.isoformat(),'ends_at':slot.ends_at.isoformat(),'format':meeting_format}
    with data_api.connect(current) as db:
        await data_api.execute(lambda:db.rpc('appointment_hold',{'p_command_id':str(uuid4()),'p_session_id':str(uuid4()),
            'p_case_id':str(uuid4()),'p_target':target}).execute(),'appointment.hold')


async def confirm(appointment,ctx: RequestContext) -> bool:
    current=ctx.model_copy(update={'agent':None,'case_id':appointment.case_id})
    task=f'chat_{uuid4().hex[:12]}'
    command=ActionCommand(command_id=uuid4(),type='appointment.confirm',target={'appointment_id':str(appointment.appointment_id)},
        idempotency_key=f'{appointment.case_id}:{task}:appointment.confirm')
    await actions.run_action(command,current,False)
    receipt=await actions.run_action(command,current,True)
    return isinstance(receipt,Receipt) and receipt.outcome=='succeeded'


async def respond(task_id: str,question: str,history: list[Message],records,ctx: RequestContext) -> AgentResult | None:
    latest=history[-1].content if history and history[-1].role=='user' else question
    text=normalise(latest)
    now=ctx.received_at
    slots=[slot for slot in records.slots.data if slot.starts_at>now]
    previous=history[-2].content if awaiting(history) else ''
    if HELD in previous and CONFIRM.search(text):
        held=[item for item in records.appointments.data if item.status=='held' and item.case_id is not None and item.starts_at>now]
        if not held:return result(task_id,'That hold has expired, so nothing was booked. Ask me for free times and pick one again.')
        item=max(held,key=lambda row:row.hold_expires_at)
        if not await confirm(item,ctx):return result(task_id,'That time was taken before I could book it. Ask me for free times and pick another.')
        return result(task_id,f'Booked: {slot_label(item.starts_at)} with your relationship manager ({LABEL[item.format]}). '
            'You can see or cancel it in My requests > Meetings.','relationship-appointments')
    if previous or ASK.search(text):
        slot=matching_slot(text,slots)
        meeting_format=next((value for key,value in FORMAT.items() if key in text),None) or asked_format(history) or 'video'
        if slot is not None:
            await hold(slot,meeting_format,ctx)
            return result(task_id,f'I’ve held {slot_label(slot.starts_at)} ({LABEL[meeting_format]}) with your relationship manager '
                f'for 10 minutes. {HELD} You can also confirm it in My requests > Meetings.')
        shown=slots
        if previous and MORE.search(text):shown=[slot for slot in slots if slot_label(slot.starts_at) not in previous] or slots
        day_match=NEXT_DAY.search(text)
        if day_match:
            today=now.astimezone(SGT).date()
            delta=(WEEKDAYS.index(day_match.group(1).lower())-today.weekday())%7 or 7
            target=today+timedelta(days=delta)
            shown=[s for s in slots if s.starts_at.astimezone(SGT).date()==target]
            if not shown:
                return result(task_id,f'I cannot find a free RM meeting slot for {target:%d %B %Y} in the available schedule. You can ask for other available times.')
        if 'next week' in text:
            start,end=next_week(now.astimezone(SGT).date())
            shown=[slot for slot in slots if start<=slot.starts_at.astimezone(SGT).date()<=end] or slots
        if not shown:return result(task_id,'Your relationship manager has no free times in the next two weeks. '
            'You can ask them to call you instead: say “ask my relationship manager to call me”.')
        lines='\n'.join(f'• {slot_label(slot.starts_at)}' for slot in spread(shown))
        lead='Here are free 30-minute times with your relationship manager (Singapore time):'
        if previous and (DAY.search(text) or TIME.search(text)):lead='That time isn’t free. '+lead
        elif previous and MORE.search(text):lead='Here are more free times with your relationship manager (Singapore time):'
        if meeting_format=='video':
            tail='Meetings are by video call unless you say “phone” or “in person”.'
        else:
            noun={'in_person':'an in-person','phone':'a phone'}[meeting_format]
            lead=f'Sure, {LABEL[meeting_format]}. '+lead if previous and not (DAY.search(text) or TIME.search(text)) else lead
            tail=f'It will be {noun} meeting.'
        return result(task_id,f'{lead}\n{lines}\n{LISTED} {tail}')
    return None
