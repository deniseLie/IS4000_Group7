"""Run the catalogue policy cases and exercise the real meeting connector boundaries."""
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import ProgrammingError

from app.core.observability import span_sink
from app.schemas.contracts import PlanTask, RequestContext
from app.schemas.meeting_agents import MeetingRmAccess
from app.services.agents import registry
from app.services.authorization import PolicyDenied, decide
from app.services.meetings.database import ApplicationDatabase, MeetingStoreError
from app.services.tools import TOOLS, meeting_call
from evaluation.meeting_boundaries import attempt, cases
from tests.test_meeting_lifecycle import claim_transcript, export_body, flow, headers, post, start


@pytest.mark.parametrize('case', cases(), ids=lambda case: case.id)
def test_meeting_tool_policy(case):
    ctx, target = attempt(case)
    decision = decide(ctx.principal, case.tool, target, ctx)
    allowed = case.expected_rule == 'explicit_rule'
    assert decision.allow == allowed and decision.reasons == [case.expected_rule]
    if isinstance(ctx, RequestContext):
        return
    spans = []
    token = span_sink.set(spans)
    try:
        if allowed:
            with meeting_call(case.tool, UUID(target['meeting_id']), ctx):
                pass
        else:
            with pytest.raises(PolicyDenied, match=case.expected_rule):
                with meeting_call(case.tool, UUID(target['meeting_id']), ctx):
                    pytest.fail('Denied tool entered the connector body')
    finally:
        span_sink.reset(token)
    name, facts = spans[0]
    assert name == 'meeting.tool_call'
    assert facts['allowed'] == allowed and facts['agent'] == case.agent
    assert set(facts) <= {'tool', 'agent', 'side', 'transport', 'allowed', 'reason', 'error_type'}
    if not allowed:
        assert facts['reason'] == case.expected_rule


def test_meeting_agents_are_in_catalogue_and_outside_both_chat_planners_and_the_chat_tools():
    # Their reads are MCP tools of their own (tools.MEETING_READS, tests/test_tool_boundary.py), never chat tools.
    assert set(registry.MEETING_SPECS) == {'brief_writer', 'proposal_drafter', 'minutes_writer', 'minutes_overview'}
    assert set(registry.SPECS) == set(registry.CHAT_SPECS) | set(registry.MEETING_SPECS)
    assert set(registry.MEETING_SPECS).isdisjoint(registry.CLIENT_SPECS.keys() | registry.RM_SPECS.keys())
    assert registry.MEETING_TOOLS.isdisjoint(TOOLS)
    assert not registry.MINUTES_OVERVIEW.tools
    assert set(PlanTask.model_json_schema()['properties']['agent']['enum']) == set(registry.CHAT_SPECS)


def test_rm_connector_refuses_an_overridden_meeting_id_before_opening_data_api():
    ctx, target = attempt(next(case for case in cases() if case.id == 'MA11'))
    assert isinstance(ctx, MeetingRmAccess)
    database = ApplicationDatabase(RequestContext(session_id=uuid4(), principal=ctx.principal))
    with pytest.raises(MeetingStoreError, match='meeting_scope_mismatch') as refused:
        database.agent_call(ctx.agent, 'meeting_save_brief', ctx.meeting_id, {'p_meeting_id': UUID(target['meeting_id'])}, ctx.claims)
    assert refused.value.code == '42501'


def test_native_current_assignment_overrides_a_valid_rm_session(flow):
    start(flow, 'client')
    uri = f'/meetings/{flow.meeting}'
    rm = headers(flow, 'rm')
    assert flow.http.get(uri+'/preparation', headers=rm).status_code == 200
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.assignments set ended_on=rm.today() where customer_id=1002 and ended_on is null")
    assert flow.http.get(uri+'/preparation', headers=rm).status_code == 403
    assert flow.http.post(uri+'/pack/draft', headers=rm, json={'amounts': [], 'command_id': str(uuid4())}).status_code == 403
    assert flow.http.post(uri+'/generate-notes-draft', headers=rm, json={'command_id': str(uuid4())}).status_code == 403


def test_native_claim_token_stays_required_after_policy_allows_worker(flow):
    start(flow, 'client')
    post(flow, 'rm', '/transcript-import', export_body())
    claim = claim_transcript(flow)
    assert flow.worker.context(claim).meeting_id == flow.meeting
    spans = []
    token = span_sink.set(spans)
    try:
        with pytest.raises(ProgrammingError) as refused:
            flow.worker.context(claim.model_copy(update={'claim_token': uuid4()}))
    finally:
        span_sink.reset(token)
    assert refused.value.sqlstate == 'P1002'
    assert spans[0][1]['allowed'] is False
