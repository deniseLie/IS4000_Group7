"""Lane 1 v2 (migration 0143; docs/planning/stateful-talking-points.md): the talking-point classifier that knows the
current state. Still one strict-JSON call per qualifying message, no tools and no loop: the model (`talking_points/v2`)
sees her current points (list values only) and her last few redacted messages in this chat, and proposes add, update or
on hold for each topic. Code checks every change (`validate`) and customer_apply_talking_point checks it again. The model
never deletes: Delete, mute, withdrawing consent and the 90-day expiry stay with her or with code.

`gate` is v1's pre-filter with one difference: a message with no topic word still runs while a topic is active in this
chat (recorded from one of her last 4 turns, read by SQL), as a carry-over ("Is the lock-in usually two years?"). A
change whose quote has no topic word counts only for an active topic. On hold needs her own words for it (`OFF`): a wrong
on hold silently hides a lead from her RM, so wording the pattern misses leaves the point as it is.

`apply` is the SQL's rules as a pure function on an in-memory state, for the evaluation and the unit tests. Which version
runs is prompts.VERSIONS['talking_points'] (`version`); v1 stays in services/talking_points.py."""
import json
import re
from collections import Counter
from typing import get_args
from uuid import UUID
from app.core.config import settings
from app.schemas.chat import Message
from app.schemas.contracts import RequestContext,RiskAssessment
from app.schemas.meetings import KINDS,ConcernTag
from app.schemas.talking_points_state import TopicState
from app.services import llm_gateway,prompts,tools
from app.services import talking_points as tp
from app.services.authorization import require_permission

ACTIONS=('add','update','on_hold')
CONCERNS=get_args(ConcernTag)
ALL_KINDS=tuple(kind for kinds in KINDS.values() for kind in kinds)
RANK=('concern','curious','exploring','planning','ready')  # = insights.stage_rank: the stage only moves up
EARLIER=5  # her messages before the latest one that the model sees, from this chat only
# Her own words that a plan is off. Only these make an on hold; doubt or a question never does.
OFF=re.compile(r"\bnot\s+(?:going\s+)?(?:to\s+)?go(?:ing)?\s+ahead\b|\bdecided\s+(?:not\s+to|against)\b|\bput\s+(?:it|this|that|them|things|everything)\s+off\b"
    r"|\bput\s+off\b|\bput\s+(?:the|our|my)\s+\w+(?:\s+\w+)?\s+off\b|\bpostpon\w*|\bhold(?:ing)?\s+off\b|\bon\s+hold\b|\bno\s+longer\b"
    r"|\bchanged\s+(?:my|our)\s+minds?\b|\bcancel\w*|\bcall(?:ed|ing)?\s+(?:it|the\s+\w+)\s+off\b",re.I)
SCHEMA={'title':'talking_point_changes','description':'Changes her latest message makes to her talking points: list values and her exact quote only.',
    'type':'object','additionalProperties':False,'required':['changes'],'properties':{'changes':{'type':'array','items':{
    'type':'object','additionalProperties':False,'required':['action','topic','stage','timeframe','kind','concerns','quote'],'properties':{
        'action':{'type':'string','enum':list(ACTIONS)},'topic':{'type':'string','enum':list(tp.TOPICS)},
        'stage':{'type':'string','enum':list(tp.STAGES)},'timeframe':{'type':'string','enum':list(tp.TIMEFRAMES)},
        'kind':{'type':'string','enum':[*ALL_KINDS,'none']},  # "none": she has not said (the gateway's schema check has no null type)
        'concerns':{'type':'array','items':{'type':'string','enum':list(CONCERNS)}},'quote':{'type':'string'}}}}}}


def version() -> str:
    """The lane 1 prompt in use (prompts.VERSIONS, the one table): Versions after v1 use this stateful module."""
    return prompts.VERSIONS['talking_points']


def gate(message: str,risk: RiskAssessment,offer_reply: bool=False,active: list[str]|tuple=()) -> str | None:
    """Why this turn is not worth a model call, or None: v1's pre-filter, except that a message with no topic word runs
    while a topic is active in this chat (a carry-over)."""
    reason=tp.prefilter(message,risk,offer_reply)
    return None if reason=='no_topic' and active else reason


def earlier_messages(history: list[Message]) -> list[str]:
    """Her messages in this chat before the latest, oldest first, at most EARLIER. The saved history is redacted."""
    users=[m.content for m in history if m.role=='user']
    return users[:-1][-EARLIER:]


def model_input(state: TopicState,earlier: list[str],message: str) -> list[tuple[str,str]]:
    """Her points as list values, the active topics and her own redacted messages: never the assistant's replies, her
    records or any stored words."""
    points=[p.model_dump(mode='json') for p in state.points]
    return [('user',json.dumps({'current_points':points,'active_topics':list(state.active_topics),'earlier_messages':earlier,'message':message},
        ensure_ascii=False))]


def concerns_of(values) -> list[str]:
    """Listed concerns, each once, at most 4 (the first ones she raised), stored in order."""
    return sorted(list(dict.fromkeys(c for c in values or [] if c in CONCERNS))[:4])


def merge_concerns(old,new) -> list[str]:
    """= insights.merge_concerns: her newest concerns first, each once, at most 4, stored in order."""
    return sorted(list(dict.fromkeys([*(new or []),*(old or [])]))[:4])


def validate(changes: list[dict],message: str,state: TopicState) -> tuple[list[dict],Counter]:
    """The code checks on what the model proposed: listed values and a kind of that topic, not muted, her exact words
    from her latest message, a topic word in the quote or a topic active in this chat (a carry-over), no sensitive word,
    confirmed points untouched, update and on hold only for a point she has (an update with a topic word becomes an add),
    on hold only with her words for it, one change per topic. Amounts in a kept quote are masked."""
    kept,dropped,seen=[],Counter(),set()
    points={p.topic:p for p in state.points}
    for change in changes:
        action,topic,quote=change.get('action'),change.get('topic'),(change.get('quote') or '').strip()
        kind=None if change.get('kind') in (None,'none') else change.get('kind')
        if action not in ACTIONS or topic not in tp.TOPICS or change.get('stage') not in tp.STAGES or change.get('timeframe') not in tp.TIMEFRAMES:
            dropped['unlisted']+=1;continue
        if kind is not None and kind not in KINDS[topic]:dropped['wrong_kind']+=1;continue
        if topic in state.muted:dropped['muted']+=1;continue
        if topic in seen:dropped['repeat']+=1;continue
        if not quote or len(quote)>200 or quote not in message:dropped['not_her_words']+=1;continue
        named=bool(tp.TOPIC_WORDS[topic].search(quote))
        if not named and topic not in state.active_topics:dropped['not_active']+=1;continue
        if tp.SENSITIVE.search(quote):dropped['sensitive']+=1;continue
        point=points.get(topic)
        if point is not None and point.origin=='confirmed':dropped['confirmed']+=1;continue
        if point is None and action!='add':
            if action=='update' and named:action='add'
            else:dropped['no_point']+=1;continue
        if point is not None and action=='add':action='update'
        if action=='on_hold' and not OFF.search(quote):dropped['no_off_words']+=1;continue
        seen.add(topic)
        kept.append({'action':action,'topic':topic,'stage':change['stage'],'timeframe':change['timeframe'],'kind':kind,
            'concerns':concerns_of(change.get('concerns')),'quote':tp.mask_amounts(quote),'carried':not named})
    return kept,dropped


def apply(points: dict[str,dict],change: dict,active: list[str]|tuple=(),muted: list[str]|tuple=()) -> tuple[dict[str,dict],str|None]:
    """customer_apply_talking_point's rules on an in-memory state ({topic: point with stage, timeframe, kind, concerns,
    origin, on_hold}): returns the new state and None, or the state unchanged and the reason it was skipped."""
    topic,action=change['topic'],change['action']
    point=points.get(topic)
    reason=('muted' if topic in muted else 'not_active' if change.get('carried') and topic not in active else
        'confirmed' if point and point['origin']=='confirmed' else 'no_point' if point is None and action!='add' else None)
    if reason:return points,reason
    if point is None:
        new={'topic':topic,'stage':change['stage'],'timeframe':change['timeframe'],'kind':change.get('kind'),
            'concerns':merge_concerns([],change.get('concerns')),'origin':'inferred','on_hold':False}
    elif action=='on_hold':new={**point,'on_hold':True}
    else:
        rank=RANK.index
        new={**point,'stage':change['stage'] if rank(change['stage'])>rank(point['stage']) else point['stage'],
            'timeframe':point['timeframe'] if change['timeframe']=='unknown' else change['timeframe'],'kind':change.get('kind') or point.get('kind'),
            'concerns':merge_concerns(point['concerns'],change.get('concerns')),'on_hold':False}
    return {**points,topic:new},None


async def state(ctx: RequestContext,turn_id: UUID) -> TopicState:
    """The one read before a turn is classified: the status, her points (list values only) and this chat's active topics."""
    return (await tools.snapshot('my_topic_state',{'turn_id':str(turn_id)},tp.lane(ctx),TopicState)).data


async def extract(ctx: RequestContext,turn_id: UUID,message: str,earlier: list[str],current: TopicState) -> dict:
    """One classification and the code checks; applies the kept changes under her JWT. Counts only come back."""
    prompt=prompts.load('talking_points',version())
    result=await llm_gateway.call(prompt,prompt['system'],model_input(current,earlier,message),SCHEMA,model=settings.talking_points_model or None,retries=0)
    proposed=result.data.get('changes') or []
    kept,dropped=validate(proposed,message,current)
    outcomes=Counter()
    if kept:require_permission(ctx.principal,'insights.record',{'customer_id':ctx.principal.customer_id},ctx)
    for change in kept:
        reply=await tp._rpc(ctx,'customer_apply_talking_point',{'p_turn_id':str(turn_id),'p_action':change['action'],'p_topic':change['topic'],
            'p_stage':change['stage'],'p_timeframe':change['timeframe'],'p_kind':change['kind'],'p_concerns':change['concerns'],
            'p_quote':change['quote'],'p_carried':change['carried']})
        outcomes['recorded' if reply.get('recorded') else reply.get('reason') or 'not_recorded']+=1
    return {'proposed':len(proposed),'kept':len(kept),'dropped':dict(dropped),'outcomes':dict(outcomes),
        'actions':dict(Counter(c['action'] for c in kept)),'carried':sum(c['carried'] for c in kept)}
