"""Separate Proposal Drafter graph. Internal brief and banking data cannot enter its closed context model."""
import asyncio,json
from uuid import UUID
from langgraph.graph import START,END,StateGraph
from psycopg.types.json import Jsonb
from app.core.config import settings
from app.schemas.contracts import RequestContext
from app.schemas.meeting_packs import AgendaCode,PackGraphState,PackInputs,PackPlan,PackQuestion,PackRequest,PackView,ProductChoice,ProposalContext,StoredPack
from app.core.observability import span
from app.services.agents.registry import PROPOSAL_DRAFTER
from app.services.meetings.database import ApplicationDatabase
from app.services.meetings.preparation import caller
from app.services.llm_gateway import call
from app.services.prompts import load
from app.services.privacy import redact


def safe_context(frame: PackInputs) -> ProposalContext:
    return ProposalContext(purpose=redact(frame.purpose)[0] if frame.purpose else None,topics=frame.topics,
        catalogue=tuple(ProductChoice(item_id=item.item_id,name=item.name,topic=item.topic,currency=item.currency) for item in frame.catalogue),
        amount_currencies=tuple(sorted({amount.currency for amount in frame.amounts})))

def with_discussion_items(plan: PackPlan,frame: PackInputs) -> PackPlan:
    required: list[PackQuestion]=[]
    product_count=len(plan.product_ids)
    product_agenda: AgendaCode='product_terms' if product_count==1 else 'options'
    agenda=[]
    for code in plan.agenda:
        if code in ('options','product_terms'):
            if product_count and product_agenda not in agenda:agenda.append(product_agenda)
        else:agenda.append(code)
    if product_count and product_agenda not in agenda:agenda.append(product_agenda)
    if product_count==1:required.append('review_catalogue_product')
    if product_count>1:required.append('compare_catalogue_options')
    if 'compliance_review' in frame.topics:required.append('confirm_document_review')
    replaced={'compare_catalogue_options','review_catalogue_product','review_options','ask_product_risks','confirm_document_review'}
    if 'confirm_document_review' in required:replaced.add('confirm_documents')
    questions=(*required,*(question for question in plan.questions if question not in replaced))
    return plan.model_copy(update={'agenda':tuple(agenda) or ('goals',),'questions':questions[:6]})


async def read(meeting: UUID,ctx: RequestContext) -> PackView|None:
    result=await asyncio.to_thread(ApplicationDatabase(ctx).call,'meeting_current_pack',{'p_meeting_id':meeting},caller(ctx))
    return None if result is None else PackView.model_validate(result)


async def draft(meeting: UUID,body: PackRequest,ctx: RequestContext) -> PackView:
    database=ApplicationDatabase(ctx);claims=caller(ctx)
    amounts=[{**amount.model_dump(mode='json'),'label':redact(amount.label)[0]} for amount in body.amounts]
    frame=PackInputs.model_validate(await database.agent_read(PROPOSAL_DRAFTER.name,'meeting_pack_inputs',meeting,{'p_meeting_id':meeting,'p_amounts':Jsonb(amounts)},claims))
    existing=await read(meeting,ctx)
    if existing is not None and existing.document_id==body.command_id:
        if existing.amounts!=frame.amounts:raise ValueError('This pack command was already used with different stated amounts; use a new command ID')
        return existing
    async def compose(state: PackGraphState) -> dict[str,PackPlan]:
        prompt=load(PROPOSAL_DRAFTER.prompt,PROPOSAL_DRAFTER.version)
        with span('meeting.agent','agent',agent=PROPOSAL_DRAFTER.name,intent='draft_pack',side='meeting',path='workflow',prompt_version=prompt['id']):
            result=await call({'agent':PROPOSAL_DRAFTER.prompt,'id':prompt['id']},prompt['system'],[('user',safe_context(frame).model_dump_json())],PackPlan.model_json_schema(),model=settings.meeting_model or None)
            plan=PackPlan.model_validate(result.data)
            if not set(plan.product_ids)<={item.item_id for item in frame.catalogue}:raise ValueError('Proposal Drafter selected a catalogue item outside the agreed agenda')
            return {'plan':with_discussion_items(plan,frame)}
    async def save(state: PackGraphState) -> dict[str,UUID]:
        if state.plan is None:raise RuntimeError('Pack graph reached storage without a checked plan')
        result=await asyncio.to_thread(database.agent_call,PROPOSAL_DRAFTER.name,'meeting_save_pack',meeting,{'p_meeting_id':meeting,'p_revision':frame.revision,'p_source_hash':frame.source_hash,
            'p_amounts':Jsonb(amounts),'p_plan':Jsonb(state.plan.model_dump(mode='json')),'p_command_id':body.command_id},claims)
        return {'document_id':StoredPack.model_validate(result).document_id}
    graph=StateGraph(PackGraphState);graph.add_node('compose_client_safe_plan',compose);graph.add_node('insert_current_catalogue_terms',save)
    graph.add_edge(START,'compose_client_safe_plan');graph.add_edge('compose_client_safe_plan','insert_current_catalogue_terms');graph.add_edge('insert_current_catalogue_terms',END)
    await graph.compile().ainvoke(PackGraphState(meeting_id=meeting,plan=None,document_id=None))
    document=await read(meeting,ctx)
    if document is None:raise RuntimeError('Proposal Drafter completed without a stored document')
    return document
