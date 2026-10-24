"""Resume only the post-review portion of an authenticated, signed durable graph.

Customer decisions are ledger reads, never resume arguments. A private database lease replaces
the turn run id, preventing two responders or a cancelled worker from persisting the reply.
"""
from uuid import UUID,uuid4
import httpx
from langgraph.types import Command
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.core import data_api
from app.core.observability import log_event
from app.schemas.contracts import RequestContext
from app.schemas.conversations import ConversationAccess,TurnResponse
from app.schemas.events import AnswerEvent,WorkflowPreview,WorkflowStatus
from app.schemas.workflows import OwnedWorkflow,PipelineContext
from app.services import audit,conversations,workflow_store
from app.services.actions import ActionStateError
from app.services.actions import store as action_store
from app.services.actions.store import ActionIntegrityError,ActionNotFound
from app.services.authorization import PolicyDenied
from app.services.checkpoint_codec import CheckpointIntegrityError
from app.services.checkpoints import PostgrestCheckpointSaver,signing_key
from app.services.conversations import ConversationIntegrityError


async def preview(turn_id: UUID,ctx: RequestContext) -> WorkflowPreview:
    """Rebuild a waiting reply's sections from its signed checkpoint; reading never resumes or decides anything."""
    from app.services.orchestrator import build_graph,paused_preview
    with data_api.connect(ctx) as db:
        current=await workflow_store.load(turn_id,ctx,db)
        if current.workflow.state!='awaiting_confirmation':raise ActionStateError('not_waiting')
        scoped=ctx.model_copy(update={'case_id':current.turn.case_id,'agent':None})
        access=ConversationAccess(session_id=current.turn.session_id,guest_token=None)
        saver=PostgrestCheckpointSaver(db,access,scoped,current.turn,signing_key())
        snapshot=await build_graph(saver).aget_state({'configurable':{'thread_id':str(current.turn.case_id)}})
        values=snapshot.values
        if values.get('workflow_version')!='v2-actions-v1' or not values.get('pending_reviews') or snapshot.next!=('review_actions',):
            raise CheckpointIntegrityError('The saved workflow is not waiting at its review step')
        outcomes=[await action_store.load(item.command_id,scoped,db) for item in values['pending_reviews']]
    _,answer=paused_preview(values,outcomes,current.workflow.pending_commands)
    audit.add('data','data.read',resource_type='workflow.preview',resource_id=str(turn_id),
        details={'tool':'workflow.preview','section_count':len(answer.sections or [])})
    return WorkflowPreview(workflow=current.workflow,preview=answer)


async def resume(turn_id: UUID,ctx: RequestContext) -> WorkflowStatus:
    from app.services.orchestrator import build_graph
    worker=uuid4()
    with data_api.connect(ctx) as db:
        lease=await workflow_store.acquire(turn_id,worker,ctx,db)
        if not lease.acquired:return lease.workflow
        current=ctx.model_copy(update={'case_id':lease.turn.case_id,'agent':None})
        access=ConversationAccess(session_id=lease.turn.session_id,guest_token=None)
        owned=OwnedWorkflow(access=access,turn=lease.turn)
        try:
            saver=PostgrestCheckpointSaver(db,access,current,lease.turn,signing_key())
            graph=build_graph(saver)
            config={'configurable':{'thread_id':str(lease.turn.case_id)}}
            snapshot=await graph.aget_state(config)
            if snapshot.values.get('workflow_version')!='v2-actions-v1' or not snapshot.values.get('pending_reviews'):
                raise CheckpointIntegrityError('The saved workflow does not contain the expected reviewed actions')
            if any(node not in ('review_actions','settle_actions','compose') for node in snapshot.next):
                raise CheckpointIntegrityError('A confirmed workflow cannot rerun planning or model dispatch')
            if snapshot.next:
                value=Command(resume={'turn_id':str(turn_id)}) if snapshot.interrupts else None
                result=await graph.ainvoke(value,config,context=PipelineContext(request=current,workflow=owned),durability='sync')
                if result.get('__interrupt__'):raise CheckpointIntegrityError('The workflow still requires a customer decision')
            else:result=snapshot.values
            answer=AnswerEvent.model_validate(result['answer'])
            await conversations.finish_turn(TurnResponse(turn=lease.turn,response=None),answer,access,current,db)
        except (APIError,httpx.TransportError,ValidationError,CheckpointIntegrityError,ConversationIntegrityError,ActionIntegrityError,ActionNotFound,PolicyDenied) as error:
            log_event('workflow_resume_failed',turn_id=str(turn_id),error_type=type(error).__name__,
                db_code=data_api.database_code(error) if isinstance(error,APIError) else None)
            # A concurrent cancellation is terminal and wins over a failed or late resume.
            latest=await workflow_store.load(turn_id,current,db)
            if latest.turn.state!='processing':return latest.workflow
            return await workflow_store.failed(turn_id,worker,current,db)
        return (await workflow_store.load(turn_id,current,db)).workflow
