"""RM-only Brief Writer: choose live source references, then recheck them on every read and approval.

The model receives the six existing scoped readers' allowlisted evidence, never raw chats, document contents or
merchant transactions. Persistent plans carry IDs and codes; a revoked or deleted item is not copied into storage.
"""
import asyncio,json,re
from uuid import UUID,uuid5
from langgraph.graph import START,END,StateGraph
from psycopg.types.json import Jsonb
from pydantic import JsonValue,TypeAdapter
from app.schemas.contracts import RequestContext
from app.schemas.meeting_lifecycle import Claims
from app.schemas.meetings import BriefCard,BriefItem,BriefSection
from app.schemas.meeting_preparation import BriefGraphState,BriefPlan,BriefReference,BriefSelection,BriefSource,Heading,PlannedSection,PreparationInputs,PreparedBrief,SelectedRecord,StoredBrief
from app.services.agents.registry import BRIEF_WRITER
from app.services.meetings.database import ApplicationDatabase
from app.services.llm_gateway import call
from app.services.privacy import redact
from app.services.prompts import load
from app.core import auth
from app.core.config import settings
from app.core.observability import span

PROMPT=BRIEF_WRITER.version
SCHEMA_OBJECT=TypeAdapter(dict[str,JsonValue])

HEADINGS={'profile':'Profile and products','compliance':'Compliance status','requests':'Open requests','history':'Meeting history',
 'shared_topics':'Topics the client shared','transaction_insights':'Shared transaction insights','questions':'Questions to clarify'}
QUESTIONS={'confirm_goal':'Confirm the client’s goal for this item.','review_status':'Review the current status of this item.',
 'clarify_next_step':'Clarify the next step and who is responsible.','discuss_shared_topic':'Ask whether the client wants to discuss this shared topic.',
 'check_information':'Check whether this recorded information is still current.',
 'compare_product_terms':'For the agreed product discussion, which terms, charges, risks and access to funds should we compare?',
 'confirm_document_next_step':'For this outstanding document, what must the client provide next and which point needs clarification before bank review?',
 'discuss_funding_options':'Possible options to discuss for this goal, based on the points above (not advice; you advise): using the maturing time deposit, a Lombard loan against the portfolio, or property financing.',
 'review_collateral_next_step':'For the recorded collateral shortfall, which lending values and borrowing figures should the bank confirm, what options should we discuss, and who will confirm any required action and timing?'}

DEPOSIT_NEXT_STEP='RM discussion point: compare time-deposit options with the client before any placement. Confirm which option details the client wants to review.'
COLLATERAL_NEXT_STEP=(
    'RM next step: review the recorded shortfall with the client. Ask the bank which borrowing balance and lending value '
    'it will use for the review, whether it needs updated information, and when it will confirm any required action. '
    'Agree who will relay the bank’s response to the client.')
PROPERTY_FINANCING_QUESTION=(
    'Home loan: clarify the client’s purpose and timeframe for the discussion. Ask the bank what information '
    'it needs and whether the recorded collateral position affects what can be discussed.')
INVESTMENTS_QUESTION=(
    'Investments: ask which portfolio valuation date and eligible holdings the bank used for the lending value, '
    'and whether any valuations need updating before it reviews the shortfall.')
DOCUMENT_REVIEW_NEXT_STEP='Review the listed requirements and request statuses with the client. Confirm what the bank has received, what is still needed, and who will follow up by when.'

COLLATERAL = re.compile(r'\b(?:collateral|shortfall|lombard)\b',re.I)
DEPOSIT_MATURITY = re.compile(r'^Recorded date: Time deposit .*? matures on ([^.]+)\.')
FUNDING_TOPICS = frozenset({'home_loan','investments'})
DOCUMENT_REQUEST = re.compile(r'\b(?:documents?|personal details|identity|address|certification)\b',re.I)
REVIEW_CONTEXT = re.compile(r'\b(?:review|compliance|verification|checklist|due diligence)\b',re.I)
DOCUMENT_COMMITMENT = re.compile(
    r'\b(?:I|we|he|she|they|the client)\s+(?:will|agreed to)\s+(?:upload|submit|send|provide)\s+[^.!?\n]{1,160}'
    r'\b(?:agreement|documents?|certificates?|statements?|proof|forms?)\b[^.!?\n]{0,100}',re.I)
DEPOSIT_DISCUSSION = re.compile(
    r'\b(?:I|we|he|she|they|the client)\s+(?:asked|wanted|would like)\b[^.!?\n]{0,150}'
    r'\b(?:fixed|time) deposit\b[^.!?\n]{0,120}',re.I)
PRODUCT_EVIDENCE = {
    'fixed_deposit': re.compile(r'\b(?:fixed|time) deposit\b',re.I),
    'home_loan': re.compile(r'\b(?:home loan|mortgage|property financing)\b',re.I),
    'investments': re.compile(r'\b(?:investment portfolio|investment fund)\b',re.I),
    'insurance': re.compile(r'\binsurance\b',re.I),
}


def collateral_review(frame: PreparationInputs, sources: tuple[BriefSource,...]) -> bool:
    return bool(FUNDING_TOPICS.issuperset(frame.topics) and COLLATERAL.search(frame.purpose or '') and any(
        source.reference.startswith('rm_collateral_alert:') for source in sources))


def financing_timeframe_source(frame: PreparationInputs) -> UUID|None:
    if 'home_loan' not in frame.topics:return None
    current=max((item for item in frame.talking_points if item.point.topic=='home_loan' and item.point.timeframe!='unknown'),
        key=lambda item:(item.point.origin=='confirmed',item.point.last_seen,item.source_id),default=None)
    return current.source_id if current else None


def preparation_sources(frame: PreparationInputs, sources: tuple[BriefSource,...]) -> tuple[BriefSource,...]:
    """Keep optional evidence focused when the agreed purpose explicitly concerns a recorded collateral alert."""
    if not collateral_review(frame,sources):return sources
    timeframe=financing_timeframe_source(frame)
    return tuple(source for source in sources if
        source.source_id==timeframe or source.reference.startswith(('meeting_agenda:','rm_collateral_alert:'))
        or source.reference.startswith('rm_client_profile:') and source.text.startswith(
            ('Recorded investment portfolio:','Recorded Lombard borrowing:','Recorded financing product:'))
        or source.reference.startswith(('meeting_followups:','rm_tickets:','rm_meeting_notes:')) and COLLATERAL.search(source.text))


def document_context(sources: tuple[BriefSource,...]) -> tuple[tuple[BriefSource,str],...]:
    """Keep current review records and quote only the commitment from a relevant dated note."""
    items=[]
    for source in sources:
        if (source.reference.startswith('rm_review_status:') or
            source.reference.startswith('rm_tickets:') and DOCUMENT_REQUEST.search(source.text)):
            items.append((source,source.text))
        elif (source.reference.startswith('rm_meeting_notes:') and (match:=DOCUMENT_COMMITMENT.search(source.text))
            and REVIEW_CONTEXT.search(match.group())):
            items.append((source,match.group().strip()))
    return tuple(items)


def caller(ctx: RequestContext) -> Claims:
    if ctx.principal.jwt is None:raise PermissionError('Meeting preparation needs the assigned RM’s verified login')
    return Claims.model_validate(auth.verify_token(ctx.principal.jwt.get_secret_value()))


async def inputs(meeting: UUID,database: ApplicationDatabase,claims: Claims) -> PreparationInputs:
    result=await database.agent_read(BRIEF_WRITER.name,'meeting_preparation_inputs',meeting,{'p_meeting_id':meeting},claims)
    return PreparationInputs.model_validate(result)


def live_sources(frame: PreparationInputs) -> tuple[BriefSource,...]:
    result=[source.model_copy(update={'text':redact(source.text)[0]}) for source in frame.sources]
    for item in frame.talking_points:
        if item.point.origin=='inferred' and (not frame.consent['chat_topics'] or item.grant_id is None):
            raise PermissionError('An inferred topic arrived without its required current grant; brief generation is refused')
        result.append(BriefSource(source_id=item.source_id,label=item.point.brief_label(),text=item.point.line(),reference=item.reference,
            category=item.category,grant_id=item.grant_id))
    if len({source.source_id for source in result})!=len(result):raise ValueError('Preparation readers returned duplicate source references')
    return tuple(result)


def render(frame: PreparationInputs,stored: StoredBrief) -> PreparedBrief:
    live=live_sources(frame)
    sources={source.source_id:source for source in live}
    relevant={source.source_id for source in preparation_sources(frame,live)}
    focus=collateral_review(frame,live)
    agenda={key:source for key,source in sources.items() if source.reference.startswith('meeting_agenda:')}
    commitments={key:source for key,source in sources.items() if key in relevant and source.reference.startswith('meeting_followups:')}
    alerts={key:source for key,source in sources.items() if source.reference.startswith('rm_collateral_alert:')}
    collateral_refs={'rm_client_profile:'+source.reference.split(':',1)[1] for source in alerts.values()}
    collateral={key:source for key,source in sources.items()
        if source.reference in collateral_refs and source.text.startswith('Recorded Lombard borrowing:')}
    portfolios={key:source for key,source in sources.items() if focus and 'investments' in frame.topics
        and source.reference in collateral_refs and source.text.startswith('Recorded investment portfolio:')}
    financing={key:source for key,source in sources.items() if focus and source.reference in collateral_refs
        and source.text.startswith('Recorded financing product: securities_backed')}
    documents=document_context(live)[:15] if 'compliance_review' in frame.topics and not focus else ()
    prior_deposit=None
    maturities={}
    if 'fixed_deposit' in frame.topics and not focus:
        prior_deposit=next(((source,match.group().strip()) for source in live if source.reference.startswith('rm_meeting_notes:')
            and (match:=DEPOSIT_DISCUSSION.search(source.text))),None)
        if not frame.consent['transaction_insights']:
            maturities={source.source_id:(source,match.group(1)) for source in live
                if source.reference.startswith('rm_client_profile:') and (match:=DEPOSIT_MATURITY.match(source.text))}
    mandatory=agenda.keys()|commitments.keys()|alerts.keys()|collateral.keys()|portfolios.keys()|financing.keys()|maturities.keys()|{source.source_id for source,_ in documents}
    deposit_items=[]
    if prior_deposit:mandatory.add(prior_deposit[0].source_id)
    manifest={item.source_id:item for item in stored.source_manifest}
    removed={item.source_id:item for item in frame.removed_sources}
    sections: list[BriefSection]=[];withheld=0
    if agenda:
        sections.append(BriefSection(heading='Agreed meeting agenda',items=[
            BriefItem(label='record',text=source.text,sources=[source.reference],category=None) for source in agenda.values()]))
    if prior_deposit:
        source,excerpt=prior_deposit
        deposit_items.append(BriefItem(label='record',text='Earlier client request: '+excerpt,sources=[source.reference]))
        if 'compar' in excerpt.casefold():
            deposit_items.append(BriefItem(label='ai_suggestion',text=DEPOSIT_NEXT_STEP,sources=[source.reference]))
    recorded={item.source_id for section in stored.plan.sections for item in section.items if item.kind=='record'}|mandatory
    for section in stored.plan.sections:
        items: list[BriefItem]=[]
        if section.heading=='history' and len(section.items)+len(deposit_items)<=30:
            items.extend(deposit_items)
            deposit_items=[]
        for reference in section.items:
            source=sources.get(reference.source_id)
            if source is not None and source.source_id not in relevant:continue
            if source is not None and source.source_id in mandatory and reference.kind=='record':continue
            if source is None:
                withheld+=1
                if reference.kind=='question':continue
                missing=removed.get(reference.source_id)
                text='This previously referenced information is no longer shared or available.'
                if missing is not None:
                    label='Removed' if missing.reason=='removed' else 'Access ended'
                    text=label+(' on '+missing.removed_on.isoformat() if missing.removed_on else '')+'.'
                items.append(BriefItem(label='gap',text=text,sources=[],category=manifest[reference.source_id].category))
                continue
            if reference.kind=='question' and reference.source_id in mandatory:continue
            text=source.text if reference.kind=='record' else QUESTIONS[reference.question]+(' Referenced record: '+source.text if reference.source_id not in recorded else '')
            items.append(BriefItem(label=source.label if reference.kind=='record' else 'ai_suggestion',text=text,sources=[source.reference],category=source.category))
        if items:sections.append(BriefSection(heading=HEADINGS[section.heading],items=items))
    if deposit_items:sections.append(BriefSection(heading='Meeting history',items=deposit_items))
    if maturities:
        sections.append(BriefSection(heading='Fixed deposit maturity',items=[
            BriefItem(label='record',text='Time deposit maturity: '+date,sources=[source.reference])
            for source,date in tuple(maturities.values())[:30]]))
    if commitments:
        sections.append(BriefSection(heading='Outstanding follow-ups and RM tasks',items=[
            BriefItem(label='record',text=source.text,sources=[source.reference],category=source.category) for source in commitments.values()]))
    if portfolios:
        sections.append(BriefSection(heading='Recorded investment portfolio',items=[
            BriefItem(label='record',text=source.text,sources=[source.reference]) for source in tuple(portfolios.values())[:30]]))
    if alerts:
        alert_items=[
            BriefItem(label='record',text=source.text,sources=[source.reference],category=source.category)
            for source in alerts.values()]
        for source in collateral.values():
            alert_items.append(BriefItem(label='record',text=source.text,sources=[source.reference]))
        for source in financing.values():
            alert_items.append(BriefItem(label='record',text=source.text.partition('. Outstanding:')[0]+'.',sources=[source.reference]))
        alert_items.extend(BriefItem(label='ai_suggestion',text=COLLATERAL_NEXT_STEP if focus else QUESTIONS['review_collateral_next_step'],sources=[source.reference])
            for source in alerts.values())
        sections.append(BriefSection(heading='Recorded collateral alert',items=alert_items))
    if focus and agenda:
        questions=[question for topic,question in (
            ('home_loan',PROPERTY_FINANCING_QUESTION),('investments',INVESTMENTS_QUESTION)) if topic in frame.topics]
        if questions:
            references=[next(iter(agenda.values())).reference,next(iter(alerts.values())).reference]
            sections.append(BriefSection(heading='Questions for the agreed agenda',items=[
                BriefItem(label='ai_suggestion',text=question,sources=references) for question in questions]))
    if documents:
        items=[]
        current_refs=[]
        for source,excerpt in documents:
            items.append(BriefItem(label='record',text=excerpt,sources=[source.reference],category=source.category))
            if excerpt!=source.text:
                question='Confirm whether this recorded commitment was completed and who will follow up: '+excerpt
                items.append(BriefItem(label='ai_suggestion',text=question,sources=[source.reference],category=source.category))
            else:
                current_refs.append(source.reference)
        if current_refs:
            items.append(BriefItem(label='ai_suggestion',text=DOCUMENT_REVIEW_NEXT_STEP,sources=list(dict.fromkeys(current_refs))[:10]))
        sections.append(BriefSection(heading='Document review and follow-ups',items=items))
    if frame.gaps:sections.append(BriefSection(heading='Information not available',items=[BriefItem(label='gap',text=gap.text,sources=[],category=gap.category) for gap in frame.gaps]))
    changed=stored.source_hash!=frame.source_hash or stored.revision!=frame.revision
    card=BriefCard(meeting_id=frame.meeting_id,version=stored.version,status='approved' if stored.status=='approved' and not changed else 'draft',
        sections=sections,documents=[],consent=frame.consent,withheld=withheld,as_of=frame.as_of)
    return PreparedBrief(card=card,brief_id=stored.brief_id,revision=stored.revision,content_hash=stored.content_hash,sources_changed=changed,decision=stored.status,review_note=stored.review_note)


async def current(meeting: UUID,database: ApplicationDatabase,claims: Claims) -> StoredBrief|None:
    result=await asyncio.to_thread(database.call,'meeting_current_brief',{'p_meeting_id':meeting},claims)
    return None if result is None else StoredBrief.model_validate(result)


async def read(meeting: UUID,ctx: RequestContext) -> PreparedBrief|None:
    database=ApplicationDatabase(ctx);claims=caller(ctx)
    frame=await inputs(meeting,database,claims);stored=await current(meeting,database,claims)
    return None if stored is None else render(frame,stored)


def usable(selection: BriefSelection,frame: PreparationInputs,sources: tuple[BriefSource,...]) -> BriefSelection:
    """Keep the stored references focused on the agreed agenda and current sources."""
    known={source.source_id:source for source in sources}
    focus=collateral_review(frame,sources)
    timeframe=financing_timeframe_source(frame) if focus else None
    items=[]
    for item in selection.items:
        source=known.get(item.source_id)
        if source is None:raise ValueError('Brief Writer selected an unavailable source reference')
        if item.source_id==timeframe:continue
        if item.kind=='question':
            if focus and item.question!='review_collateral_next_step':continue
            if 'compliance_review' in frame.topics and item.question=='review_status':continue
            if item.question=='compare_product_terms' and not any(
                pattern.search(source.text) for topic,pattern in PRODUCT_EVIDENCE.items() if topic in frame.topics):continue
            if item.question=='confirm_document_next_step' and not (
                'compliance_review' in frame.topics and source.reference.startswith('rm_review_status:')):continue
            if item.question=='discuss_funding_options' and not FUNDING_TOPICS.intersection(frame.topics):continue
        items.append(item)
    if timeframe is not None and timeframe in known:
        heading='questions' if known[timeframe].label=='ai_suggestion' else 'shared_topics'
        items=[SelectedRecord(source_id=timeframe,heading=heading,kind='record',question=None),*items[:29]]
    if not items:
        agenda=next((source for source in sources if source.reference.startswith('meeting_agenda:')),None)
        if agenda is None:raise ValueError('Brief Writer selected no relevant sources')
        return BriefSelection.model_validate({'items':[{'source_id':agenda.source_id,'heading':'profile','kind':'record','question':None}]})
    return selection.model_copy(update={'items':tuple(items)})


def selection_plan(selection: BriefSelection) -> BriefPlan:
    """Group a globally bounded model selection into the existing reference-only SQL storage contract."""
    groups: dict[Heading,list[BriefReference]]={};reference=TypeAdapter(BriefReference)
    for item in selection.items:
        groups.setdefault(item.heading,[]).append(reference.validate_python(item.model_dump(exclude={'heading'})))
    return BriefPlan(sections=tuple(PlannedSection(heading=heading,items=tuple(items)) for heading,items in groups.items()))


def selection_schema(sources: tuple[BriefSource,...]) -> dict[str,JsonValue]:
    """Constrain both selection kinds to this authorised frame; SQL still rechecks every source before saving."""
    choices: list[JsonValue]=[str(source.source_id) for source in sources]
    if not choices:raise ValueError('No authorised source references are available for this meeting brief')
    schema=SCHEMA_OBJECT.validate_python(BriefSelection.model_json_schema())
    definitions=SCHEMA_OBJECT.validate_python(schema['$defs'])
    selected: dict[str,JsonValue]={}
    for name in ('SelectedRecord','SelectedQuestion'):
        definition=SCHEMA_OBJECT.validate_python(definitions[name])
        properties=SCHEMA_OBJECT.validate_python(definition['properties'])
        source=SCHEMA_OBJECT.validate_python(properties['source_id'])
        selected[name]={**definition,'properties':{**properties,'source_id':{**source,'enum':choices}}}
    return {**schema,'$defs':{**definitions,**selected}}


async def generate(meeting: UUID,frame: PreparationInputs,database: ApplicationDatabase,claims: Claims,command: UUID,review_note: str|None) -> StoredBrief:
    async def choose(state: BriefGraphState) -> dict[str,BriefPlan]:
        prompt=load(BRIEF_WRITER.prompt,PROMPT)
        sources=preparation_sources(frame,live_sources(frame))
        payload={'purpose':redact(frame.purpose)[0] if frame.purpose else None,'topics':frame.topics,'review_note':redact(review_note)[0] if review_note else None,
            'sources':[source.model_dump(mode='json',exclude={'grant_id'}) for source in sources],'gaps':[gap.model_dump() for gap in frame.gaps]}
        with span('meeting.agent','agent',agent=BRIEF_WRITER.name,intent='prepare_brief',side='meeting',path='workflow',prompt_version=prompt['id']):
            result=await call({'agent':BRIEF_WRITER.prompt,'id':prompt['id']},prompt['system'],[('user',json.dumps(payload,ensure_ascii=False))],selection_schema(sources),model=settings.meeting_model or None)
            return {'plan':selection_plan(usable(BriefSelection.model_validate(result.data),frame,sources))}

    async def save(state: BriefGraphState) -> dict[str,UUID]:
        if state.plan is None:raise RuntimeError('Brief graph reached storage without a validated reference plan')
        value=await asyncio.to_thread(database.agent_call,BRIEF_WRITER.name,'meeting_save_brief',meeting,{'p_meeting_id':meeting,'p_revision':frame.revision,
            'p_source_hash':frame.source_hash,'p_plan':Jsonb(state.plan.model_dump(mode='json')),'p_command_id':command},claims)
        return {'brief_id':StoredBrief.model_validate(value).brief_id}

    graph=StateGraph(BriefGraphState);graph.add_node('choose_scoped_references',choose);graph.add_node('save_after_rechecking_sources',save)
    graph.add_edge(START,'choose_scoped_references');graph.add_edge('choose_scoped_references','save_after_rechecking_sources');graph.add_edge('save_after_rechecking_sources',END)
    await graph.compile().ainvoke(BriefGraphState(meeting_id=meeting,plan=None,brief_id=None))
    stored=await current(meeting,database,claims)
    if stored is None:raise RuntimeError('Brief Writer completed without a stored reference plan')
    return stored


async def prepare(meeting: UUID,ctx: RequestContext) -> PreparedBrief:
    database=ApplicationDatabase(ctx);claims=caller(ctx)
    frame=await inputs(meeting,database,claims);stored=await current(meeting,database,claims)
    if stored is None:stored=await generate(meeting,frame,database,claims,uuid5(meeting,'prepare:'+frame.source_hash),None)
    return render(await inputs(meeting,database,claims),stored)


async def refresh(meeting: UUID,ctx: RequestContext,command: UUID) -> PreparedBrief:
    database=ApplicationDatabase(ctx);claims=caller(ctx)
    frame=await inputs(meeting,database,claims);previous=await current(meeting,database,claims)
    stored=await generate(meeting,frame,database,claims,command,previous.review_note if previous else None)
    return render(await inputs(meeting,database,claims),stored)
