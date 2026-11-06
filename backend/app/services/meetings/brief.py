"""Brief Writer (architecture §15.4, migration 0118): one saved, versioned pre-meeting brief per confirmed meeting.

prepare() runs the RM copilot's meeting brief for the appointment with the RM's own JWT (rm.records under rm_scope), so
every rule of the copilot applies: the book check, consent-gated reads with the fixed `meeting_prep` purpose, record
sections written by code, model points checked against their facts, every item cited. The restricted meeting worker
never runs this (it has no RM JWT and reads no client records).

What is stored (store()): the record sections, the checked points and the source titles. Items that rest only on a
consent-gated read (topics from her chats, transaction insights, large movements) are not stored, and neither is the
record behind a gated source; the section keeps the place they go (`gated_at`). view() re-reads those readers under her
consent each time the brief is opened and puts the current items back, so a withdrawn grant or a removed topic is never
shown from an old copy: it renders as "Removed by client on <date>", and an AI suggestion that rested only on gated
sources that are no longer shared is dropped (and counted).
"""
import asyncio
from typing import Any
from uuid import UUID,uuid4
from app.schemas.contracts import RequestContext
from app.schemas.meetings import BriefCard,BriefItem,BriefSection
from app.schemas.rm import RmAssistantRequest,RmContext
from app.services.rm import brief_facts as bf
from app.services.rm import evidence as ev
from app.services.rm import records

GATED={'talking-points':'chat_topics','transaction-insights':'transaction_insights','cash-events':'transaction_insights'}
CATEGORY_WORDS={'chat_topics':'topics from her chats','transaction_insights':'transaction insights'}
LABELS={'record':'record','gap':'gap','suggestion':'ai_suggestion','client_stated':'client_said'}
PURPOSE='meeting_prep'
MODEL_HEADINGS={h for _,h,*_ in bf.BRIEF_POINTS}  # the model's checked points (kept; dropped on open if they rested only on gated reads)
PREPARE_DAYS=7  # the workspace prepares missing briefs for meetings in the next 7 days


class BriefUnavailable(RuntimeError):
    """The copilot could not write a brief for this meeting (it asked a question or failed); nothing was saved."""


def _gated(ids: list[str]) -> bool:return bool(ids) and set(ids)<=set(GATED)


def store(reply: dict) -> dict:
    """What is saved from a copilot brief: gated items and gated source records left out (see the module docstring)."""
    sections=[]
    for s in reply.get('sections') or []:
        items,at=[],None
        for i in s.get('items') or []:
            if _gated(i.get('source_ids') or []) and s['heading'] not in MODEL_HEADINGS:  # record items from a gated read
                at=len(items) if at is None else at
                continue
            items.append({k:i[k] for k in ('text','kind','owner','source_ids') if k in i})
        if items or at is not None:sections.append({'heading':s['heading'],'items':items,**({'gated_at':at} if at is not None else {})})
    sources=[{k:v for k,v in src.items() if k!='record'} if src.get('id') in GATED else src for src in reply.get('sources') or []]
    return {'summary':reply.get('summary') or '','sections':sections,'sources':sources,'validation':reply.get('validation') or {},
        'prompt_version':reply.get('prompt_version'),'context':reply.get('context') or {}}


async def gated_bundle(ctx: RequestContext,customer_id: int) -> tuple[ev.Bundle,list[dict]]:
    """The three consent-gated reads now, with the brief's fixed purpose (each audited in SQL), and the topics shown."""
    points,insights,cash=await asyncio.gather(records.talking_points(ctx,customer_id,PURPOSE),
        records.transaction_insights(ctx,customer_id,PURPOSE),records.cash_events(ctx,customer_id))
    bundle=ev.Bundle()
    bundle.add(ev.talking_points_evidence(points,customer_id))
    bundle.add(ev.insights_evidence(insights,customer_id))
    bundle.add(ev.cash_evidence(cash,customer_id))
    return bundle,[p for p in points.get('data') or [] if isinstance(p,dict) and p.get('point_id')]


def removed_items(brief: dict) -> list[dict]:
    rows=[bf.gap(f"Removed by client on {w['on']}: {CATEGORY_WORDS.get(w['category'],w['category'])}.",
        [i for i,c in GATED.items() if c==w['category']][:1]) for w in brief.get('withdrawn') or []]
    if brief.get('removed_points') and not any(w.get('category')=='chat_topics' for w in brief.get('withdrawn') or []):
        days=sorted({r['on'] for r in brief['removed_points']})
        rows.append(bf.gap(f"Removed by client on {', '.join(days)}: {bf.plural(len(brief['removed_points']),'topic')} shown in an earlier version.",['talking-points']))
    return rows


def view(brief: dict,bundle: ev.Bundle) -> dict:
    """The stored brief with its gated items read again now (see the module docstring)."""
    content=brief['content']
    shared={c for i,c in GATED.items() if (e:=bundle.get(i)) is not None and e.kind!='consent'}
    fresh=[*bf.topic_items(bundle),*bf.insights_gap(bundle)]
    sections,dropped,placed=[],0,False
    for s in content.get('sections') or []:
        items=[]
        for i in s.get('items') or []:
            ids=i.get('source_ids') or []
            if s['heading'] in MODEL_HEADINGS and _gated(ids) and not {GATED[x] for x in ids}<=shared:dropped+=1;continue
            items.append(i)
        if 'gated_at' in s:
            items[s['gated_at']:s['gated_at']]=[*fresh,*removed_items(brief)];placed=True
        if items:sections.append({'heading':s['heading'],'items':items})
    if not placed and (extra:=[*fresh,*removed_items(brief)]):sections.append({'heading':'What the client has told us','items':extra})
    cited={x for s in sections for i in s['items'] for x in i.get('source_ids') or []}
    sources=[src for src in content.get('sources') or [] if src.get('id') not in GATED and src.get('id') in cited]
    sources+=[e.for_ui() for e in bundle.items if e.id in cited]
    return {'brief_id':brief['brief_id'],'version':brief['version'],'brief_status':brief['status'],'created':brief.get('created_text'),
        'stale':bool(brief.get('stale')),'withdrawn':brief.get('withdrawn') or [],'removed_points':brief.get('removed_points') or [],
        'summary':content.get('summary') or '','sections':sections,'sources':sources,'withheld':dropped,
        'consent':{c:c in shared for c in CATEGORY_WORDS},'prompt_version':content.get('prompt_version'),'intent':'meeting_brief','status':'ok',
        'context':content.get('context') or {}}  # status/intent/context: the shape RmAnswer renders


async def read(meeting_id: UUID,ctx: RequestContext) -> dict:
    """The meeting and its latest brief re-checked and re-read now (None when no brief is saved yet)."""
    result=await records.rpc('rm_meeting_brief',{'p_meeting_id':str(meeting_id)},ctx,'rm.meeting_brief')
    data=result['data']
    if data.get('brief') is None:return {'meeting':data['meeting'],'brief':None}
    bundle,_=await gated_bundle(ctx,data['meeting']['customer_id'])
    return {'meeting':data['meeting'],'brief':view(data['brief'],bundle)}


async def prepare(meeting_id: UUID,ctx: RequestContext,regenerate: bool=False,say=None,show=None) -> dict:
    """The current brief, or a new version written now: when none exists, when its records changed (stale), or on request."""
    from app.services.rm import graph  # the copilot graph; imported here so the worker never loads it
    current=await read(meeting_id,ctx)
    brief=current['brief']
    if brief is not None and not regenerate and not brief['stale']:return current
    meeting=current['meeting']
    async def quiet(*_):return None
    req=RmAssistantRequest(message='Prepare the briefing for this meeting.',intent='meeting_brief',
        context=RmContext(client_id=meeting['customer_id'],appointment_id=meeting_id))
    reply=await graph.respond(req,ctx,say or quiet,show)
    if reply.get('status')!='ok' or reply.get('intent')!='meeting_brief' or not reply.get('sections'):
        raise BriefUnavailable('The briefing could not be prepared for this meeting.')
    _,points=await gated_bundle(ctx,meeting['customer_id'])
    await records.rpc('rm_meeting_brief_save',{'p_meeting_id':str(meeting_id),'p_content':store(reply),
        'p_point_ids':[str(p['point_id']) for p in points][:50],'p_command_id':str(uuid4())},ctx,'rm.meeting_brief_save')
    return await read(meeting_id,ctx)


async def approve(meeting_id: UUID,brief_id: UUID,version: int,command_id: UUID,ctx: RequestContext) -> dict:
    return await records.rpc('rm_meeting_brief_approve',{'p_meeting_id':str(meeting_id),'p_brief_id':str(brief_id),'p_version':version,
        'p_command_id':str(command_id)},ctx,'rm.meeting_brief_approve')


async def upcoming(ctx: RequestContext) -> list[dict]:
    """The RM's confirmed meetings in the next 14 days with where each brief stands."""
    return (await records.rpc('rm_meeting_briefs',{},ctx,'rm.meeting_briefs'))['data']


def card(meeting_id: UUID,viewed: dict,as_of: Any) -> BriefCard:
    """The seam's BriefCard (schemas/meetings.py) for a viewed brief: labels mapped, every non-gap item cited."""
    sections=[BriefSection(heading=s['heading'][:120],items=[BriefItem(label=LABELS.get(i.get('kind'),'record'),text=i['text'][:1000],
        sources=(i.get('source_ids') or [])[:10]) for i in s['items'] if i.get('text') and (i.get('kind')=='gap' or i.get('source_ids'))][:30])
        for s in viewed['sections']][:12]
    return BriefCard(meeting_id=meeting_id,version=viewed['version'],status=viewed['brief_status'],sections=sections,
        consent={k:v for k,v in viewed['consent'].items()},withheld=viewed['withheld'],as_of=as_of)
