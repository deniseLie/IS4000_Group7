"""v5 agent dependencies (build plan phase 7; architecture §14.6): the plan check, the router (gather) with sample outputs,
code-computed exports and their provenance through the validator, the trip rules, and offer chain order.

No model is called: sample planner rows and agent outputs exercise the graph code, as the plan's tests ask."""
import asyncio
import json
from datetime import date
from uuid import uuid4
import pytest
from app.schemas.agents import Evidence
from app.schemas.chat import ChatRequest, Message
from app.schemas.contracts import AgentOutput, AgentResult, Export, OfferProposal, PlanTask, Principal, RequestContext
from app.schemas.events import Source
from app.services import audit, llm_gateway, offers, orchestrator, source_checks
from app.services.actions.store import ActionIntegrityError
from app.services.agents import derived, rules, runner, trip_checks
from app.services.agents.registry import SPECS
from tests.test_runner import CARD, ctx, served  # noqa: F401 (fixture)

PERSONAL = ChatRequest(messages=[Message(role='user', content='hi')], mode='personal')
GUEST = ChatRequest(messages=[Message(role='user', content='hi')], mode='public')


def plan(*tasks: PlanTask) -> list[PlanTask]:
    return list(tasks)


def t(id_: str, agent: str, intent: str, **fields) -> PlanTask:
    return PlanTask(id=id_, agent=agent, intent=intent, question=f'question {id_}', **fields)


def ok(id_: str, agent: str, **exports) -> AgentOutput:
    return AgentOutput(task_id=id_, agent=agent, status='ok', exports={k: Export(value=v, source_ids=['my_cards']) for k, v in exports.items()},
        result=AgentResult(task_id=id_, agent=agent, status='ok', answer_kind='conversation', text='done'))


def state(tasks: list[PlanTask], outputs: list[AgentOutput], wave: int = 1) -> dict:
    history = [Message(role='user', content='If a payment falls due while I am away, move my meeting')]
    return {'request': PERSONAL, 'history': history, 'tasks': tasks, 'outputs': outputs, 'lines': [], **({'next': []} if wave == 2 else {})}


# ---- Plan check ----

def test_the_planner_numbers_become_links_and_survive_the_plan_check(monkeypatch):
    rows = [{'agent': 'accounts', 'intent': 'trip_due_dates', 'question': 'Payments due 10-20 Nov?', 'depends_on': 0, 'condition': '', 'for_each': '',
             'entities': [{'name': 'from_date', 'value': '2026-11-10'}, {'name': 'to_date', 'value': '2026-11-20'}]},
            {'agent': 'relationship', 'intent': 'meeting_times', 'question': 'Move my meeting', 'depends_on': 1, 'condition': 'due_in_window',
             'for_each': '', 'entities': []},
            {'agent': 'guide', 'intent': 'general', 'question': 'Opening hours?', 'depends_on': 3, 'condition': '', 'for_each': '', 'entities': []}]
    async def call(*a, **kw):return type('R', (), {'data': {'tasks': rows}})()
    monkeypatch.setattr(llm_gateway, 'call', call)
    tasks = asyncio.run(orchestrator.model_plan([Message(role='user', content='x')]))
    assert [(x.id, x.depends_on, x.condition) for x in tasks] == [('t1', None, None), ('t2', 't1', 'due_in_window'), ('t3', None, None)]
    schema = orchestrator.plan_schema()['properties']['tasks']['items']
    assert {'depends_on', 'condition', 'for_each'} <= set(schema['required'])
    # a rule task inserted in front renumbers the plan; links follow their tasks
    shifted = orchestrator.checked([t('t0', 'guide', 'login_help'), *tasks], PERSONAL)
    assert [(x.id, x.depends_on) for x in shifted] == [('t1', None), ('t2', None), ('t3', 't2'), ('t4', None)]
    assert orchestrator.catalogue().count('due_in_window (bool)') == 1


def test_links_the_specs_do_not_declare_are_dropped_with_a_fixed_line():
    good = t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='due_in_window')
    cases = [t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='no_such_export'),
        t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='due_dates'),  # a list is not a condition
        t('t2', 'relationship', 'meeting_times', depends_on='t1', for_each='due_in_window'),  # a bool is not a list
        t('t2', 'relationship', 'meeting_times', depends_on='t9')]  # not in the plan ('?' from checked)
    upstream = t('t1', 'accounts', 'trip_due_dates')
    kept, lines = orchestrator.linked([upstream, good], PERSONAL)
    assert kept == [upstream, good] and lines == []
    for bad in cases:
        kept, lines = orchestrator.linked([upstream, bad], PERSONAL)
        assert kept == [upstream] and [l.answer for l in lines] == [orchestrator.UNLINKED]
    # at most 2 waves: a task cannot hang off a dependent task, so there is no chain of three and no cycle
    third = t('t3', 'compliance', 'tax_form', depends_on='t2')
    kept, lines = orchestrator.linked([upstream, good, third], PERSONAL)
    assert [x.id for x in kept] == ['t1', 't2'] and len(lines) == 1
    loop = [t('t1', 'accounts', 'card_figures', depends_on='t2'), t('t2', 'relationship', 'my_rm', depends_on='t1')]
    assert orchestrator.linked(loop, PERSONAL)[0] == []
    with pytest.raises(ValueError):PlanTask(id='t1', agent='accounts', intent='card_figures', question='q', depends_on='t1')
    with pytest.raises(ValueError):PlanTask(id='t1', agent='accounts', intent='card_figures', question='q', condition='due_in_window')


def test_guests_get_no_dependent_tasks():
    kept, lines = orchestrator.linked([t('t1', 'guide', 'general'), t('t2', 'guide', 'general', depends_on='t1')], GUEST)
    assert lines == [] and all(x.depends_on is None for x in kept)


def test_only_independent_tasks_start_in_wave_one():
    sends = orchestrator.after_planner(state([t('t1', 'accounts', 'trip_due_dates'),
        t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='due_in_window')], []))
    assert [s.arg['task'].id for s in sends] == ['t1'] and sends[0].arg['inputs'] == {}


# ---- The router (gather) ----

def test_a_true_condition_sends_the_dependant_with_only_its_imports():
    batch = audit.AuditBatch(session_id=uuid4());token = audit.current_batch.set(batch)
    try:
        tasks = [t('t1', 'accounts', 'trip_due_dates'), t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='due_in_window')]
        update = orchestrator.gather(state(tasks, [ok('t1', 'accounts', due_in_window=True, due_dates=['2026-11-25'], account_refs=[])]))
    finally:audit.current_batch.reset(token)
    assert update['outputs'] == []
    [send] = update['next']
    assert send['task'].id == 't2' and set(send['inputs']) == {'due_dates'}  # relationship imports due_dates only
    assert send['inputs']['due_dates'].source_ids == ['my_cards']
    assert [(e.action, e.details) for e in batch.events] == [('agent.export_passed', {'from': 'accounts', 'to': 'relationship', 'name': 'due_dates', 'count': 1})]
    assert orchestrator.after_gather({**update})[0].arg['task'].id == 't2'


@pytest.mark.parametrize('upstream,text', [
    (lambda: ok('t1', 'accounts', due_in_window=False, due_dates=[]), orchestrator.CONDITION_FALSE['due_in_window']),
    (lambda: ok('t1', 'accounts'), orchestrator.UPSTREAM_UNKNOWN),  # the export is missing: unknown means no
    (lambda: AgentOutput(task_id='t1', agent='accounts', status='error', origin='validation'), orchestrator.UPSTREAM_UNKNOWN),
    (lambda: ok('t1', 'accounts', due_in_window='yes'), orchestrator.CONDITION_FALSE['due_in_window']),  # only True is true
])
def test_false_or_unknown_skips_the_dependant_with_fixed_wording(upstream, text):
    tasks = [t('t1', 'accounts', 'trip_due_dates'), t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='due_in_window')]
    update = orchestrator.gather(state(tasks, [upstream()]))
    assert update['next'] == [] and orchestrator.after_gather(update) == 'compose'
    [skip] = update['outputs']
    assert (skip.task_id, skip.origin, skip.result.text) == ('t2', 'safety', text)
    section = orchestrator.section_for(tasks[1], skip)
    assert section.response_origin == 'safety' and section.sources == []


def test_fan_out_is_capped_at_five_in_declared_order():
    refs = [{'type': 'savings', 'currency': 'SGD', 'last4': f'{n:04d}'} for n in (7, 3, 9, 1, 5, 8, 2)]
    tasks = [t('t1', 'accounts', 'account_balance'), t('t2', 'compliance', 'tax_form', depends_on='t1', for_each='account_refs')]
    update = orchestrator.gather(state(tasks, [ok('t1', 'accounts', account_refs=refs)]))
    sent = [(s['task'].id, s['task'].entities['item']['last4'], s['inputs']['account_refs'].value['last4']) for s in update['next']]
    assert sent == [('t2', '0001', '0001'), ('t2-2', '0002', '0002'), ('t2-3', '0003', '0003'), ('t2-4', '0005', '0005'), ('t2-5', '0007', '0007')]
    assert [l.answer for l in update['lines']] == [orchestrator.MORE_ITEMS.format(shown=5, total=7)]
    assert [x.id for x in update['tasks']] == ['t1', 't2', 't2-2', 't2-3', 't2-4', 't2-5']


def test_an_out_of_scope_question_is_handed_on_once_with_the_question_only():
    asked = t('t1', 'accounts', 'card_fees', entities={'card': '4821'})
    handed = AgentOutput(task_id='t1', agent='accounts', status='out_of_scope', suggested='relationship')
    update = orchestrator.gather(state([asked], [handed]))
    [send] = update['next']
    assert (send['task'].agent, send['task'].rerouted, send['task'].entities, send['task'].intent) == ('relationship', True, {}, 'open')
    assert send['history'] == [Message(role='user', content='question t1')] and send['inputs'] == {}
    # handed once: a rerouted task, a second wave, or an agent outside hands_to is not handed on again
    assert orchestrator.gather(state([update['tasks'][0]], [handed]))['next'] == []
    assert orchestrator.gather(state([asked], [handed], wave=2))['next'] == []
    public = AgentOutput(task_id='t1', agent='guide', status='out_of_scope', suggested='relationship')
    assert orchestrator.gather(state([t('t1', 'guide', 'phishing')], [public]))['next'] == []
    assert orchestrator.section_for(t('t1', 'guide', 'phishing'), public).answer == orchestrator.OUT_OF_SCOPE


def test_a_fee_waiver_in_accounts_is_out_of_scope_for_relationship():
    waiver = PlanTask(id='t1', agent='accounts', intent='card_fees', question='Can you waive my annual fee?')
    out = asyncio.run(rules.accounts_rules(SPECS['accounts'], waiver, ctx(), [], 'English'))
    assert (out.status, out.suggested, out.result) == ('out_of_scope', 'relationship', None)
    assert rules.WAIVER.search('could the late fee be waived') and not rules.WAIVER.search('what is the annual fee')


def test_the_graph_runs_a_second_wave_through_gather():
    graph = orchestrator.build_graph().get_graph()
    assert {(e.source, e.target) for e in graph.edges} >= {('agent', 'gather'), ('gather', 'agent'), ('gather', 'compose')}


# ---- Exports, provenance and the trip rules ----

def test_an_imported_export_is_evidence_for_the_receiving_agent():
    found = runner.input_evidence({'due_dates': Export(value=['2026-11-25'], source_ids=['my_cards', 'trip-checks'])})
    dumped = [e.model_dump() for e in found]
    assert source_checks.check_sources('Your card payment is paid on 2026-11-25.', ['input-due_dates'], dumped) is None
    assert source_checks.check_sources('Your card payment is paid on 2026-11-26.', ['input-due_dates'], dumped) == 'unsupported_figure'
    assert json.loads(found[0].text)['source_ids'] == ['my_cards', 'trip-checks']


def test_the_trip_window_is_read_never_guessed():
    today = date(2026, 11, 4)
    assert trip_checks.window({'from_date': '2026-11-10', 'to_date': '2026-11-20'}, '', today) == (date(2026, 11, 10), date(2026, 11, 20))
    assert trip_checks.window({}, 'I am away 10 to 20 November', today) == (date(2026, 11, 10), date(2026, 11, 20))
    assert trip_checks.window({}, 'away 28 Dec - 3 January', today) is None  # a range across months needs entities
    for bad in ({'from_date': '2026-11-20', 'to_date': '2026-11-10'}, {'from_date': '2026-10-01', 'to_date': '2026-10-05'},
            {'from_date': '2026-11-10', 'to_date': '2027-06-10'}, {'from_date': 'soon', 'to_date': '2026-11-20'}, {}):
        assert trip_checks.window(bad, 'next week', today) is None, bad


def test_due_dates_shift_past_weekends_and_public_holidays():
    holidays = {date(2026, 12, 25)}
    assert trip_checks.pays_on(date(2026, 11, 28), holidays) == (date(2026, 11, 30), 'weekend')
    assert trip_checks.pays_on(date(2026, 12, 25), holidays) == (date(2026, 12, 28), 'public holiday')
    assert trip_checks.pays_on(date(2026, 11, 25), holidays) == (date(2026, 11, 25), None)
    found = trip_checks.dues([('Card ending 4821 payment', date(2026, 11, 25))], date(2026, 11, 10), date(2026, 12, 31), holidays)
    assert [(d['due'], d['pays_on'], d['in_trip']) for d in found] == [('2026-11-25', '2026-11-25', True), ('2026-12-25', '2026-12-28', True)]
    # a due date the day before the trip that shifts into it counts
    found = trip_checks.dues([('Loan instalment', date(2026, 11, 7))], date(2026, 11, 9), date(2026, 11, 12), set())
    assert [(d['pays_on'], d['in_trip']) for d in found] == [('2026-11-09', True)]


def evidence(tool: str, rows: list) -> Evidence:
    return Evidence(id=tool, text=json.dumps({'records': rows}), publisher='UBS Connect', scope='test',
        source=Source(id=tool, title=tool, url=None, version='v1', publisher='UBS Connect'))


def holidays_reader(rows: list | None):
    async def read(name, args):
        assert (name, args['country']) == ('public_holidays', 'SG')
        if rows is None:return [runner.note('public_holidays-unavailable', 'This public reference could not be checked right now.')]
        return [evidence('public_holidays' if args['year'] == 2026 else f'public_holidays-{args["year"]}', rows)]
    return read


def test_trip_due_dates_export_only_what_code_computed():
    spec, item = SPECS['accounts'], t('t1', 'accounts', 'trip_due_dates', entities={'from_date': '2026-12-20', 'to_date': '2026-12-31'})
    item = item.model_copy(update={'question': 'Payments due while I am away?'})
    reads = [evidence('my_cards', [{**CARD, 'due_date': '2026-11-25'}]), evidence('my_loans', [{'type': 'Home loan', 'next_payment': '2026-12-01'}])]
    found = asyncio.run(derived.compute(spec, item, ctx(), reads, holidays_reader([{'date': '2026-12-25', 'national': True}])))
    assert found.exports['due_in_window'].value is True and found.exports['due_dates'].value == ['2026-12-28']
    assert found.exports['due_dates'].source_ids == ['my_cards', 'my_loans', 'public_holidays', 'trip-checks']
    body = json.loads(next(e for e in reads if e.id == 'trip-checks').text)
    assert body['holidays_checked'] and [d['shifted_for'] for d in body['due_dates'] if d['in_trip']] == ['public holiday']
    # holidays not checked: no exports (a dependant is skipped), and the evidence says so
    reads = [evidence('my_cards', [CARD])]
    found = asyncio.run(derived.compute(spec, item, ctx(), reads, holidays_reader(None)))
    assert found.exports == {} and json.loads(reads[-1].text)['holidays_checked'] is False
    with pytest.raises(derived.Clarify, match='Which dates'):
        asyncio.run(derived.compute(spec, item.model_copy(update={'entities': {}}), ctx(), [], holidays_reader([])))


def test_account_refs_carry_no_balances():
    found = derived.account_refs([evidence('my_accounts', [{'type': 'savings', 'number': '123-456789-0', 'currency': 'SGD', 'balance': 5000}])])
    assert found['account_refs'].value == [{'type': 'savings', 'currency': 'SGD', 'last4': '7890'}]
    assert derived.account_refs([runner.note('my_accounts-unavailable', 'x')]) == {}


def test_a_missing_trip_window_is_a_question_with_no_model_call(served, monkeypatch):
    async def no_model(*a, **kw):raise AssertionError('no model call for a clarifying question')
    monkeypatch.setattr(runner, 'checked_model_answer', no_model)
    out = asyncio.run(runner.run_agent(SPECS['accounts'], t('t1', 'accounts', 'trip_due_dates'), {}, ctx()))
    assert (out.status, out.origin, out.result.question, out.exports) == ('needs_clarification', 'safety', trip_checks.ASK_WINDOW, {})


# ---- Offer chains ----

def proposal(task_id: str, kind: str = 'handoff.create') -> OfferProposal:
    return OfferProposal(task_id=task_id, agent='compliance' if task_id == 't1' else 'relationship', type=kind, summary=f'offer {task_id}',
        target={'reason': 'general_support', 'priority': 'P3', 'summary': 'x'})


def test_a_dependants_offers_follow_its_upstream_and_the_first_is_chained():
    up = AgentOutput(task_id='t1', agent='compliance', status='needs_human', proposals=[proposal('t1')])
    other = AgentOutput(task_id='t3', agent='relationship', status='ok', proposals=[proposal('t3')])
    after = AgentOutput(task_id='t2', agent='relationship', status='ok', proposals=[proposal('t2'), proposal('t2')])
    rows = [(t('t1', 'compliance', 'review_status', priority='P0'), up), (t('t3', 'relationship', 'my_rm', priority='P2'), other),
        (t('t2', 'relationship', 'my_rm', depends_on='t1'), after)]
    order = orchestrator.offer_order(rows)
    assert [(p.task_id, p.chained) for p in order] == [('t1', False), ('t2', True), ('t2', False), ('t3', False)]
    # a withheld upstream offers nothing, so its dependant's offer is not chained to anything
    rows[0] = (rows[0][0], up.model_copy(update={'status': 'error'}))
    assert [(p.task_id, p.chained) for p in orchestrator.offer_order(rows)] == [('t3', False)]


def test_a_reply_cannot_start_with_a_chained_offer():
    case = RequestContext(session_id=uuid4(), case_id=uuid4(), principal=Principal(kind='customer', user_id=uuid4(), customer_id=1001, jwt='synthetic-test-token'))
    with pytest.raises(ActionIntegrityError, match='nothing to follow'):
        asyncio.run(offers.save([proposal('t2').model_copy(update={'chained': True})], case))


# ---- Through the graph ----

@pytest.mark.parametrize('due,second', [(True, 'relationship: question t2'), (False, orchestrator.CONDITION_FALSE['due_in_window'])])
def test_a_dependent_task_runs_in_a_second_wave_and_streams_in_place(monkeypatch, due, second):
    from tests.test_orchestrator_graph import context
    from app.services.guardrail import assess_request
    seen = []
    async def run(spec, task, inputs, ctx, history=None, language='English'):
        seen.append((task.id, sorted(inputs)))
        out = ok(task.id, spec.name, **({'due_in_window': due, 'due_dates': ['2026-11-25'] if due else []} if spec.name == 'accounts' else {}))
        return out.model_copy(update={'result': out.result.model_copy(update={'text': f'{spec.name}: {task.question}'})})
    async def model_plan(history):
        return [t('t1', 'accounts', 'trip_due_dates'), t('t2', 'relationship', 'meeting_times', depends_on='t1', condition='due_in_window')]
    monkeypatch.setattr(runner, 'run_agent', run);monkeypatch.setattr(orchestrator, 'model_plan', model_plan)
    req = ChatRequest(messages=[{'role': 'user', 'content': 'If a payment falls due while I am away, move my meeting'}], mode='personal')
    async def main():
        return [item async for item in orchestrator.stream(req, assess_request(req), context(conversation=False))]
    items = asyncio.run(main())
    assert [(kind, getattr(item, 'task_id', None)) for kind, item in items] == [('plan', None), ('section', 't1'), ('section', 't2'), ('answer', None)]
    assert seen == ([('t1', []), ('t2', ['due_dates'])] if due else [('t1', [])])
    assert [s.answer for s in items[-1][1].sections] == ['accounts: question t1', second]


# ---- Batch 3: rule-path exports and inputs, savings after the cards (P06), a failed check (P07), planner v10 ----

def answer(id_: str, agent: str, status: str = 'ok') -> AgentResult:
    return AgentResult(task_id=id_, agent=agent, status=status, answer_kind='conversation', text='done',
        **({'question': 'Which one?'} if status == 'needs_clarification' else {}))


def test_a_rules_answer_releases_only_declared_exports_and_only_when_ok():
    spec, item = SPECS['compliance'], t('t1', 'compliance', 'document_findings')
    values = {'check_failed': Export(value=True, source_ids=['document-prechecks']), 'due_dates': Export(value=[], source_ids=['x'])}
    assert set(rules.out(spec, item, answer('t1', 'compliance'), [], None, exports=values).exports) == {'check_failed'}
    assert rules.out(spec, item, answer('t1', 'compliance', 'needs_human'), [], None, exports=values).exports == {}
    assert rules.out(spec, item, answer('t1', 'compliance', 'error'), [], None, exports=values).exports == {}


def test_an_accounts_rule_answer_still_passes_her_account_list_on():
    from app.schemas.customer_data import BankAccount
    rows = [BankAccount.model_validate({'account_id': 'acc_1', 'type': 'savings', 'number': '****7890', 'currency': 'SGD', 'balance': 5000, 'status': 'active'})]
    found = derived.account_refs_from(rows)['account_refs']
    assert (found.value, found.source_ids) == ([{'type': 'savings', 'currency': 'SGD', 'last4': '7890'}], ['retail-accounts'])
    assert derived.account_refs_from([]) == {}


def test_computed_accounts_intents_skip_the_rules():
    for intent in rules.COMPUTED:
        assert asyncio.run(rules.accounts_rules(SPECS['accounts'], t('t1', 'accounts', intent), ctx(), [], 'English')) is None


ACCOUNTS_ROWS = [{'type': 'savings', 'number': '****1111', 'currency': 'SGD', 'balance': 8000, 'status': 'active'},
    {'type': 'savings', 'number': '****2222', 'currency': 'SGD', 'balance': 500, 'status': 'dormant'},  # not counted
    {'type': 'current', 'number': '****3333', 'currency': 'SGD', 'balance': 2000, 'status': 'active'}]  # not savings


def savings(threshold: str | None, cards: list | None = None, question: str = 'Would my savings stay above it after paying my cards?'):
    item = t('t1', 'accounts', 'savings_after_cards', entities={'threshold': threshold} if threshold else {}).model_copy(update={'question': question})
    reads = [evidence('my_accounts', ACCOUNTS_ROWS), evidence('my_cards', cards if cards is not None else
        [{**CARD, 'outstanding': 3850.25}, {**CARD, 'last4': '7310', 'outstanding': 0}])]
    async def no_read(name, args):raise AssertionError('no extra read')
    found = asyncio.run(derived.compute(SPECS['accounts'], item, ctx(), reads, no_read))
    return found, json.loads(next(e for e in reads if e.id == derived.SAVINGS_SOURCE).text), reads


def test_savings_after_paying_the_cards_against_her_amount():
    found, body, reads = savings('5000')
    assert found.exports['below_threshold'].value is True  # 8,000 - 3,850.25 = 4,149.75
    assert found.exports['below_threshold'].source_ids == ['my_accounts', 'my_cards', 'savings-checks']
    assert [body[k]['amount'] for k in ('savings_total', 'card_balances_total', 'savings_after_paying', 'threshold')] == [8000.0, 3850.25, 4149.75, 5000.0]
    assert [c['last4'] for c in body['card_balances']] == ['4821'] and [a['last4'] for a in body['savings_accounts']] == ['1111']
    dumped = [e.model_dump() for e in reads]
    assert source_checks.check_sources('You would have SGD 4,149.75 left, below SGD 5,000.00.', ['savings-checks'], dumped) is None
    assert source_checks.check_sources('You would have SGD 4,200.00 left.', ['savings-checks'], dumped) == 'unsupported_figure'
    assert savings('4000')[0].exports['below_threshold'].value is False
    assert savings('4149.75')[0].exports['below_threshold'].value is False  # exactly the amount is not below it
    assert 'account_refs' in found.exports  # the always-on list rides along


def test_savings_in_another_currency_or_without_an_amount_are_not_guessed():
    found, body, _ = savings('5000', cards=[{**CARD, 'currency': None, 'outstanding': 100}])
    assert 'below_threshold' not in found.exports and (body['checked'], body['not_checked']) == (False, 'other_currency')
    assert savings(None, question='Would I keep at least 5k after paying both cards?')[0].exports['below_threshold'].value is True
    with pytest.raises(derived.Clarify, match='What amount'):savings(None)
    assert [derived.amount(v) for v in ('5k', '5,000', 'SGD 5000', 'lots', '0', True)] == [5000, 5000, 5000, None, None, None]


PRECHECKS = [{'item': 'Proof of address', 'precheck': {'status': 'issues_found'}},
    {'item': 'Tax residency self-certification', 'precheck': {'status': 'no_issues'}},
    {'item': 'Source of wealth declaration', 'precheck': None}]


@pytest.mark.parametrize('rows,question,expected', [
    (PRECHECKS, 'Did my address proof pass the check?', True),
    (PRECHECKS, 'did my tax residency form pass?', False),
    (PRECHECKS, 'did my source of wealth declaration pass?', None),  # no current check: unknown
    (PRECHECKS, 'did my documents pass the check?', None),  # several items and none named
    ([{'item': 'Proof of address', 'precheck': {'status': 'unable_to_assess'}}], 'did it pass?', True),  # the only one shown
    ([], 'did my address proof pass?', None)])
def test_check_failed_comes_from_the_one_named_documents_current_check(rows, question, expected):
    found = derived.check_failed([evidence('document-prechecks', rows)], question)
    assert (found['check_failed'].value if found else None) is expected
    if found:assert found['check_failed'].source_ids == ['document-prechecks']
    assert derived.check_failed([], question) == {}


def test_compliance_rules_cite_her_account_list_and_export_the_check(monkeypatch):
    from app.services.agents import document
    seen = {}
    async def none(task, ctx):return []
    async def respond(task, history, language, ctx, public):
        seen['public'] = [e.id for e in public]
        return answer(task.task_id, 'document'), [*public, evidence('document-prechecks', PRECHECKS)], 'document/v5+x'
    monkeypatch.setattr(rules, 'knowledge', none);monkeypatch.setattr(document, 'respond', respond)
    item = t('t1', 'compliance', 'document_findings').model_copy(update={'question': 'Did my proof of address pass?'})
    refs = {'account_refs': Export(value=[{'type': 'savings', 'currency': 'SGD', 'last4': '1111'}], source_ids=['retail-accounts'])}
    out = asyncio.run(rules.compliance_rules(SPECS['compliance'], item, ctx('compliance'), [], 'English', refs))
    assert seen['public'] == ['input-account_refs'] and out.exports['check_failed'].value is True
    assert 'input-account_refs' in [e['id'] for e in out.evidence]


def test_telling_her_rm_is_a_handover_that_keeps_its_link():
    from app.services.guardrail import assess_request
    message = "did my address proof pass the check? if it didn't, tell my RM so she knows why it's late"
    risk = assess_request(ChatRequest(messages=[{'role': 'user', 'content': message}], mode='personal'))
    planned = [PlanTask(id='t1', agent='compliance', intent='review_status', question='Did my address proof pass the check?'),
        PlanTask(id='t2', agent='relationship', intent='my_rm', question="Tell my RM why it's late", depends_on='t1', condition='check_failed')]
    tasks, lines = orchestrator.linked(orchestrator.checked(orchestrator.overridden(planned, risk), PERSONAL), PERSONAL)
    assert lines == [] and [(x.agent, x.intent, x.depends_on, x.condition) for x in tasks] == [
        ('compliance', 'document_findings', None, None), ('relationship', 'open', 't1', 'check_failed')]
    assert tasks[1].entities.get('handover') == 'rm'
    meeting = PlanTask(id='t1', agent='relationship', intent='meeting_request', question='Tell my RM to book a meeting')
    assert 'handover' not in orchestrator.overridden([meeting], risk)[0].entities


def test_a_link_sets_the_intent_that_computes_its_export():
    facts: dict = {}
    tasks, lines = orchestrator.linked([t('t1', 'accounts', 'account_balance'),
        t('t2', 'relationship', 'meeting_request', depends_on='t1', condition='below_threshold')], PERSONAL, facts)
    assert lines == [] and tasks[0].intent == 'savings_after_cards'
    assert facts['intent_set'] == [{'task': 't1', 'from': 'account_balance', 'to': 'savings_after_cards'}]
    # an upstream agent that does not export it keeps its intent and the link is dropped
    tasks, lines = orchestrator.linked([t('t1', 'guide', 'general'),
        t('t2', 'relationship', 'meeting_request', depends_on='t1', condition='below_threshold')], PERSONAL)
    assert [x.intent for x in tasks] == ['general'] and [l.answer for l in lines] == [orchestrator.UNLINKED]


def test_the_planner_sees_imports_and_her_amount():
    lines = {line.split(':')[0]: line for line in orchestrator.catalogue().splitlines()}
    assert lines['- compliance'].endswith('Exports: check_failed (bool). Imports: account_refs.')
    assert 'below_threshold (bool)' in lines['- accounts'] and 'savings_after_cards' in lines['- accounts']
    planned = orchestrator.checked([t('t1', 'accounts', 'savings_after_cards', entities={'threshold': 'SGD 5k'}),
        t('t2', 'accounts', 'savings_after_cards', entities={'threshold': 'a lot'})], PERSONAL)
    assert [x.entities.get('threshold') for x in planned] == ['5000', None]


@pytest.mark.parametrize('condition', ['below_threshold', 'check_failed'])
def test_a_false_condition_has_its_own_fixed_line(condition):
    up = 'accounts' if condition == 'below_threshold' else 'compliance'
    tasks = [t('t1', up, 'savings_after_cards' if up == 'accounts' else 'document_findings'),
        t('t2', 'relationship', 'meeting_request', depends_on='t1', condition=condition)]
    [skip] = orchestrator.gather(state(tasks, [ok('t1', up, **{condition: False})]))['outputs']
    assert skip.result.text == orchestrator.CONDITION_FALSE[condition]
    assert orchestrator.gather(state(tasks, [ok('t1', up, **{condition: True})]))['next'][0]['task'].id == 't2'
