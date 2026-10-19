"""Runtime identity and durable workflow ownership; credentials never belong in graph state."""
from typing import Self
from uuid import UUID
from pydantic import BaseModel,ConfigDict,StrictBool,model_validator
from app.schemas.contracts import RequestContext
from app.schemas.conversations import ConversationAccess,TurnRecord
from app.schemas.events import WorkflowStatus
from app.schemas.events import Origin


class OwnedWorkflow(BaseModel):
    model_config=ConfigDict(extra='forbid')
    access: ConversationAccess
    turn: TurnRecord


class PipelineContext(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request: RequestContext
    workflow: OwnedWorkflow | None


class WorkflowSnapshot(BaseModel):
    model_config=ConfigDict(extra='ignore')
    workflow: WorkflowStatus
    turn: TurnRecord

    @model_validator(mode='after')
    def consistent_turn(self) -> Self:
        if self.workflow.turn_id!=self.turn.turn_id or self.workflow.session_id!=self.turn.session_id or self.workflow.case_id!=self.turn.case_id or self.workflow.customer_id!=self.turn.customer_id:
            raise ValueError('Workflow and turn identities disagree')
        return self


class WorkflowLease(WorkflowSnapshot):
    acquired: StrictBool


class PendingReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task_id: str
    command_id: UUID


class ResponseMeta(BaseModel):
    model_config=ConfigDict(extra='forbid')
    origin: Origin
    prompt_id: str | None
