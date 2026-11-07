"""Real bounded LangGraph generation with database-owned durable phase state.

The parent lifecycle owns queue checkpoints. Its Minutes Writer operation persists
fenced chunks and the final draft directly; it does not create a nested graph that
inherits the parent saver. The separate RM-notes graph runs under the caller JWT.
Cues and runtime identities stay outside checkpoints; SQL rechecks the final write.
"""
import asyncio
import logging
from app.core.observability import log_event
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from psycopg import ProgrammingError
from psycopg.types.json import Jsonb

from app.services.llm_gateway import LLMError,LLMBudgetExceeded
from app.services.meetings.database import ApplicationDatabase, WorkerDatabase
from app.schemas.meeting_lifecycle import Claim,Claims,DraftReceipt,GraphState,NotesContext
from app.services.meetings.writer import generate
from app.services.meetings.chunks import generate_queued


def notes_graph(context: NotesContext, database: ApplicationDatabase, claims: Claims, command_id: UUID) -> CompiledStateGraph:
    async def draft(state: GraphState) -> dict[str, UUID]:
        content = await generate(context)
        receipt = DraftReceipt.model_validate(await asyncio.to_thread(database.call, 'meeting_save_reviewed_draft', {
            'p_meeting_id': context.meeting_id, 'p_source_id': context.source_id, 'p_source_hash': context.source_hash,
            'p_revision': context.revision, 'p_consent_epoch': context.consent_epoch,
            'p_content': Jsonb(content.model_dump(mode='json')), 'p_command_id': command_id,'p_review_id':context.review_id}, claims))
        return {'draft_id': receipt.draft_id}

    graph = StateGraph(GraphState)
    graph.add_node('draft_and_guarded_save', draft)
    graph.add_edge(START, 'draft_and_guarded_save')
    graph.add_edge('draft_and_guarded_save', END)
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
    try:
        content = await generate_queued(context,database,claim)
        receipt = DraftReceipt.model_validate(await asyncio.to_thread(database.save,claim,context,content))
    except LLMBudgetExceeded:
        database.reject(claim,'budget_exceeded')
        raise
    except LLMError as error:
        log_event('meeting_model_failed',level=logging.WARNING,event_id=claim.event_id,error_code=error.reason)
        database.fail(claim, error.reason)
        raise
    except ValidationError:
        database.reject(claim, 'model_output_invalid')
        raise
    except ValueError:
        database.reject(claim,'meeting_output_capacity')
        raise
    except ProgrammingError as error:
        if error.sqlstate in ('P1001', '42501', '22023', '23505'):
            database.reject(claim, 'source_or_output_invalid')
        raise
    database.finish(claim)
    return receipt.draft_id
