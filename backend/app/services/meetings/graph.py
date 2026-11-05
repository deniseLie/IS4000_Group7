"""Real bounded LangGraph generation with database-owned durable phase state.

Graph state contains identifiers only. Cues and runtime identities stay outside
checkpoints. The source/job revision is rechecked at the final SQL write.
"""
import asyncio
import json
import logging
from app.core.observability import log_event
from uuid import UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from psycopg import ProgrammingError
from psycopg.types.json import Jsonb

from app.services.llm_gateway import LLMError, call
from app.services.prompts import load
from app.services.meetings.database import ApplicationDatabase, WorkerDatabase
from app.schemas.meeting_lifecycle import Action, Claim, Claims, DraftReceipt, GenerationContext, GraphState, JobContext, MinutesContent, MinutesProposal, NotesContext



def bind_action_ids(proposal: MinutesProposal, source_id: UUID) -> MinutesContent:
    actions = tuple(Action.model_validate({**action.model_dump(), 'action_id': uuid5(source_id,
        json.dumps(action.model_dump(mode='json'), sort_keys=True))}) for action in proposal.actions)
    return MinutesContent(title=proposal.title, summary=proposal.summary, decisions=proposal.decisions,
        actions=actions, client_recap=compose_recap(proposal))


def compose_recap(proposal: MinutesProposal) -> str:
    """Keep every agreed commitment in the exact recap the RM reviews, before hashing or release."""
    labels = {'rm': 'Your RM', 'client': 'You', 'unassigned': 'Owner to confirm'}
    parts: list[str] = [' '.join(item.text for item in proposal.summary)]
    parts.extend(labels[action.owner] + ': ' + action.text for action in proposal.actions if action.commitment == 'agreed')
    return '\n'.join(parts)


async def generate(context: GenerationContext) -> MinutesContent:
    prompt = load('minutes_writer', 'v1')
    system = prompt['system']
    prompt_id = prompt['id']
    payload = {'source_kind': context.source_kind, 'cues': [cue.model_dump(mode='json') for cue in context.cues]}
    result = await call({'agent': 'minutes_writer', 'id': prompt_id}, system,
        [('user', json.dumps(payload))], MinutesProposal.model_json_schema())
    log_event('meeting_model_completed', prompt_version=prompt_id, input_tokens=result.input_tokens,
        output_tokens=result.output_tokens, model=result.model, latency_ms=result.latency_ms)
    proposal = MinutesProposal.model_validate(result.data)
    return bind_action_ids(proposal, context.source_id)


def notes_graph(context: NotesContext, database: ApplicationDatabase, claims: Claims, command_id: UUID) -> CompiledStateGraph:
    async def draft(state: GraphState) -> dict[str, UUID]:
        content = await generate(context)
        receipt = DraftReceipt.model_validate(await asyncio.to_thread(database.call, 'meeting_save_generated_draft', {
            'p_meeting_id': context.meeting_id, 'p_source_id': context.source_id, 'p_source_hash': context.source_hash,
            'p_revision': context.revision, 'p_consent_epoch': context.consent_epoch,
            'p_content': Jsonb(content.model_dump(mode='json')), 'p_command_id': command_id}, claims))
        return {'draft_id': receipt.draft_id}

    graph = StateGraph(GraphState)
    graph.add_node('draft_and_guarded_save', draft)
    graph.add_edge(START, 'draft_and_guarded_save')
    graph.add_edge('draft_and_guarded_save', END)
    return graph.compile()


def transcript_graph(context: JobContext, database: WorkerDatabase, claim: Claim) -> CompiledStateGraph:
    async def draft(state: GraphState) -> dict[str, UUID]:
        content = await generate(context)
        receipt = DraftReceipt.model_validate(database.save(claim, context, content))
        return {'draft_id': receipt.draft_id}

    graph = StateGraph(GraphState)
    graph.add_node('draft_and_fenced_save', draft)
    graph.add_edge(START, 'draft_and_fenced_save')
    graph.add_edge('draft_and_fenced_save', END)
    return graph.compile()


async def process_transcript(database: WorkerDatabase, claim: Claim) -> UUID:
    try:
        context = database.context(claim)
    except ProgrammingError as error:
        if error.sqlstate in ('P1001', '42501'):
            database.reject(claim, 'source_authorization_changed')
        raise
    if context.draft_id is not None:
        # A previous process committed the draft and crashed before acknowledging the queue.
        database.finish(claim)
        return context.draft_id
    graph = transcript_graph(context, database, claim)
    try:
        result = await graph.ainvoke(GraphState(meeting_id=context.meeting_id, source_id=context.source_id, draft_id=None))
    except LLMError as error:
        log_event('meeting_model_failed',level=logging.WARNING,event_id=claim.event_id,error_code=error.reason)
        database.fail(claim, error.reason)
        raise
    except ValidationError:
        database.reject(claim, 'model_output_invalid')
        raise
    except ProgrammingError as error:
        if error.sqlstate in ('P1001', '42501', '22023', '23505'):
            database.reject(claim, 'source_or_output_invalid')
        raise
    state = GraphState.model_validate(result)
    if state.draft_id is None:
        raise RuntimeError('Meeting graph completed without a persisted draft')
    database.finish(claim)
    return state.draft_id
