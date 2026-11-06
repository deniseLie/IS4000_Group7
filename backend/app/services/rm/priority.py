"""The RM's priority score (build plan 3b): one source of truth for the Today page (GET /rm/priorities) and the
assistant's `today` and `priorities` answers. Pure functions over rows the RM's own SQL reads returned (rm_tickets,
rm_meeting_requests, rm_review_status or rm_followups, rm_appointments): nothing here reads, writes or calls a model,
so the model only explains an order code has already fixed.

An item's score is the sum of its parts; every part is {code, points, reason} and travels with the item:

    urgent             50  an urgent request in her own queue (the 2b *Urgent card* requests are urgent rm_handover tickets)
    collateral         40  Lombard borrowing above the lending value of its collateral (the 0116 collateral_alert milestone)
    past_target        30  a request past its target time, a review or a task past its due date
    respond_by_passed  30  a client's meeting request past its respond-by date
    deadline_14d       20  a compliance review due within 14 days (not yet past)
    meeting_2d         20  a confirmed meeting today, tomorrow or the day after
    waiting       1 a day  each full day a request or meeting request has waited for her answer, at most 10

Items: her own open requests (rm_tickets rm_owned; age counts only while submitted or in review), meeting requests
waiting for her answer, reviews due within 14 days or overdue, confirmed meetings in the next 7 days, tasks with a due
date and milestones (maturities) within 14 days (no points: listed so the day is complete; a collateral alert scores). Specialist tickets and
commitments are not scored.

Ties (equal scores) are broken in this order: the earlier date first (a request's target time, a meeting request's
respond-by date, a review's, task's or milestone's due date from midnight Singapore time, a meeting's start; no date
last), then kind (request, meeting request, review, meeting, task, milestone), then the client's name ignoring case,
then the item key. So the same records always give the same order. The first DO_FIRST items with a score above 0 are
marked do_first.
"""
from collections import Counter
from datetime import date,datetime,time,timedelta,timezone

SGT=timezone(timedelta(hours=8))
POINTS={'urgent':50,'collateral':40,'past_target':30,'respond_by_passed':30,'deadline_14d':20,'meeting_2d':20}
WAIT_CAP=10  # waiting: 1 point a day, at most 10
KINDS=('request','meeting_request','review','meeting','task','milestone')
DEADLINE_DAYS,MEETING_DAYS,WEEK,SOON_DAYS,DO_FIRST=14,2,7,14,3
WAITING=('submitted','in_review')  # waiting on the bank, as rm.ticket_json's overdue
URGENT_CARD='[Urgent card]'  # the summary prefix of rm_handover.URGENT_TOPIC requests
COLLATERAL='Lombard collateral below'  # the title prefix of rm.check_collateral's milestone (0116)


def day(d: date) -> str:return f'{d.day} {d:%b %Y}'  # rm.day's format, portable (no %-d on Windows)


def _date(value) -> date|None:
    try:return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:return None


def _at(value) -> datetime|None:
    if not value:return None
    text=str(value)
    try:at=datetime.fromisoformat(text) if len(text)>10 else datetime.combine(date.fromisoformat(text),time(),SGT)
    except ValueError:return None
    return at if at.tzinfo else at.replace(tzinfo=SGT)


def part(code: str,reason: str,points: int|None=None) -> dict:return {'code':code,'points':POINTS[code] if points is None else points,'reason':reason}


def waited(days: int) -> list[dict]:
    return [part('waiting',f"Waiting {days} day{'s' if days!=1 else ''}",min(days,WAIT_CAP))] if days>0 else []


def in_days(left: int) -> str:return 'today' if left==0 else 'tomorrow' if left==1 else f'in {left} days'


# Export values (rm/graph.py exports_for): ids, dates and codes only, never a client's words ---------------------------
def needs_answer(row: dict) -> bool:
    """A meeting request waiting for her answer (lib/rm.js needsAnswer): new, or her proposed time lapsed."""
    return row.get('status')=='requested' or (row.get('status')=='countered' and bool(row.get('proposal_lapsed')))


def requests_to_answer(rows: list[dict]) -> list[dict]:
    """The Calendar's requests_to_answer export (never the purpose, which is the client's own words)."""
    return [{'request_id':str(r['request_id']),'customer_id':r['customer_id'],'name':r.get('client'),'status':r.get('status'),
        'topics':list(r.get('topics') or []),'received':r.get('created_at'),'received_text':r.get('created_text'),'respond_by':r.get('respond_by'),
        'respond_by_text':r.get('respond_by_text'),'overdue':bool(r.get('overdue')),'proposal_lapsed':bool(r.get('proposal_lapsed'))}
        for r in rows if needs_answer(r)]


def reviews_due(rows: list[dict],end: date) -> list[dict]:
    """The Client's reviews_due/clients_due export (rm_review_status rows, or rm_followups review deadlines): due on or
    before `end`; an overdue review is still due."""
    due=[(r,_date(r.get('due_date') or r.get('due_on'))) for r in rows]
    return [{'customer_id':r['customer_id'],'name':r.get('client') or r.get('name'),'due_date':d.isoformat()} for r,d in due if d and d<=end]


def meetings_next_7d(rows: list[dict],today: date) -> list[dict]:
    """The Calendar's meetings_next_7d export: confirmed meetings not yet over, today to 7 days ahead."""
    out=[]
    for a in rows:
        on=_date(a.get('date') or a.get('starts_at'))
        if a.get('status')=='confirmed' and not a.get('past') and on and today<=on<=today+timedelta(days=WEEK):
            out.append({'appointment_id':str(a['appointment_id']),'customer_id':a['customer_id'],'name':a.get('client'),
                'starts_at':str(a['starts_at']),'date':on.isoformat(),'when':a.get('when'),'format':a.get('format')})
    return out


def key_of(followup: dict) -> str:
    """A follow-up row's item key as rank() keys it (a review deadline by client and due date)."""
    if followup.get('kind')=='review_deadline':return f"review:{followup['customer_id']}:{_date(followup.get('due_on'))}"
    return str(followup.get('item_key'))


# The score ------------------------------------------------------------------------------------------------------------
def item(kind: str,key: str,row: dict,title,on,when,parts: list[dict],**ref) -> dict:
    return {'key':key,'kind':kind,'customer_id':row.get('customer_id'),'client':row.get('client') or row.get('name'),'title':title,
        'on':str(on) if on else None,'when':when,'score':sum(p['points'] for p in parts),'parts':parts,'ref':ref}


def order(x: dict) -> tuple:
    at=_at(x['on'])
    return (-x['score'],at is None,at.timestamp() if at else 0.0,KINDS.index(x['kind']),(x['client'] or '').casefold(),x['key'])


def rank(*,today: date,now: datetime,tickets: dict|None=None,requests: list[dict]=(),reviews: list[dict]=(),meetings: list[dict]=(),
        followups: list[dict]=()) -> list[dict]:
    """Scored items, highest first (ties as in the module docstring), each with rank (1, 2, ...) and do_first.
    tickets: rm_tickets data; requests: requests_to_answer; reviews: reviews_due; meetings: meetings_next_7d;
    followups: rm_followups data (tasks and milestones only)."""
    items=[]
    for t in (tickets or {}).get('rm_owned') or []:
        parts=[part('urgent','Urgent request')] if t.get('urgency')=='urgent' else []
        if t.get('overdue'):parts.append(part('past_target',f"Past its target time ({t.get('target_sgt')})"))
        if t.get('status') in WAITING:parts+=waited(int(t.get('age_hours') or 0)//24)
        title='Urgent card' if str(t.get('summary') or '').startswith(URGENT_CARD) else t.get('category_label')
        items.append(item('request',f"ticket:{t['reference']}",t,title,t.get('target_at'),t.get('target_sgt'),parts,
            reference=t['reference'],status=t.get('status'),urgency=t.get('urgency')))
    for r in requests:
        parts=[part('respond_by_passed',f"Answer was due {r.get('respond_by_text')}")] if r.get('overdue') else []
        received=_at(r.get('received'))
        parts+=waited((now-received).days if received else 0)
        items.append(item('meeting_request',f"meeting_request:{r['request_id']}",r,'Meeting request',r.get('respond_by'),r.get('respond_by_text'),parts,
            request_id=r['request_id'],status=r.get('status'),topics=r.get('topics') or []))
    for r in reviews:
        due=_date(r.get('due_date'))
        if due is None or due>today+timedelta(days=DEADLINE_DAYS):continue
        left=(due-today).days
        parts=[part('past_target',f'Review was due {day(due)}')] if left<0 else [part('deadline_14d',f'Review due {in_days(left)} ({day(due)})')]
        items.append(item('review',f"review:{r['customer_id']}:{due.isoformat()}",r,'Compliance review',due,day(due),parts))
    for a in meetings:
        on=_date(a.get('date'))
        if on is None or not today<=on<=today+timedelta(days=WEEK):continue
        left=(on-today).days
        parts=[part('meeting_2d',f'Meeting {in_days(left)}')] if left<=MEETING_DAYS else []
        items.append(item('meeting',f"meeting:{a['appointment_id']}",a,'Meeting',a.get('starts_at'),a.get('when'),parts,
            appointment_id=str(a['appointment_id']),format=a.get('format')))
    for f in followups:
        due=_date(f.get('due_on'))
        if f.get('kind') not in ('task','milestone') or due is None or (f['kind']=='milestone' and not today<=due<=today+timedelta(days=SOON_DAYS)):continue
        parts=[part('past_target',f"Was due {f.get('due_text') or day(due)}")] if f['kind']=='task' and due<today else []
        if f['kind']=='milestone' and str(f.get('title') or '').startswith(COLLATERAL):parts.append(part('collateral','Lombard collateral below its lending value'))
        items.append(item(f['kind'],str(f['item_key']),f,f.get('title'),due,f.get('due_text') or day(due),parts,item_key=f['item_key']))
    items.sort(key=order)
    for n,x in enumerate(items,1):x.update(rank=n,do_first=n<=DO_FIRST and x['score']>0)
    return items


def facts(items: list[dict]) -> dict:
    """Span facts for a scored list: counts and factor codes only (never names, titles or reasons)."""
    return {'scored':len(items),'scored_kinds':dict(Counter(x['kind'] for x in items)),
        'factors':dict(Counter(p['code'] for x in items for p in x['parts'])),'top_score':items[0]['score'] if items else 0}
