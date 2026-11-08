"""v5 agent runner (build plan phase 3): plan checks, default reads, the open-task loop state machine and answer validation.
Unit tests feed `advance` and the validator sample strings; they never stand in for the model. The real-provider checks
are opt-in (TEST_RUNNER_LIVE=1 with TEST_PERSONA_FIXTURE_FILE) and run against the hosted project with persona logins."""
import asyncio
import json
import os
from uuid import UUID, uuid4
import pytest
from app.core.config import settings
from app.schemas.contracts import Export, PlanTask, Principal, RequestContext, Task
from app.services import external, llm_gateway, tools
from app.services.agents import runner
from app.services.agents.answer import WITHHELD, validate_draft
from app.services.agents.registry import SPECS
from app.services.agents.runner import Gathered, LoopState, PlanRefused, advance, check_task, default_reads, read
from app.services.authorization import PolicyDenied

ACCOUNTS = SPECS['accounts']
CARD = {'card_id': 'card_4821', 'name': 'Signature Card', 'network': 'visa', 'last4': '4821', 'status': 'active', 'currency': 'SGD',
    'limit': 20000, 'outstanding': 3850.25, 'minimum_payment': 50, 'due_date': '2026-11-25', 'unfreeze_blocked_reason': None, 'customer_id': 1001}
TXN = {'txn_id': 't1', 'date': '2026-10-30', 'description': 'Dining', 'amount': -42.5, 'currency': 'SGD', 'status': 'posted',
    'card_last4': '4821', 'card_id': 'card_4821', 'is_overseas': False, 'failure_reason': None, 'customer_id': 1001}


def ctx(agent='accounts') -> RequestContext:
    return RequestContext(session_id=uuid4(), agent=agent,
        principal=Principal(kind='customer', user_id=uuid4(), customer_id=1001, jwt='synthetic-test-token'))


def task(intent: str, question: str = 'What is my card balance?', agent='accounts', **entities) -> PlanTask:
    return PlanTask(id='t1', agent=agent, intent=intent, question=question, entities=entities)


@pytest.fixture
def served(monkeypatch):
    """The data-layer reads behind the tools record their arguments and return synthetic rows; tools.call itself is real."""
    calls = []
    def tool(name, rows):
        async def run(**args):
            calls.append((name, args))
            return {'items': rows, 'call_id': str(uuid4())}
        return run
    monkeypatch.setitem(tools.TOOLS, 'my_cards', tool('my_cards', [CARD]))
    monkeypatch.setitem(tools.TOOLS, 'my_accounts', tool('my_accounts', []))
    monkeypatch.setitem(tools.TOOLS, 'my_transactions', tool('my_transactions', [TXN]))
    monkeypatch.setitem(tools.TOOLS, 'request_categories', tool('request_categories', []))
    monkeypatch.setitem(tools.TOOLS, 'knowledge_search', tool('knowledge_search', []))
    return calls


# ---- Plan checks: refused before any read or model call ----

def test_tasks_the_agent_cannot_run_are_refused(monkeypatch):
    with pytest.raises(PlanRefused, match="no intent 'move_money'"):check_task(ACCOUNTS, task('move_money'), {})
    with pytest.raises(PlanRefused, match='is for guide'):check_task(ACCOUNTS, task('card_figures', agent='guide'), {})
    no_loop = SPECS['compliance'].model_copy(update={'loop_tools': frozenset()})  # since 3a every spec has loop tools
    monkeypatch.setattr(runner, 'PROMPTED', runner.PROMPTED | {'compliance'})  # a prompt alone does not let an agent without loop tools loop
    with pytest.raises(PlanRefused, match='listed intents only'):check_task(no_loop, task('open', agent='compliance'), {})


def test_an_agent_without_a_released_prompt_or_an_unlisted_import_is_refused(monkeypatch):
    monkeypatch.setattr(runner, 'PROMPTED', runner.PROMPTED - {'guide'})
    monkeypatch.delitem(runner.RULES, 'guide', raising=False)  # neither a prompt nor a rules hook
    with pytest.raises(PlanRefused, match='no released runner prompt'):check_task(SPECS['guide'], task('product_info', agent='guide'), {})
    with pytest.raises(PlanRefused, match='does not import'):
        check_task(ACCOUNTS, task('card_figures'), {'spend_total': Export(value=1, source_ids=['my_cards'])})
    check_task(ACCOUNTS, task('card_figures'), {})
    check_task(ACCOUNTS, task('open'), {})


def test_run_agent_refuses_before_reading(served):
    with pytest.raises(PlanRefused):asyncio.run(runner.run_agent(ACCOUNTS, task('move_money'), {}, ctx()))
    assert served == []


# ---- Known intent: exactly the default reads, arguments from entities ----

def test_a_known_intent_runs_exactly_its_default_reads_with_entity_arguments(served):
    got = Gathered()
    asyncio.run(default_reads(ACCOUNTS, task('transactions', 'Show my last 30 days', days=30), ctx(), got))
    assert served == [('my_transactions', {'status': None, 'days': 30})]  # status entity missing: the tool's default (the MCP server fills it)
    assert [e.id for e in got.evidence] == ['my_transactions']
    records = json.loads(got.evidence[0].text)
    assert 'call_id' not in records and records['records'][0] == {**{k: TXN[k] for k in
        ('date', 'description', 'currency', 'status', 'card_last4', 'is_overseas', 'failure_reason')}, 'amount': 42.5, 'direction': 'debit'}


def test_fixed_arguments_limits_and_the_question_as_knowledge_query(served):
    got = Gathered()
    asyncio.run(default_reads(ACCOUNTS, task('transfer_status', 'Where is my transfer?'), ctx(), got))
    asyncio.run(default_reads(ACCOUNTS, task('card_fees', 'What is the annual fee?'), ctx(), got))
    asyncio.run(default_reads(ACCOUNTS, task('transactions', 'Everything'), ctx(), got))
    assert served == [('my_transactions', {'status': 'pending', 'days': 90}), ('my_accounts', {}),  # as validated by the MCP server
        ('my_cards', {}), ('knowledge_search', {'query': 'What is the annual fee?', 'k': 4}), ('my_transactions', {'status': None, 'days': 90})]
    assert [e.id for e in got.evidence] == ['my_transactions', 'my_accounts', 'my_cards', 'my_transactions-2']
    assert 'customer_id' not in got.evidence[2].text and '"card_id"' not in got.evidence[2].text  # field view
    assert [entry['tool'] for entry in got.log] == ['my_transactions', 'my_accounts', 'my_cards', 'knowledge_search', 'my_transactions']


def test_reads_beyond_the_limit_or_unavailable_become_notes_never_records(served, monkeypatch):
    got = Gathered()
    asyncio.run(default_reads(ACCOUNTS, task('transactions', 'This year', days=365), ctx(), got))
    assert not any(name == 'my_transactions' and args.get('days') == 365 for name, args in served) and got.evidence[0].id == 'my_transactions-limit'
    async def down(**args):raise external.ExternalError('fx_rate', 'timeout')
    async def broken(**args):raise RuntimeError('database down')
    monkeypatch.setitem(tools.TOOLS, 'fx_rate', down)
    monkeypatch.setitem(tools.TOOLS, 'my_cards', broken)
    notes = asyncio.run(read('fx_rate', {'base': 'SGD', 'quote': 'JPY'}, ctx(), set())) + asyncio.run(read('my_cards', {}, ctx(), set()))
    assert [n.id for n in notes] == ['fx_rate-unavailable', 'my_cards-unavailable']
    assert all(not any(ch.isdigit() for ch in n.text) for n in notes)


def test_a_tool_outside_the_spec_raises_instead_of_becoming_a_note(served):
    with pytest.raises(PolicyDenied):asyncio.run(read('my_compliance_case', {}, ctx(), set()))


# ---- Open task: the loop state machine ----

def start(limit=4) -> LoopState:
    return LoopState(tools=ACCOUNTS.loop_tools, limit=limit)


def step(*calls, action='call') -> str:
    return json.dumps({'action': action, 'calls': [{'tool': t, 'args_json': json.dumps(a)} for t, a in calls]})


def test_parallel_calls_in_one_step_all_run():
    state, calls = advance(start(), step(('my_transactions', {'days': 60}), ('my_cards', {})))
    assert [c.tool for c in calls] == ['my_transactions', 'my_cards'] and calls[0].args == {'days': 60}
    assert state.used == 2 and not state.done and state.refused == ()


def test_the_call_limit_stops_the_loop():
    state, calls = advance(start(), step(*[('my_transactions', {'days': d}) for d in (10, 20, 30, 40, 50)]))
    assert len(calls) == 4 and state.done and state.refused == (('my_transactions', 'call_limit'),)
    state, calls = advance(start(), step(('my_cards', {})))
    for days in (10, 20, 30):state, more = advance(state, step(('my_transactions', {'days': days})))
    assert state.used == 4 and state.done


def test_tools_outside_loop_tools_are_refused_and_never_run():
    state, calls = advance(start(), step(('my_compliance_case', {}), ('my_tickets', {}), ('transfer_money', {'amount': 10}), ('my_cards', {})))
    assert [c.tool for c in calls] == ['my_cards']
    assert state.refused == (('my_compliance_case', 'tool_not_allowed'), ('my_tickets', 'tool_not_allowed'), ('transfer_money', 'tool_not_allowed'))
    assert 'my_compliance_case' not in ACCOUNTS.loop_tools and SPECS['compliance'].tools['my_compliance_case'].free_text


def test_bad_arguments_and_repeats_are_refused():
    bad = json.dumps({'action': 'call', 'calls': [{'tool': 'my_cards', 'args_json': 'not json'}, {'tool': 'my_loans', 'args_json': '[1]'}]})
    state, calls = advance(start(), bad)
    assert calls == [] and state.refused == (('my_cards', 'bad_args'), ('my_loans', 'bad_args'))
    state, calls = advance(start(), step(('my_cards', {}), ('my_cards', {})))
    assert len(calls) == 1 and state.refused == (('my_cards', 'repeated'),)
    state, calls = advance(state, step(('my_cards', {})))
    assert calls == [] and state.refused[-1] == ('my_cards', 'repeated')


@pytest.mark.parametrize('bad', ['not json', '[]', '{"action":"jump","calls":[]}', '{"action":"call","calls":{}}', '{"calls":[]}'])
def test_a_malformed_step_ends_the_loop(bad):
    state, calls = advance(start(), bad)
    assert calls == [] and state.done and state.refused == (('', 'malformed_step'),)


def test_answer_or_no_calls_ends_the_loop_and_refused_steps_cannot_loop_forever():
    for done in (step(action='answer'), step()):
        state, calls = advance(start(), done)
        assert state.done and calls == [] and state.refused == ()
    state = start()
    for _ in range(4):state, calls = advance(state, step(('my_tickets', {})))
    assert state.done and state.used == 0 and state.steps == 4


def test_the_step_schema_offers_only_loop_tools():
    schema = runner.step_schema(ACCOUNTS.loop_tools)
    assert schema['properties']['calls']['items']['properties']['tool']['enum'] == sorted(ACCOUNTS.loop_tools)
    assert 'request_categories' not in ACCOUNTS.loop_tools and {'fx_rate', 'public_holidays'} <= ACCOUNTS.loop_tools


# ---- Answer validation against all gathered evidence ----

def evidence(served_reads):
    got = Gathered()
    asyncio.run(default_reads(ACCOUNTS, task('card_figures'), ctx(), got))
    return got


@pytest.mark.parametrize('answer,ok', [('Your card ending 4821 has SGD 3,850.25 outstanding.', True),
    ('Your card ending 4821 has SGD 9,999.99 outstanding.', False)])
def test_an_answer_with_figures_not_in_the_evidence_is_withheld(served, answer, ok):
    got = evidence(served)
    raw = json.dumps({'answer': answer, 'source_ids': ['my_cards'], 'answer_kind': 'banking', 'status': 'ok', 'clarification_question': ''})
    result = validate_draft(Task(task_id='t1', domain='retail', intent='card_figures'), raw, got.evidence, 'accounts')
    out = runner.output(ACCOUNTS, task('card_figures'), result, got)
    assert out.agent == 'accounts' and out.reads == got.log
    if ok:assert out.status == 'ok' and out.result.text == answer and [c.source_id for c in out.result.citations] == ['my_cards']
    else:assert out.status == 'error' and out.result.text == WITHHELD and out.result.citations == []


# ---- Real provider (opt-in): hosted project, persona logins ----

LIVE = os.getenv('TEST_RUNNER_LIVE') == '1' and os.getenv('TEST_PERSONA_FIXTURE_FILE')
LOOP = asyncio.new_event_loop()  # the provider's HTTP client is cached per event loop, so every live call shares one


def persona(email: str, cid: int) -> RequestContext:
    from supabase import create_client
    from app.core import auth
    user = next(u for u in json.load(open(os.environ['TEST_PERSONA_FIXTURE_FILE'])) if u['email'] == email)
    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
    token = sb.auth.sign_in_with_password({'email': email, 'password': user['password']}).session.access_token
    claims = auth.verify_token(token)
    return RequestContext(session_id=UUID(claims['session_id']), principal=Principal(kind='customer', user_id=UUID(claims['sub']),
        customer_id=cid, auth_session_id=UUID(claims['session_id']), jwt=token))


@pytest.fixture
def counted(monkeypatch):
    """Counts real gateway calls by schema title; every call still goes to the configured provider."""
    titles, real = [], llm_gateway.call
    async def call(prompt, system, messages, schema):
        titles.append(schema['title'])
        try:return await real(prompt, system, messages, schema)
        except llm_gateway.LLMError as error:
            titles.append(f'failed: {type(error).__name__}: {error}'[:200])  # shown in the assertion, never replaced
            raise
    monkeypatch.setattr(llm_gateway, 'call', call)
    return titles


@pytest.mark.skipif(not LIVE, reason='Opt in with TEST_RUNNER_LIVE=1 and TEST_PERSONA_FIXTURE_FILE (hosted persona logins)')
def test_live_known_intents_make_no_step_calls(counted):
    """"What's my card balance and last 3 transactions?": the planner (phase 5) gives two known-intent tasks, each its own issue.
    Each makes one answer call, plus at most one repair if the first draft fails validation."""
    michelle = persona('demo.michelle@example.com', 1002)
    async def both():
        return await asyncio.gather(runner.run_agent(ACCOUNTS, task('card_figures', "What's my card balance?"), {}, michelle),
            runner.run_agent(ACCOUNTS, PlanTask(id='t2', agent='accounts', intent='transactions', question='What are my last 3 transactions?'), {}, michelle))
    cards, txns = LOOP.run_until_complete(both())
    print('\n', counted, '\n', cards.status, cards.result.text, '\n', txns.status, txns.result.text)
    assert 'agent_step' not in counted and 2 <= counted.count('domain_answer') <= 4, counted
    for out, tool in ((cards, 'my_cards'), (txns, 'my_transactions')):
        assert [entry['tool'] for entry in out.reads] == [tool]
        assert out.status == 'ok', out.model_dump_json()
        assert {c.source_id for c in out.result.citations} == {tool}, out.result
    assert '7310' in cards.result.text and '5169.6' in cards.result.text.replace(',', '')


@pytest.mark.skipif(not LIVE, reason='Opt in with TEST_RUNNER_LIVE=1 and TEST_PERSONA_FIXTURE_FILE (hosted persona logins)')
def test_live_open_question_uses_the_bounded_loop(counted):
    michelle = persona('demo.michelle@example.com', 1002)
    out = LOOP.run_until_complete(runner.run_agent(ACCOUNTS, task('open', 'Did I spend more on dining in September than August?'), {}, michelle))
    steps = counted.count('agent_step')
    print('\n', counted, out.reads, '\n', out.status, out.result.text)
    assert 1 <= steps <= settings.agent_tool_calls and counted[-1] == 'domain_answer' and counted.count('domain_answer') in {1, 2}, counted
    assert 1 <= len(out.reads) <= settings.agent_tool_calls and all(e['tool'] in ACCOUNTS.loop_tools for e in out.reads), out.reads
    assert all(e['args'].get('days', 90) <= 90 for e in out.reads if e['tool'] == 'my_transactions')
    assert out.status in {'ok', 'needs_human'}, out.model_dump_json()
