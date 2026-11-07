"""Deterministic evaluation cases for the meeting workflow's tool policy."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NamedTuple
from uuid import uuid4

from app.schemas.contracts import Principal, RequestContext
from app.schemas.meeting_agents import MeetingAccess, MeetingAgent, MeetingRmAccess, MeetingWorkerAccess
from app.schemas.meeting_lifecycle import AppMetadata, Claim, Claims
from evaluation.meetings import Model


class BoundaryCase(Model):
    id: str
    title: str
    agent: MeetingAgent
    actor: Literal['rm', 'client', 'rm_chat', 'client_chat', 'worker', 'wrong_event', 'wrong_login']
    tool: str
    target: Literal['same', 'other']
    expected_rule: str


class Attempt(NamedTuple):
    context: RequestContext | MeetingAccess
    target: dict[str, str]


def cases() -> tuple[BoundaryCase, ...]:
    data = json.loads(Path(__file__).with_name('meeting_boundary_cases.json').read_text())
    return tuple(BoundaryCase.model_validate(item) for item in data)


def attempt(case: BoundaryCase) -> Attempt:
    """Use unrelated synthetic identities and a fresh meeting ID for each policy case."""
    meeting_id = uuid4()
    target = {'meeting_id': str(meeting_id if case.target == 'same' else uuid4())}
    now = datetime.now(timezone.utc)
    if case.actor in ('worker', 'wrong_event'):
        claim = Claim(event_id=1, event_type='transcript.ready' if case.actor == 'worker' else 'meeting.ended',
            meeting_id=meeting_id, claim_token=uuid4(), attempts=1, available_at=now)
        return Attempt(MeetingWorkerAccess(case.agent, Principal(kind='system'), claim), target)
    user_id = uuid4()
    principal = Principal(kind='customer', user_id=user_id, jwt='synthetic-token-for-policy-only', customer_id=1002)
    if case.actor in ('rm_chat', 'client_chat'):
        agent = 'rm_calendar' if case.actor == 'rm_chat' else 'relationship'
        return Attempt(RequestContext(session_id=uuid4(), principal=principal, agent=agent), target)
    metadata = AppMetadata() if case.actor == 'client' else AppMetadata(demobank_role='rm', rm_id=uuid4())
    claims = Claims(sub=uuid4() if case.actor == 'wrong_login' else user_id, role='authenticated',
        aud='authenticated', exp=int(now.timestamp())+3600, app_metadata=metadata)
    return Attempt(MeetingRmAccess(case.agent, principal, meeting_id, claims), target)
