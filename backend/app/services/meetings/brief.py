"""RM briefing routes use the lifecycle's canonical saved brief and exact approval.

The pure store/view/card helpers retain the 0118 historical copilot format. Existing copilot versions remain in
meetings.copilot_briefs; they are never copied into the current reference-only plan or treated as its approval.
"""
import asyncio
from typing import Any
from pydantic import JsonValue
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


async def read(meeting_id: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    """Read the same current reference-only brief used by the lifecycle and copilot."""
    from app.services.meetings import brief_workspace
    return await brief_workspace.read(meeting_id,ctx)


async def prepare(meeting_id: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    """Prepare a current reference-only version; changed sources produce a new unapproved draft."""
    from app.services.meetings import brief_workspace
    return await brief_workspace.prepare(meeting_id,ctx)


async def refresh(meeting_id: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    """Create a new immutable version for a fresh exact review."""
    from app.services.meetings import brief_workspace
    return await brief_workspace.refresh(meeting_id,ctx)


async def approve(meeting_id: UUID,brief_id: UUID,version: int,command_id: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    """Approve the exact canonical version and current sources from either RM screen."""
    from app.services.meetings import brief_workspace
    return await brief_workspace.approve(meeting_id,brief_id,version,command_id,ctx)


async def upcoming(ctx: RequestContext) -> list[dict[str,JsonValue]]:
    """Index upcoming appointments by their canonical brief version and current source status."""
    from app.services.meetings import brief_workspace
    return await brief_workspace.upcoming(ctx)


def card(meeting_id: UUID,viewed: dict,as_of: Any) -> BriefCard:
    """The seam's BriefCard (schemas/meetings.py) for a viewed brief: labels mapped, every non-gap item cited."""
    sections=[BriefSection(heading=s['heading'][:120],items=[BriefItem(label=LABELS.get(i.get('kind'),'record'),text=i['text'][:1000],
        sources=(i.get('source_ids') or [])[:10]) for i in s['items'] if i.get('text') and (i.get('kind')=='gap' or i.get('source_ids'))][:30])
        for s in viewed['sections']][:12]
    return BriefCard(meeting_id=meeting_id,version=viewed['version'],status=viewed['brief_status'],sections=sections,
        consent={k:v for k,v in viewed['consent'].items()},withheld=viewed['withheld'],as_of=as_of)
