"""RM-only Brief Writer: choose live source references, then recheck them on every read and approval.

The model receives the six existing scoped readers' allowlisted evidence, never raw chats, document contents or
merchant transactions. Persistent plans carry IDs and codes; a revoked or deleted item is not copied into storage.
"""
import asyncio,json
from uuid import UUID,uuid5
from langgraph.graph import START,END,StateGraph
from psycopg.types.json import Jsonb
from pydantic import JsonValue,TypeAdapter
from app.schemas.contracts import RequestContext
from app.schemas.meeting_lifecycle import Claims
from app.schemas.meetings import BriefCard,BriefItem,BriefSection
from app.schemas.meeting_preparation import BriefGraphState,BriefPlan,BriefReference,BriefSelection,BriefSource,Heading,PlannedSection,PreparationInputs,PreparedBrief,StoredBrief
from app.services.meetings.database import ApplicationDatabase
from app.services.llm_gateway import call
from app.services.privacy import redact
from app.services.prompts import load
from app.core import auth

PROMPT='v3'
SCHEMA_OBJECT=TypeAdapter(dict[str,JsonValue])

HEADINGS={'profile':'Profile and products','compliance':'Compliance status','requests':'Open requests','history':'Meeting history',
 'shared_topics':'Topics the client shared','transaction_insights':'Shared transaction insights','questions':'Questions to clarify'}
QUESTIONS={'confirm_goal':'Confirm the client’s goal for this item.','review_status':'Review the current status of this item.',
 'clarify_next_step':'Clarify the next step and who is responsible.','discuss_shared_topic':'Ask whether the client wants to discuss this shared topic.',
 'check_information':'Check whether this recorded information is still current.',
 'compare_product_terms':'Which available product terms, charges, risks and access to funds should we compare for the client’s goal?',
 'confirm_document_next_step':'For this outstanding document, what must the client provide next and which point needs clarification before bank review?',
 'review_collateral_next_step':'For the recorded collateral shortfall, which lending values and borrowing figures should the bank confirm, what options should we discuss, and who will confirm any required action and timing?'}


def caller(ctx: RequestContext) -> Claims:
    if ctx.principal.jwt is None:raise PermissionError('Meeting preparation needs the assigned RM’s verified login')
    return Claims.model_validate(auth.verify_token(ctx.principal.jwt.get_secret_value()))


async def inputs(meeting: UUID,database: ApplicationDatabase,claims: Claims) -> PreparationInputs:
    result=await asyncio.to_thread(database.call,'meeting_preparation_inputs',{'p_meeting_id':meeting},claims)
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
    sources={source.source_id:source for source in live_sources(frame)}
    commitments={key:source for key,source in sources.items() if source.reference.startswith('meeting_followups:')}
    alerts={key:source for key,source in sources.items() if source.reference.startswith('rm_collateral_alert:')}
    mandatory=commitments.keys()|alerts.keys()
    manifest={item.source_id:item for item in stored.source_manifest}
    removed={item.source_id:item for item in frame.removed_sources}
    sections: list[BriefSection]=[];withheld=0
    recorded={item.source_id for section in stored.plan.sections for item in section.items if item.kind=='record'}|mandatory
    for section in stored.plan.sections:
        items: list[BriefItem]=[]
        for reference in section.items:
            source=sources.get(reference.source_id)
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
            text=source.text if reference.kind=='record' else QUESTIONS[reference.question]+(' Referenced record: '+source.text if reference.source_id not in recorded else '')
            items.append(BriefItem(label=source.label if reference.kind=='record' else 'ai_suggestion',text=text,sources=[source.reference],category=source.category))
        if items:sections.append(BriefSection(heading=HEADINGS[section.heading],items=items))
    if commitments:
        sections.append(BriefSection(heading='Outstanding follow-ups and RM tasks',items=[
            BriefItem(label='record',text=source.text,sources=[source.reference],category=source.category) for source in commitments.values()]))
    if alerts:
        sections.append(BriefSection(heading='Recorded collateral alert',items=[
            BriefItem(label='record',text=source.text,sources=[source.reference],category=source.category) for source in alerts.values()]))
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
        prompt=load('brief_writer',PROMPT)
        payload={'purpose':redact(frame.purpose)[0] if frame.purpose else None,'topics':frame.topics,'review_note':redact(review_note)[0] if review_note else None,
            'sources':[source.model_dump(mode='json',exclude={'grant_id'}) for source in live_sources(frame)],'gaps':[gap.model_dump() for gap in frame.gaps]}
        result=await call({'agent':'brief_writer','id':prompt['id']},prompt['system'],[('user',json.dumps(payload,ensure_ascii=False))],selection_schema(live_sources(frame)))
        plan=selection_plan(BriefSelection.model_validate(result.data));known={source.source_id for source in live_sources(frame)}
        if any(item.source_id not in known for section in plan.sections for item in section.items):raise ValueError('Brief Writer selected an unavailable source reference')
        return {'plan':plan}

    async def save(state: BriefGraphState) -> dict[str,UUID]:
        if state.plan is None:raise RuntimeError('Brief graph reached storage without a validated reference plan')
        value=await asyncio.to_thread(database.call,'meeting_save_brief',{'p_meeting_id':meeting,'p_revision':frame.revision,
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
