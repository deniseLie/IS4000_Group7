"""Typed, customer-scoped workflow lifecycle RPCs; no credentials enter graph snapshots."""
from uuid import UUID
from postgrest import SyncPostgrestClient
from app.core.data_api import execute
from app.schemas.contracts import RequestContext
from app.schemas.events import WorkflowStatus
from app.schemas.workflows import WorkflowLease,WorkflowSnapshot
from app.services.conversations import ConversationIntegrityError


def checked(status: WorkflowStatus,turn_id: UUID,ctx: RequestContext) -> WorkflowStatus:
    if status.turn_id!=turn_id or status.customer_id!=ctx.principal.customer_id:
        raise ConversationIntegrityError('Workflow identity does not match the requested turn and customer')
    return status


async def load(turn_id: UUID,ctx: RequestContext,db: SyncPostgrestClient) -> WorkflowSnapshot:
    response=await execute(lambda:db.rpc('workflow_read_customer',{'p_turn_id':str(turn_id),
        'p_customer_id':ctx.principal.customer_id}).execute(),'workflow.status')
    result=WorkflowSnapshot.model_validate(response.data)
    checked(result.workflow,turn_id,ctx)
    return result


async def waiting(turn_id: UUID,run_id: UUID,checkpoint_id: str,ctx: RequestContext,db: SyncPostgrestClient) -> WorkflowStatus:
    response=await execute(lambda:db.rpc('workflow_mark_waiting',{'p_turn_id':str(turn_id),'p_run_id':str(run_id),
        'p_checkpoint_id':checkpoint_id}).execute(),'workflow.waiting')
    return checked(WorkflowStatus.model_validate(response.data),turn_id,ctx)


async def acquire(turn_id: UUID,worker_id: UUID,ctx: RequestContext,db: SyncPostgrestClient) -> WorkflowLease:
    response=await execute(lambda:db.rpc('workflow_acquire_resume',{'p_turn_id':str(turn_id),
        'p_worker_id':str(worker_id)}).execute(),'workflow.resume')
    result=WorkflowLease.model_validate(response.data)
    checked(result.workflow,turn_id,ctx)
    return result


async def failed(turn_id: UUID,worker_id: UUID,ctx: RequestContext,db: SyncPostgrestClient) -> WorkflowStatus:
    response=await execute(lambda:db.rpc('workflow_resume_failed',{'p_turn_id':str(turn_id),
        'p_worker_id':str(worker_id)}).execute(),'workflow.failed')
    return checked(WorkflowStatus.model_validate(response.data),turn_id,ctx)
