"""Turn RM records into cited evidence for the RM assistant, and the matching source list the workspace shows.

Each evidence item has a short stable id the model must cite (for example note-2025-03-12, ticket-TCK-001004,
shared-note), a title, the kind of record and its as-of time, plus the record itself as JSON. Dates and times are already
in Singapore time words ("Tue 3 Nov 2026, 10:00 am"), so the model never converts time zones and the checker can compare
dates. Restricted specialist detail never reaches this module: the SQL functions withhold it.
"""
import json
import re
from dataclasses import dataclass,field
from datetime import date,datetime,timedelta,timezone
from collections.abc import Awaitable,Callable
from typing import Any
from pydantic import TypeAdapter,ValidationError
from app.schemas.knowledge import KnowledgeSection
from app.schemas.meetings import TalkingPoint


@dataclass
class Evidence:
    id: str
    title: str
    kind: str  # record | client_stated | note | ticket | guidance | calendar | consent
    data: Any
    as_of: str|None=None
    customer_id: int|None=None

    @property
    def text(self) -> str:return json.dumps(self.data,ensure_ascii=False,default=str)

    def for_model(self) -> dict:return {'id':self.id,'title':self.title,'kind':self.kind,'as_of':self.as_of,'record':self.data}

    def for_ui(self) -> dict:return {'id':self.id,'title':self.title,'kind':self.kind,'as_of':self.as_of,'record':self.data}


@dataclass
class Bundle:
    items: list[Evidence]=field(default_factory=list)
    notes: dict[str,str]=field(default_factory=dict)  # evidence id -> note_id, so the UI can open the note

    def add(self,item: Evidence|None):
        if item is not None and item.id not in {e.id for e in self.items}:self.items.append(item)

    def ids(self) -> list[str]:return [e.id for e in self.items]
    def get(self,source_id: str) -> Evidence|None:return next((e for e in self.items if e.id==source_id),None)
    def for_model(self) -> list[dict]:return [e.for_model() for e in self.items]


SGT=timezone(timedelta(hours=8))


def _strip(record: dict,*keys: str) -> dict:return {k:v for k,v in record.items() if k not in keys}


def note_ids(notes: list[dict]) -> list[str]:
    """note-YYYY-MM-DD, with -2, -3 for a second note on the same day."""
    seen: dict[str,int]={}
    result=[]
    for note in notes:
        base='note-'+str(note['met_on'])
        seen[base]=seen.get(base,0)+1
        result.append(base if seen[base]==1 else f'{base}-{seen[base]}')
    return result


def add_notes(bundle: Bundle,notes: list[dict],as_of: str|None,customer_id: int|None,withheld: int=0):
    for source_id,note in zip(note_ids(notes),notes):
        bundle.notes[source_id]=str(note['note_id'])
        who='your own note' if note.get('written_by_current_rm') else f"written by {note.get('author')} (previous relationship manager)"
        bundle.add(Evidence(source_id,f"Meeting note {note.get('met_on_text')} ({note.get('format','').replace('_',' ')}), {who}",'note',
            _strip(note,'note_id','has_source_notes'),as_of,customer_id))
    if withheld:
        bundle.add(Evidence('notes-withheld','Private notes not shared with you','record',
            {'private_notes_by_other_rms':withheld,'meaning':'These notes exist but their author kept them private; their content is not available.'},as_of,customer_id))


def ticket_evidence(ticket: dict,as_of: str|None) -> Evidence:
    owner='your request' if ticket.get('rm_owned') else f"specialist-managed ({ticket.get('owner_team')}, read-only)"
    data=_strip(ticket,'customer_id','created_at','target_at')
    if ticket.get('restricted') and not ticket.get('rm_owned'):data['summary']='[restricted to the specialist team]'
    return Evidence('ticket-'+ticket['reference'],f"{ticket.get('category_label')} {ticket['reference']} for {ticket.get('client')}: {owner}",'ticket',
        data,as_of,ticket.get('customer_id'))


def client_bundle(result: dict) -> Bundle:
    """RM2: the client 360 overview from rm_client_profile."""
    data,as_of=result['data'],str(result.get('as_of'))
    cid=data['profile']['customer_id']
    bundle=Bundle()
    rel=data.get('relationship') or {}
    bundle.add(Evidence('profile',f"Client profile: {data['profile']['name']}",'record',
        {**data['profile'],'relationship':rel,'today':data.get('today_text'),'missing_information':data.get('gaps',[])},as_of,cid))
    for key,title in (('accounts','Accounts'),('cards','Credit cards'),('loans','Loans')):
        bundle.add(Evidence(key,f"{title} ({len(data.get(key) or [])} recorded)",'record',data.get(key) or [],as_of,cid))
    bundle.add(Evidence('compliance','Open compliance review','record',data.get('compliance') or {'open_review':None},as_of,cid))
    bundle.add(Evidence('preferences','Preferences the client stated','client_stated',data.get('preferences') or [],as_of,cid))
    bundle.add(Evidence('milestones','Upcoming maturities and reviews (next 120 days)','record',data.get('milestones') or [],as_of,cid))
    bundle.add(Evidence('appointments','Upcoming appointments with you','calendar',data.get('appointments') or [],as_of,cid))
    bundle.add(Evidence('rm-requests','Requests the client raised with a relationship manager','record',data.get('rm_requests') or [],as_of,cid))
    bundle.add(Evidence('tasks','Your open follow-up tasks for this client','record',data.get('tasks') or [],as_of,cid))
    for ticket in data.get('tickets') or []:bundle.add(ticket_evidence(ticket,as_of))
    add_notes(bundle,data.get('interactions') or [],as_of,cid)
    return bundle


def shared_evidence(result: dict|None,customer_id: int) -> Evidence:
    """R04/R05: the handover note is evidence only under active consent; otherwise only the consent status is."""
    if result is None:return Evidence('consent-status','Client handover note','consent',{'consent_active':False,'note':'Not checked'},None,customer_id)
    data,as_of=result['data'],str(result.get('as_of'))
    if data.get('consent_active') and data.get('shared'):
        return Evidence('shared-note','Handover note the client shared with you (under active consent)','client_stated',
            {'notes':[_strip(item,'summary_id','shared_at') for item in data['shared']],
             'excluded_notes_without_active_consent':(data.get('excluded_revoked') or 0)+(data.get('excluded_expired') or 0)},as_of,customer_id)
    return Evidence('consent-status','Client handover note','consent',{'consent_active':bool(data.get('consent_active')),
        'shared_notes_available':0,'excluded_notes_without_active_consent':(data.get('excluded_revoked') or 0)+(data.get('excluded_expired') or 0),
        'meaning':'The client has not shared a handover note with you under active consent; nothing from such a note may be used.'},as_of,customer_id)


async def category_guidance(result: dict,read: Callable[[str,dict],Awaitable[dict]]) -> list[KnowledgeSection]:
    """The approved article a ticket's request type points to (category_info.kb_article), read with the agent's own
    tool call (`knowledge_article`, Turn.read), so the Service Desk spec's allow-list and audit apply."""
    article_id=(result['data'].get('category_info') or {}).get('kb_article')
    if not article_id:return []
    return TypeAdapter(list[KnowledgeSection]).validate_python((await read('knowledge_article',{'article_id':article_id}))['items'])


def ticket_detail_bundle(result: dict,guidance: list[KnowledgeSection] | None=None) -> Bundle:
    """RM8: one ticket, what the client was told, its context and the category rules (who decides, which fields)."""
    data,as_of=result['data'],str(result.get('as_of'))
    bundle=Bundle()
    ref,cid=data['reference'],data.get('customer_id')
    bundle.add(ticket_evidence({k:v for k,v in data.items() if k in ('reference','customer_id','client','category','category_label','owner_team',
        'rm_owned','restricted','urgency','status','client_safe_status','source','created_sgt','age_hours','target_sgt','overdue','summary','details','attachments')},as_of))
    bundle.add(Evidence('told-'+ref,f'What the client was told about {ref}','record',{'by_the_bank':data.get('told_by_bank') or [],
        'by_the_chat_assistant':data.get('told_by_assistant') or [],'by_a_relationship_manager':data.get('sent_by_rm') or []},as_of,cid))
    if data.get('rm_owned'):
        bundle.add(Evidence('context-'+ref,f'Handover context for {ref} (internal)','record',{'handover_summary':data.get('handover_context'),
            'client_messages_in_chat':data.get('client_messages') or [],'internal_notes':data.get('internal_notes') or [],
            'request_to_rm':data.get('rm_request')},as_of,cid))
    info=data.get('category_info') or {}
    bundle.add(Evidence('category-'+data['category'],f"Request type rules: {info.get('label')}",'guidance',info,as_of,cid))
    if guidance:
        first,version=guidance[0],f'v{guidance[0].version}'
        bundle.add(Evidence('kb-'+first.article_id,f'Approved guidance: {first.title}','guidance',
            {'content':' '.join(item.body for item in guidance),'version':version},version,cid))
    return bundle


def tickets_bundle(result: dict) -> Bundle:
    bundle=Bundle()
    as_of=str(result.get('as_of'))
    for ticket in result['data']['rm_owned']+result['data']['specialist']:bundle.add(ticket_evidence(ticket,as_of))
    bundle.add(Evidence('inbox-summary','Ticket counts','record',{'rm_owned_open':len(result['data']['rm_owned']),
        'specialist_open':len(result['data']['specialist']),'ordering_rule':'urgent first, then past target time, then urgency, then age'},as_of))
    return bundle


def followups_bundle(result: dict,appointments: dict|None) -> Bundle:
    bundle=Bundle()
    as_of=str(result.get('as_of'))
    for index,item in enumerate(result['data'],1):
        bundle.add(Evidence(f'followup-{index}',f"{item['client']}: {item['title']}",'record',_strip(item,'customer_id'),as_of,item.get('customer_id')))
    if appointments is not None:bundle.add(appointments_evidence(appointments))
    return bundle


def appointments_evidence(result: dict,customer_id: int|None=None) -> Evidence:
    rows=[_strip(a,'starts_at','ends_at') for a in result['data'] if customer_id is None or a['customer_id']==customer_id]
    return Evidence('appointments','Confirmed and held appointments (held times are not confirmed)','calendar',rows,str(result.get('as_of')),customer_id)


def reviews_bundle(result: dict) -> Bundle:
    bundle=Bundle()
    for row in result['data']:
        bundle.add(Evidence(f"review-{row['customer_id']}",f"Compliance review: {row['client']}",'record',_strip(row,'customer_id'),str(result.get('as_of')),row['customer_id']))
    if not result['data']:bundle.add(Evidence('reviews-none','Open compliance reviews','record',{'open_reviews':0},str(result.get('as_of'))))
    return bundle


def handover_bundle(result: dict,shared: bool=True) -> Bundle:
    """RM16: the client overview plus all permitted notes, the consent-checked handover note and what the client was told.
    shared=False (R23): the client's note is shown in its own section after this one, so it is left out here."""
    data,as_of=result['data'],str(result.get('as_of'))
    bundle=client_bundle({'data':{**data,'interactions':[]},'as_of':as_of})
    cid=data['profile']['customer_id']
    add_notes(bundle,data.get('notes') or [],as_of,cid,data.get('withheld_private_notes') or 0)
    if shared:bundle.add(shared_evidence({'data':data.get('shared') or {},'as_of':as_of},cid))
    else:bundle.add(Evidence('consent-status','Client handover note','consent',{'meaning':'Checked separately: any handover note the client '
        'shared under active consent is shown in its own section of this reply. Do not mention or summarise it here.'},as_of,cid))
    bundle.add(Evidence('told','What the client was previously told (bank, chat assistant, relationship managers)','record',data.get('told') or [],as_of,cid))
    bundle.add(Evidence('free-slots','Your free times in the next 14 days (not held or booked)','calendar',data.get('free_slots') or [],as_of,cid))
    return bundle


CATEGORY_LABEL={'transaction_insights':'transaction insights','chat_topics':'topics from their chats','meeting_recording':'meeting recordings'}


def not_shared(result: dict,source_id: str,title: str,customer_id: int) -> Evidence|None:
    """A consent-gated read (0106) the client has not shared: a gap the model must state, never an empty record.
    None when the read was shared."""
    if result.get('shared') is not False:return None
    label=CATEGORY_LABEL.get(result.get('category'),'this information')
    return Evidence(source_id,title,'consent',{'shared':False,'category':result.get('category'),
        'meaning':f'The client has not shared {label} with you. Say so as a gap; nothing about it may be used or guessed.'},
        str(result.get('as_of')),customer_id)


def cash_evidence(result: dict,customer_id: int) -> Evidence:
    """Large money movements (10,000 or more in the last 180 days) as recorded, for conversation starters. No interpretation.
    Gated by the client's transaction_insights consent since 0106."""
    gap=not_shared(result,'cash-events','Large money movements in the last 180 days',customer_id)
    if gap:return gap
    rows=result['data'] or []
    return Evidence('cash-events','Large money movements in the last 180 days','record',
        rows if rows else {'large_movements':0,'meaning':'No movement of 10,000 or more is recorded in the last 180 days.'},str(result.get('as_of')),customer_id)


def insights_evidence(result: dict,customer_id: int) -> Evidence:
    """Transaction insights (0106): totals by month, card and overseas spending, large and recurring movements, computed in
    SQL under the client's transaction_insights consent. A gap when she has not shared them."""
    title='Transaction insights'
    gap=not_shared(result,'transaction-insights',title,customer_id)
    if gap:return gap
    data=result['data']
    return Evidence('transaction-insights',f"{title} for the last {data['days']} days",'record',data,str(result.get('as_of')),customer_id)


def _words(d: date) -> str:return f'{d.day} {d:%b %Y}'  # 12 Nov 2026, as rm.day writes dates


def talking_points_evidence(result: dict,customer_id: int) -> Evidence:
    """Topics from the client's chats (0107 contract; build plan phase 4 fills them), each rendered by code
    (TalkingPoint.line) with its brief label; a row that fails the contract is dropped and counted, never shown. Without
    her chat_topics grant the inferred topics are a gap, and the topics she shared herself still come with it."""
    title='Topics from the client\'s chats'
    rows,dropped=[],0
    for row in result.get('data') or []:
        try:p=TalkingPoint.model_validate(row)
        except ValidationError:dropped+=1;continue
        if result.get('shared') is False and p.origin!='confirmed':dropped+=1;continue  # inferred topics need her chat_topics grant
        rows.append({'topic':p.topic,'line':p.line(),'label':p.brief_label(),'first_seen':_words(p.first_seen),'last_seen':_words(p.last_seen)})
    withheld={'withheld':dropped} if dropped else {}
    gap=not_shared(result,'talking-points',title,customer_id)
    if gap:
        gap.data.update(shared_by_client=rows,**withheld,meaning='The client has not shared topics from their chats with you: say so as a gap '
            'and do not guess them. Only the topics under shared_by_client, which she shared with you herself, may be used.')
        return gap
    return Evidence('talking-points',title,'record',{'points':rows,**withheld} if rows else
        {'points':0,**withheld,'meaning':'No topics from the client\'s chats are recorded.'},str(result.get('as_of')),customer_id)


PERIOD=re.compile(r'\b(?:next|coming|within(?: the next)?|in the next)\s+(\d{1,3})\s+(day|week|month)s?\b',re.I)
MONTHS=dict(day=1,week=7,month=30)


def asked_period(question: str,today: date) -> tuple[date,date,str] | None:
    """The period a question across the book asks about ("next 30 days", "this month", "next month"), or None.
    Dates are compared here, in code, so the model never has to decide whether a date falls inside the period."""
    text=question.lower()
    found=PERIOD.search(text)
    if found:
        days=int(found.group(1))*MONTHS[found.group(2).lower()]
        return today,today+timedelta(days=days),f'the next {found.group(1)} {found.group(2)}{"s" if found.group(1)!="1" else ""}'
    if re.search(r'\bthis month\b',text):
        end=(today.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        return today,end,'this month'
    if re.search(r'\bnext month\b',text):
        start=(today.replace(day=28)+timedelta(days=4)).replace(day=1)
        end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        return start,end,'next month'
    if re.search(r'\bthis week\b',text):return today,today+timedelta(days=6-today.weekday()),'this week'
    return None


def _day(text: Any) -> date | None:
    try:return datetime.strptime(str(text),'%d %b %Y').date()
    except ValueError:return None


def _in_period(row: dict,period: tuple[date,date,str] | None,today: date) -> dict:
    """Days from today for every dated fact, and, when the question names a period, only the facts inside it."""
    row=dict(row)
    items=[]
    for item in row.get('maturities_and_reviews_next_90_days') or []:
        due=_day(item.get('due'))
        if due is None:continue
        if period and not period[0]<=due<=period[1]:continue
        items.append({**item,'days_from_today':(due-today).days})
    review=_day(row.get('review_due'))
    if period:
        row.pop('maturities_and_reviews_next_90_days',None)
        row[f'maturities_and_reviews_in_{re.sub(r"[^a-z0-9]+","_",period[2])}']=items
        if review is not None and not period[0]<=review<=period[1]:row['review_due']=None
    else:row['maturities_and_reviews_next_90_days']=items
    if row.get('review_due') and review is not None:row['review_due_days_from_today']=(review-today).days
    return row


def book_bundle(result: dict,question: str='',today: date | None=None) -> Bundle:
    """One evidence item per client in the RM's book (rm_book_facts), for questions across the book. When the question
    names a period, dated facts outside it are removed before the model sees them, so it cannot list them."""
    bundle=Bundle()
    today=today or datetime.now(SGT).date()
    period=asked_period(question,today)
    if period:
        bundle.add(Evidence('asked-period','The period you asked about','record',{'today':today.strftime('%-d %b %Y'),
            'from':period[0].strftime('%-d %b %Y'),'to':period[1].strftime('%-d %b %Y'),'period':period[2],
            'note':'Only maturities and reviews inside this period are listed in the client records below.'},str(result.get('as_of'))))
    for row in result['data'] or []:
        slug=re.sub(r'[^a-z]+','-',row['client'].lower()).strip('-')
        bundle.add(Evidence('facts-'+slug,f"Book facts: {row['client']}",'record',_in_period(_strip(row,'customer_id'),period,today),str(result.get('as_of')),row['customer_id']))
    if not bundle.items:bundle.add(Evidence('facts-none','Your book','record',{'clients':0},str(result.get('as_of'))))
    return bundle
