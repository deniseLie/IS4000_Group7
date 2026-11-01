"""Turn RM records into cited evidence for the RM assistant, and the matching source list the workspace shows.

Each evidence item has a short stable id the model must cite (for example note-2025-03-12, ticket-TCK-001004,
shared-note), a title, the kind of record and its as-of time, plus the record itself as JSON. Dates and times are already
in Singapore time words ("Tue 3 Nov 2026, 10:00 am"), so the model never converts time zones and the checker can compare
dates. Restricted specialist detail never reaches this module: the SQL functions withhold it.
"""
import json
from dataclasses import dataclass,field
from typing import Any
from app.services.knowledge import INDEX


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


def ticket_detail_bundle(result: dict) -> Bundle:
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
    article=INDEX.get(info.get('kb_article') or '')
    if article:bundle.add(Evidence('kb-'+article.id,f'Approved guidance: {article.title}','guidance',{'content':article.content,'version':article.version},article.version,cid))
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


def handover_bundle(result: dict) -> Bundle:
    """RM16: the client overview plus all permitted notes, the consent-checked handover note and what the client was told."""
    data,as_of=result['data'],str(result.get('as_of'))
    bundle=client_bundle({'data':{**data,'interactions':[]},'as_of':as_of})
    cid=data['profile']['customer_id']
    add_notes(bundle,data.get('notes') or [],as_of,cid,data.get('withheld_private_notes') or 0)
    bundle.add(shared_evidence({'data':data.get('shared') or {},'as_of':as_of},cid))
    bundle.add(Evidence('told','What the client was previously told (bank, chat assistant, relationship managers)','record',data.get('told') or [],as_of,cid))
    bundle.add(Evidence('free-slots','Your free times in the next 14 days (not held or booked)','calendar',data.get('free_slots') or [],as_of,cid))
    return bundle
