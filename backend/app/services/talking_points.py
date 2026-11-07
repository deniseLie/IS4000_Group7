"""Talking points (migration 0108; RM copilot build plan phase 4): what a client is planning, for her RM's meetings,
without her RM ever seeing her chat. Only list values cross to the RM side (schemas/meetings.TalkingPoint).

Lane 1, inferred (`after_reply`): a background task after the `answer` event of a signed-in saved chat, with her request
context and JWT. A care signal sets a 60-day pause and stores nothing. Otherwise it runs only while her chat_topics grant
is active (else only the skip is audited) and after a code pre-filter (greetings, yes/no and offer replies, fixed safety
turns, mostly redacted or sensitive messages, no topic word). One strict-JSON call (`talking_points/v1`, model
TALKING_POINTS_MODEL, no retry) classifies her redacted message, with her previous message as context; code keeps a point
only when the quote is her exact words, holds a word from the topic's list, the topic is listed and not muted, and no
sensitive word matches; amounts are masked. The database checks the quote again and keeps only the turn (0142: her
words are never stored). A model failure is logged with ids only; the reply is never affected. With the talking_points
prompt at v2, lane 1 reads her current points and this chat's active topics instead and proposes add, update or on hold
(services/talking_points_state, migration 0143); the skip and extract audits then add the version, actions and carry-overs.

Lane 2, shared (`share_offer`, called by orchestrator.compose): a rule, never the model, offers one insight.share when
her message names a topic and a planning cue and nothing rules it out (care, mute, pause, a prompt for that topic in the
last 30 days, an open meeting request on it). Nothing is stored unless she says yes."""
import json
import logging
import re
from collections import Counter
from datetime import date
from uuid import UUID,uuid4
from pydantic import TypeAdapter
from app.core import data_api
from app.core.config import settings
from app.core.observability import log_event,span,start_trace
from app.schemas.chat import Message
from app.schemas.contracts import OfferProposal,RequestContext,RiskAssessment
from app.schemas.customer_data import CustomerEnvelope
from app.schemas.meetings import TOPIC_NAME,TalkingPoint
from app.schemas.talking_points import InsightShareResult,InsightShareTarget,MyTalkingPoints,TopicStatus
from app.services import audit,llm_gateway,offers,prompts
from app.services.authorization import require_permission
from app.services.customer_data import _read

TOPICS=('home_loan','investments','fixed_deposit','insurance')
STAGES=('curious','exploring','planning','ready','concern')
TIMEFRAMES=('now','within_3m','within_12m','later','unknown')
CONCERNS=('rates','fees','eligibility','timing')
# The words that name each topic (lane 1 keeps a quote only with one; lane 2 needs one in her message).
TOPIC_WORDS={
    'home_loan':re.compile(r"\b(?:home|housing|property|hdb|mortgage)\s+loans?\b|\bmortgages?\b|\bcondo(?:minium)?s?\b|\bhdb\b|\bbto\b"
        r"|\bpropert(?:y|ies)\b|\brefinanc\w*|\bresale\s+flat\b|\blanded\s+(?:house|home|property)\b|\bbuy(?:ing)?\s+(?:a\s+|our\s+|my\s+)?(?:house|home|flat|place)\b",re.I),
    'investments':re.compile(r"\binvest\w*|\bunit\s+trusts?\b|\bstocks?\b|\bshares\s+in\b|\betfs?\b|\bportfolios?\b|\bbonds?\b"
        r"|\brobo[- ]?advis\w*|\bmutual\s+funds?\b",re.I),
    'fixed_deposit':re.compile(r"\bfixed[- ]deposits?\b|\btime\s+deposits?\b|\bfds?\b",re.I),
    'insurance':re.compile(r"\binsur\w*|\blife\s+cover\b|\bterm\s+life\b|\bwhole\s+life\b|\bendowments?\b|\bannuit\w*",re.I),
}
# Health, religion, politics, ethnicity, sexuality: a message naming any of them stores nothing.
SENSITIVE=re.compile(r"\b(?:cancer|diabet\w*|pregnan\w*|illness|sick\w*|diagnos\w*|disease|depress\w*|anxiety|mental\s+health|hospital\w*"
    r"|surgery|chemo\w*|therapy|disabilit\w*|medical\s+condition|religio\w*|church|mosque|temple|christian|muslim|islam\w*|buddhis\w*"
    r"|hindu\w*|catholic|jewish|tithe|zakat|politic\w*|election|voting|ethnic\w*|race|racial|malay|chinese|indian|eurasian|gay|lesbian"
    r"|bisexual|transgender|lgbt\w*|sexual\w*|queer)\b",re.I)
# Amounts are never stored: the same pattern as SQL insights.mask_amounts (0108).
AMOUNT=re.compile(r"(?:s?\$|sgd ?|usd ?)\d[\d,]*(?:\.\d+)?(?: ?(?:k|m|mil|million|thousand)\b)?|\b\d[\d,]*(?:\.\d+)? ?(?:k|m|mil|million|thousand)\b"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{5,}\b",re.I)
GREETING=re.compile(r"^\W*(?:hi|hello|hey|hiya|good\s+(?:morning|afternoon|evening)|thanks?(?:\s+you)?|thank\s+you|ok(?:ay)?|cool|great|bye|goodbye)"
    r"(?:\s+(?:there|so\s+much|very\s+much))?\W*$",re.I)
REDACTED='[private detail removed]'
# Lane 2: her own plan, not a general question ("buying", "next year", "planning to", "ready to apply").
PLANNING=re.compile(r"\b(?:buying|to\s+buy|planning(?:\s+to|\s+on)?|plan\s+to|plans\s+to|next\s+year|this\s+year|ready\s+to"
    r"|going\s+to\s+(?:buy|apply|invest|get|take|start|put|place|renew)|want\s+to\s+(?:buy|apply|invest|get|take|start|put|open|place|renew)"
    r"|apply(?:ing)?\s+for|getting\s+(?:a|an|my)\b)",re.I)
SELF=re.compile(r"\b(?:i|we)\b",re.I)  # her own plan: "I'm", "we're", "I want"...
OTHERS=re.compile(r"\b(?:my|our)\s+(?:son|daughter|child|kids|children|parents?|mum|mom|mother|dad|father|brother|sister|friend|colleague"
    r"|cousin|boss|neighbou?r)\b(?:\s+\w+){0,2}\s+(?:is|are|was|were|wants?|plans?|will)\b",re.I)
READY=re.compile(r"\bready\s+to\b|\bapply(?:ing)?\s+(?:now|today)\b|\bright\s+away\b",re.I)
WHEN={'now':re.compile(r"\b(?:right\s+now|right\s+away|immediately|asap|today|this\s+(?:week|month))\b",re.I),
    'within_3m':re.compile(r"\b(?:next\s+month|in\s+(?:a|one|two|three|[123])\s+months?|in\s+a\s+few\s+(?:weeks|months)|soon|within\s+(?:3|three)\s+months)\b",re.I),
    'within_12m':re.compile(r"\b(?:next\s+year|this\s+year|later\s+this\s+year|by\s+the\s+end\s+of\s+the\s+year|in\s+(?:6|six|9|nine|12|twelve)\s+months|within\s+(?:a|one)\s+year)\b",re.I),
    'later':re.compile(r"\b(?:in\s+a\s+few\s+years|in\s+(?:2|two|3|three|5|five)\s+years|someday|eventually|one\s+day)\b",re.I)}
SHARE_WORDS={'home_loan':'home loans','investments':'investing','fixed_deposit':'fixed deposits','insurance':'insurance'}
SCHEMA={'title':'talking_points','description':'Talking points in her latest message, list values and her exact quote only.',
    'type':'object','additionalProperties':False,'required':['points'],'properties':{'points':{'type':'array','items':{
    'type':'object','additionalProperties':False,'required':['topic','stage','timeframe','concerns','quote'],'properties':{
        'topic':{'type':'string','enum':list(TOPICS)},'stage':{'type':'string','enum':list(STAGES)},
        'timeframe':{'type':'string','enum':list(TIMEFRAMES)},'concerns':{'type':'array','items':{'type':'string','enum':list(CONCERNS)}},
        'quote':{'type':'string'}}}}}}
STATUS=TypeAdapter(CustomerEnvelope[TopicStatus])
MINE=TypeAdapter(CustomerEnvelope[MyTalkingPoints])
ACTOR={'actor_type':'agent','actor_id':'talking_points'}


def mask_amounts(text: str) -> str:
    return AMOUNT.sub('[amount]',text)


def topics_in(text: str) -> list[str]:
    """Topics whose words appear, in the order they first appear."""
    found=[(m.start(),topic) for topic,words in TOPIC_WORDS.items() if (m:=words.search(text))]
    return [topic for _,topic in sorted(found)]


def mostly_redacted(text: str) -> bool:
    return text.count(REDACTED)>0 and len(REDACTED)*text.count(REDACTED)*2>=len(text.strip())


def prefilter(message: str,risk: RiskAssessment,offer_reply: bool=False) -> str | None:
    """Why this turn is not worth a model call, or None. Pure code on her redacted message."""
    if offer_reply or offers.reply_kind(message)!='other':return 'yes_no'
    if risk.fixed_response is not None or risk.risk_flags:return 'safety'
    if GREETING.match(message):return 'greeting'
    if mostly_redacted(message):return 'redacted'
    if SENSITIVE.search(message):return 'sensitive'
    if not topics_in(message):return 'no_topic'
    return None


def validate(points: list[dict],message: str,muted: set[str]) -> tuple[list[dict],Counter]:
    """The code checks on what the model proposed: listed values, her exact words, a topic word in the quote, not muted,
    no sensitive word, one point per topic. Amounts in a kept quote are masked."""
    kept,dropped,seen=[],Counter(),set()
    for point in points:
        topic,quote=point.get('topic'),(point.get('quote') or '').strip()
        if topic not in TOPICS or point.get('stage') not in STAGES or point.get('timeframe') not in TIMEFRAMES:dropped['unlisted']+=1;continue
        if topic in muted:dropped['muted']+=1;continue
        if topic in seen:dropped['repeat']+=1;continue
        if not quote or len(quote)>200 or quote not in message:dropped['not_her_words']+=1;continue
        if not TOPIC_WORDS[topic].search(quote):dropped['no_topic_word']+=1;continue
        if SENSITIVE.search(quote):dropped['sensitive']+=1;continue
        seen.add(topic)
        kept.append({'topic':topic,'stage':point['stage'],'timeframe':point['timeframe'],
            'concerns':sorted({c for c in point.get('concerns') or [] if c in CONCERNS}),'quote':mask_amounts(quote)})
    return kept,dropped


async def status(ctx: RequestContext) -> TopicStatus:
    return (await _read('insights.status','customer_topic_status',{},STATUS,ctx)).data


async def mine(ctx: RequestContext) -> CustomerEnvelope[MyTalkingPoints]:
    """Her own points (list values, never her words), removed points, mutes and pause (Data sharing > Chat insights)."""
    return await _read('insights.read','customer_read_talking_points',{},MINE,ctx)


def system_text(prompt: dict) -> str:
    return prompt['system']


def model_input(message: str,previous: str | None) -> list[tuple[str,str]]:
    """Her redacted message and her previous one only: never the assistant's reply and never records."""
    return [('user',json.dumps({'message':message,'previous_message':previous or ''},ensure_ascii=False))]


async def _rpc(ctx: RequestContext,name: str,params: dict) -> dict:
    def run():
        with data_api.connect(ctx) as db:return db.rpc(name,params).execute().data
    return await data_api.execute(run,name)


async def extract(ctx: RequestContext,turn_id: UUID,message: str,previous: str | None,muted: set[str]) -> dict:
    """One classification and the code checks; records the kept points under her JWT. Counts only come back."""
    prompt=prompts.load('talking_points','v1')  # v1's own prompt; prompts.VERSIONS 'v2' runs talking_points_state instead
    result=await llm_gateway.call(prompt,system_text(prompt),model_input(message,previous),SCHEMA,model=settings.talking_points_model or None,retries=0)
    kept,dropped=validate(result.data.get('points') or [],message,muted)
    recorded=Counter()
    if kept:require_permission(ctx.principal,'insights.record',{'customer_id':ctx.principal.customer_id},ctx)
    for point in kept:
        reply=await _rpc(ctx,'customer_record_talking_point',{'p_turn_id':str(turn_id),'p_topic':point['topic'],'p_stage':point['stage'],
            'p_timeframe':point['timeframe'],'p_concerns':point['concerns'],'p_quote':point['quote']})
        recorded['recorded' if reply.get('recorded') else reply.get('reason') or 'not_recorded']+=1
    return {'proposed':len(result.data.get('points') or []),'kept':len(kept),'dropped':dict(dropped),'outcomes':dict(recorded)}


async def after_reply(ctx: RequestContext,turn_id: UUID,history: list[Message],risk: RiskAssessment,offer_reply: bool=False) -> None:
    """Lane 1, after the answer event (never raises; the chat reply is already sent). Its own audit batch and trace."""
    if ctx.principal.kind!='customer' or ctx.principal.customer_id is None:return
    try:
        with data_api.connect(ctx) as db:
            await _after_reply(ctx,audit.start_batch(db,session_id=ctx.session_id),turn_id,history,risk,offer_reply)
    except Exception as error:  # never surfaces: the reply is already sent
        log_event('talking_points_failed',logging.WARNING,turn_id=str(turn_id),error_type=type(error).__name__)


async def _after_reply(ctx: RequestContext,batch: audit.AuditBatch,turn_id: UUID,history: list[Message],risk: RiskAssessment,offer_reply: bool) -> None:
    trace=start_trace()
    with span('talking_points','agent',turn_id=str(turn_id)) as attrs:
        try:
            if risk.care:  # a care signal: nothing is read or stored, and suggestions pause for 60 days
                require_permission(ctx.principal,'insights.record',{'customer_id':ctx.principal.customer_id},ctx)
                await _rpc(ctx,'customer_pause_suggestions',{'p_turn_id':str(turn_id)})
                attrs['outcome']='paused'
            else:
                users=[m.content for m in history if m.role=='user']  # the saved history is redacted; the latest message by the guardrail
                message,previous=risk.redacted_message or users[-1],(users[-2] if len(users)>1 else None)
                from app.services import talking_points_state as v2  # lane 1 v2 (0143); imports this module
                stateful=v2.version()=='v2'
                current=await (v2.state(ctx,turn_id) if stateful else status(ctx))
                reason=('no_consent' if not current.chat_topics else 'paused' if current.paused else
                    v2.gate(message,risk,offer_reply,current.active_topics) if stateful else prefilter(message,risk,offer_reply))
                attrs['outcome']=reason or 'extracted'
                tag={'version':'v2'} if stateful else {}
                if reason:batch.add('agent','agent.talking_points_skipped',details={'reason':reason,'turn_id':str(turn_id),**tag},**ACTOR)
                else:
                    counts=await (v2.extract(ctx,turn_id,message,v2.earlier_messages(history),current) if stateful else
                        extract(ctx,turn_id,message,previous,set(current.muted)))
                    attrs.update(kept=counts['kept'],proposed=counts['proposed'])
                    batch.add('agent','agent.talking_points_extracted',details={'turn_id':str(turn_id),'proposed':counts['proposed'],
                        'kept':counts['kept'],'dropped':counts['dropped'],'outcomes':counts['outcomes'],
                        **({'actions':counts['actions'],'carried':counts['carried'],**tag} if stateful else {})},**ACTOR)
        except llm_gateway.LLMError as error:
            attrs['outcome']='model_failed'
            log_event('talking_points_failed',logging.WARNING,turn_id=str(turn_id),reason=error.reason)
            batch.add('agent','agent.talking_points_extracted','failed',details={'turn_id':str(turn_id),'reason':error.reason},**ACTOR)
        except Exception as error:  # a background task: logged with ids only, the reply is already sent
            attrs['outcome']='error'
            log_event('talking_points_failed',logging.WARNING,turn_id=str(turn_id),error_type=type(error).__name__)
            try:batch.add('agent','agent.talking_points_extracted','failed',details={'turn_id':str(turn_id),'reason':type(error).__name__[:60]},**ACTOR)
            except ValueError:pass
        for call in trace.llm_calls:
            batch.add('agent','agent.llm_call','success' if call['ok'] else 'failed',details={k:call[k] for k in ('model','prompt_version','transport','input_tokens','output_tokens','ms')})
    try:await batch.flush()
    except audit.AuditWriteError as error:log_event('talking_points_audit_failed',logging.WARNING,turn_id=str(turn_id),error_type=type(error).__name__)


# Lane 2 ---------------------------------------------------------------------------------------------------------------
def share_candidate(message: str) -> InsightShareTarget | None:
    """The topic, stage and timeframe a Let-your-RM-know offer would share, from her words by code; None without a topic
    word, a planning cue in her own words (not someone else's plan), or with a sensitive word."""
    found=topics_in(message)
    if not found or not PLANNING.search(message) or not SELF.search(message) or OTHERS.search(message) or SENSITIVE.search(message):return None
    timeframe=next((code for code,words in WHEN.items() if words.search(message)),'unknown')
    return InsightShareTarget(topic=found[0],stage='ready' if READY.search(message) else 'planning',timeframe=timeframe)


def may_offer(topic: str,current: TopicStatus) -> bool:
    return (current.has_rm and not current.paused and topic not in current.muted and topic not in current.prompted
        and topic not in current.meeting_topics)


def shared_line(target: InsightShareTarget | InsightShareResult) -> str:
    """What her RM will read, rendered exactly as the RM side renders it (TalkingPoint.line)."""
    return TalkingPoint(point_id=uuid4(),topic=target.topic,stage=target.stage,timeframe=target.timeframe,origin='confirmed',
        first_seen=date.today(),last_seen=date.today()).line()


def share_question(target: InsightShareTarget,rm: str | None) -> str:
    name=rm or 'your relationship manager'
    return (f'Would you like {name} to know you’re looking at {SHARE_WORDS[target.topic]}? {"She" if rm else "They"} can follow up with you. '
        f'{name[0].upper()+name[1:]} would see only: “{shared_line(target)}”, never your messages. Reply “yes” to let {"her" if rm else "them"} know or “no” '
        'for not now. Nothing is shared until you say yes, and this expires in 30 minutes.')


async def share_offer(message: str,risk: RiskAssessment | None,ctx: RequestContext) -> tuple[OfferProposal,str] | None:
    """Lane 2's rule for compose: one insight.share from Relationship & Service, or None. A failed status read offers nothing."""
    if risk is None or risk.care or risk.fixed_response is not None or risk.risk_flags:return None
    if ctx.principal.kind!='customer' or ctx.case_id is None or (target:=share_candidate(message)) is None:return None
    try:current=await status(ctx)
    except Exception as error:  # the answer stands without the offer
        log_event('share_offer_skipped',logging.WARNING,error_type=type(error).__name__)
        return None
    if not may_offer(target.topic,current):return None
    proposal=OfferProposal(task_id='share',agent='relationship',type='insight.share',target=target.model_dump(mode='json'),
        summary=f'Let {current.rm_first_name or "your relationship manager"} know you’re looking at {SHARE_WORDS[target.topic]}')
    return proposal,share_question(target,current.rm_first_name)


def points_with_lines(envelope: CustomerEnvelope[MyTalkingPoints]) -> dict:
    """Her view for the API: each point with the exact line her RM reads (TalkingPoint.line), pending during the hold."""
    data=envelope.model_dump(mode='json')
    for point,model in zip(data['data']['points'],envelope.data.points,strict=True):
        point['line']=TalkingPoint.model_validate(model.model_dump(include=set(TalkingPoint.model_fields))).line()
        point['topic_name']=TOPIC_NAME[model.topic]
    for point,model in zip(data['data']['removed'],envelope.data.removed,strict=True):  # 0110: what she removed, with Undo
        point['line']=TalkingPoint.model_validate(model.model_dump(include=set(TalkingPoint.model_fields))).line()
        point['topic_name']=TOPIC_NAME[model.topic]
    return data
