"""Internal meeting agent capabilities and runtime identities. These never enter planner or model state."""
from dataclasses import dataclass
from typing import Literal, NamedTuple, TypeAlias
from uuid import UUID

from app.schemas.contracts import Principal
from app.schemas.meeting_lifecycle import Claim, Claims

MeetingAgent: TypeAlias = Literal['brief_writer', 'proposal_drafter', 'minutes_writer', 'minutes_overview']
MeetingActor: TypeAlias = Literal['assigned_rm', 'claimed_worker']


class MeetingTool(NamedTuple):
    name: str
    actor: MeetingActor
    access: Literal['read', 'draft_write']


class MeetingAgentSpec(NamedTuple):
    name: MeetingAgent
    prompt: MeetingAgent
    version: str
    purpose: str
    tools: tuple[MeetingTool, ...]


@dataclass(frozen=True)
class MeetingRmAccess:
    agent: MeetingAgent
    principal: Principal
    meeting_id: UUID
    claims: Claims


@dataclass(frozen=True)
class MeetingWorkerAccess:
    agent: MeetingAgent
    principal: Principal
    claim: Claim


MeetingAccess: TypeAlias = MeetingRmAccess | MeetingWorkerAccess
