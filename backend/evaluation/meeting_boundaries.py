"""Deterministic evaluation cases for the meeting workflow's tool policy.

tests/test_meeting_agent_boundaries.py runs them as tests. Publish to LangSmith (dataset demobank-meeting-tool-rules,
evaluation/publish.py; code only, no model call): python -m evaluation.meeting_boundaries --publish"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NamedTuple
from uuid import UUID, uuid4

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


# LangSmith --------------------------------------------------------------------------------------------------------------
DATASET = 'demobank-meeting-tool-rules'
DESCRIPTION = ('Meeting agents\' tool policy: each actor (assigned RM, client, chat agents, the worker, a wrong event or '
    'login) may call only the meeting tools its rule allows, and a refused call never enters the connector. Code only.')


def check(case: BoundaryCase) -> dict:
    """The case as the test runs it: the policy decision, then (for a meeting access) the connector and its span."""
    from app.core.observability import span_sink
    from app.services.authorization import PolicyDenied, decide
    from app.services.tools import meeting_call
    start = time.perf_counter()
    ctx, target = attempt(case)
    decision = decide(ctx.principal, case.tool, target, ctx)
    allowed = case.expected_rule == 'explicit_rule'
    row = {'id': case.id, 'allow': decision.allow, 'reasons': list(decision.reasons), 'connector': None}
    if not isinstance(ctx, RequestContext):
        spans, entered, refused = [], False, None
        token = span_sink.set(spans)
        try:
            with meeting_call(case.tool, UUID(target['meeting_id']), ctx):
                entered = True
        except PolicyDenied as error:
            refused = str(error).rsplit(': ', 1)[-1]
        finally:
            span_sink.reset(token)
        facts = spans[0][1] if spans else {}
        row['connector'] = {'entered': entered, 'refused': refused, 'span_allowed': facts.get('allowed'), 'span_reason': facts.get('reason')}
        row['connector_ok'] = (entered and facts.get('allowed') is True) if allowed else \
            (not entered and refused == case.expected_rule and facts.get('allowed') is False and facts.get('reason') == case.expected_rule)
    row['decision_ok'] = decision.allow == allowed and list(decision.reasons) == [case.expected_rule]
    return row | {'ms': round((time.perf_counter() - start) * 1000, 1)}


def published_cases() -> list:
    from evaluation.publish import Case
    return [Case(c.id, {'say': c.title, 'agent': c.agent, 'actor': c.actor, 'tool': c.tool, 'target': c.target},
        {'check': ('Allow' if c.expected_rule == 'explicit_rule' else 'Refuse') + f' ({c.expected_rule})', 'expected_rule': c.expected_rule})
        for c in cases()]


def published_result(row: dict):
    from evaluation.publish import Result, scored
    said = ('Allowed' if row['allow'] else 'Refused') + f": {', '.join(row['reasons'])}"
    checks = {'decision': scored(row['decision_ok'], said)}
    if row['connector'] is not None:
        checks['connector'] = scored(row['connector_ok'], json.dumps(row['connector']))
    return Result(row['id'], {'answer': said, 'allow': row['allow'], 'reasons': row['reasons'], 'connector': row['connector'], 'model_calls': 0},
        checks, row['ms'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--publish', action='store_true', help=f'Publish the run to LangSmith as an experiment on {DATASET}')
    args = parser.parse_args()
    rows = [check(case) for case in cases()]
    failed = [r['id'] for r in rows if not r['decision_ok'] or r.get('connector_ok') is False]
    print(json.dumps({'cases': len(rows), 'failed': failed}))
    if args.publish:
        from evaluation.publish import publish
        print(json.dumps(publish(DATASET, DESCRIPTION, Path(__file__).with_name('meeting_boundary_cases.json'), published_cases(),
            [published_result(r) for r in rows], 'policy', {'pipeline': 'meeting tool policy', 'model': None})))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
