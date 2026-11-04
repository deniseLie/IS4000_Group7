"""The RM assistant on the v5 graph (build plan phase 9): the RM plan check, exports, fan-out, the consent condition, the
book refusal and each RM agent's tool boundary. Synthetic records stand in for the RM's SQL reads; the model-written
handlers are replaced by fixed sample replies, because these tests check graph mechanics, not answers (R21–R23 against
the real model are graph_cases.json)."""
import asyncio
from uuid import uuid4
import pytest
from app.core.config import settings
from app.schemas.contracts import Principal, RequestContext
from app.schemas.rm import RmAssistantRequest
from app.services import orchestrator, tools
from app.services.authorization import PolicyDenied
from app.services.rm import graph, handlers

BOOK = [{'customer_id': 1002, 'name': 'Michelle Tan (DEMO)'}, {'customer_id': 1003, 'name': 'David Lee (DEMO)'},
    *({'customer_id': 1004 + i, 'name': f'Client {i} (DEMO)'} for i in range(5))]


def rm_ctx() -> RequestContext:
    return RequestContext(session_id=uuid4(), principal=Principal(kind='customer', user_id=uuid4(), jwt='synthetic-test-token'))


def reviews(n: int, due: str = '2026-11-30') -> list[dict]:
    return [{'customer_id': c['customer_id'], 'client': c['name'], 'title': 'Periodic review', 'status': 'open', 'due_date': due,
        'due_text': '30 Nov 2026', 'outstanding_items': ['Proof of address'], 'open_bank_questions': 0} for c in BOOK[:n]]


@pytest.fixture
def records(monkeypatch):
    """The RM's SQL reads, recorded with the agent that asked; the direct transport runs the real tools.call checks."""
    calls: list[tuple[str, str | None]] = []
    data = {'reviews': reviews(2), 'consent': True, 'outside': False}
    def tool(name):
        async def read(**args):
            ctx = tools.TOOL_CTX.get()
            calls.append((name, ctx.agent))
            if name == 'rm_book':return {'data': BOOK, 'as_of': '2026-11-04T09:00:00+08:00'}
            if name == 'rm_match_clients':
                inside = [c for c in BOOK if c['name'].split()[0] in args['text']]
                return {'in_book': inside, 'outside_book': [{'name': 'Wei Ming'}] if data['outside'] else []}
            if name == 'rm_review_status':return {'data': data['reviews'], 'as_of': '2026-11-04T09:00:00+08:00'}
            if name == 'rm_handover_sources':return {'data': {'eligible': True, 'shared': {'consent_active': data['consent']}}, 'as_of': 'now'}
            if name == 'rm_shared_summary':
                shared = [{'summary_id': 's1', 'content': 'Prefers calls after 5pm.', 'shared_at': 'x', 'shared_text': '1 Nov 2026'}]
                return {'data': {'consent_active': data['consent'], 'shared': shared if data['consent'] else []}, 'as_of': 'now'}
            return {'data': {}, 'as_of': 'now'}
        return read
    monkeypatch.setattr(settings, 'tool_transport', 'direct')
    monkeypatch.setattr(tools, 'TOOLS', {**tools.TOOLS, **{n: tool(n) for n in tools.TOOLS if n.startswith('rm_')}})
    return calls, data


def sample(intent: str, reads: tuple[str, ...] = ()):
    """A fixed handler reply after the intent's reads (no model call)."""
    async def handler(t, plan, client_id):
        for name in reads:await t.read(name, {'customer_id': client_id} if client_id else {})
        reply = {'status': 'ok', 'intent': intent, 'summary': f'{intent} for {t.name(client_id)}', 'clarification': None,
            'sections': [{'heading': intent, 'items': [{'text': 'x', 'kind': 'record', 'owner': 'rm', 'source_ids': []}]}],
            'context': t.context(client_id), 'sources': [], 'validation': {'checked': 1, 'withheld': 0, 'reasons': []}, 'origin': 'ai'}
        if intent == 'draft_message':reply['draft'] = {'customer_id': client_id, 'client': t.name(client_id), 'reference': None, 'text': 'Hi', 'revision': 1}
        return reply
    return handler


def run(message: str, rows: list[dict] | None, **request) -> dict:
    async def say(text):pass
    return asyncio.run(graph.respond(RmAssistantRequest(message=message, **request), rm_ctx(), say))


def plan(monkeypatch, rows):
    async def model_plan(rt, message):return rows
    monkeypatch.setattr(graph, 'model_plan', model_plan)


R21 = [{'intent': 'reviews', 'depends_on': 0}, {'intent': 'draft_message', 'depends_on': 1, 'for_each': 'clients_due'}]
R23 = [{'intent': 'handover_brief', 'client_name': 'David', 'depends_on': 0},
    {'intent': 'shared_note', 'client_name': 'David', 'depends_on': 1, 'condition': 'consent_active'}]


def test_each_intent_belongs_to_one_rm_agent_and_links_survive_the_plan_check():
    tasks = graph.tasks_from([*R21, {'intent': 'made_up'}], 'q')
    assert [(t.agent, t.intent, t.depends_on, t.for_each) for t in tasks] == [('rm_client_insight', 'reviews', None, None),
        ('rm_service_desk', 'draft_message', 't1', 'clients_due'), ('rm_client_insight', 'help', None, None)]
    kept, lines = orchestrator.linked(tasks, RmAssistantRequest(message='q'))
    assert len(kept) == 3 and not lines
    # R23: the brief leaves the client's note to the dependent step, so it is shown once and only under consent.
    brief, note = graph.tasks_from(R23, 'q')
    assert brief.entities['shared'] is False and note.condition == 'consent_active'


def test_a_link_the_rm_specs_do_not_declare_is_dropped_with_a_fixed_line():
    tasks = graph.tasks_from([{'intent': 'reviews'}, {'intent': 'draft_message', 'depends_on': 1, 'condition': 'clients_due'}], 'q')
    kept, lines = orchestrator.linked(tasks, RmAssistantRequest(message='q'))
    assert [t.intent for t in kept] == ['reviews'] and lines[0].answer == orchestrator.UNLINKED


def test_exports_are_computed_from_what_the_handler_read():
    turn = handlers.Turn(RmAssistantRequest(message='a reminder to every client with a review due this month'), rm_ctx(), None)
    turn.reads['rm_review_status'] = {'data': [*reviews(1), *reviews(2, '2027-03-01')[1:]]}
    due = graph.exports_for('reviews', turn)['clients_due']
    assert [c['customer_id'] for c in due.value] == [1002] and due.source_ids == ['review-1002']
    turn.reads['rm_meeting_notes'] = {'data': {'notes': [{'note_id': 'n2', 'met_on': '2026-10-30'}, {'note_id': 'n1', 'met_on': '2026-09-01'}]}}
    assert graph.exports_for('meeting_brief', turn)['latest_note'].value == 'n2'
    turn.reads['rm_handover_sources'] = {'data': {'eligible': False}}
    assert 'consent_active' not in graph.exports_for('handover_brief', turn)  # no reassignment: unknown, so a dependant is skipped


def test_r21_fans_out_one_draft_per_client_due_at_most_five(records, monkeypatch):
    calls, data = records
    data['reviews'] = reviews(7)
    plan(monkeypatch, R21)
    monkeypatch.setitem(handlers.HANDLERS, 'reviews', sample('reviews', ('rm_review_status',)))
    monkeypatch.setitem(handlers.HANDLERS, 'draft_message', sample('draft_message'))
    reply = run('draft a short reminder to every client of mine with a review due this month', R21)
    assert [d['customer_id'] for d in reply['drafts']] == [c['customer_id'] for c in BOOK[:5]] and reply['draft'] is None
    assert reply['summary'] == graph.SEVERAL
    assert any(orchestrator.MORE_ITEMS.format(shown=5, total=7) in i['text'] for s in reply['sections'] for i in s['items'])
    assert ('rm_review_status', 'rm_client_insight') in calls and ('rm_book', None) in calls


def test_r23_shares_the_note_only_under_consent(records, monkeypatch):
    calls, data = records
    plan(monkeypatch, R23)
    monkeypatch.setitem(handlers.HANDLERS, 'handover_brief', sample('handover_brief', ('rm_handover_sources',)))
    reply = run("I just inherited David. If he's agreed to share his handover note, include it in a brief", R23)
    note = [s for s in reply['sections'] if s['heading'].startswith('Handover note shared by David')]
    assert note and note[0]['items'][0]['text'].startswith('Prefers calls after 5pm.') and note[0]['items'][0]['kind'] == 'client_stated'
    assert ('rm_shared_summary', 'rm_client_insight') in calls
    calls.clear();data['consent'] = False
    reply = run("I just inherited David. If he's agreed to share his handover note, include it in a brief", R23)
    texts = [i['text'] for s in reply['sections'] for i in s['items']]
    assert orchestrator.CONDITION_FALSE['consent_active'] in texts and not any('5pm' in t for t in texts)
    assert 'rm_shared_summary' not in {name for name, _ in calls}


def test_someone_outside_the_book_is_refused_before_planning(records, monkeypatch):
    calls, data = records
    data['outside'] = True
    async def no_plan(rt, message):raise AssertionError('no planner call for a refusal')
    monkeypatch.setattr(graph, 'model_plan', no_plan)
    reply = run('Show me Wei Ming', None)
    assert reply['status'] == 'denied' and {name for name, _ in calls} == {'rm_book', 'rm_match_clients'}


def test_a_selected_client_who_left_the_book_and_restricted_asks_are_refused_before_planning(records, monkeypatch):
    calls, _ = records
    async def no_plan(rt, message):raise AssertionError('no planner call for a refusal')
    monkeypatch.setattr(graph, 'model_plan', no_plan)
    gone = run('Overview please', None, context={'client_id': 9999})
    assert gone['status'] == 'denied' and gone['context']['client_id'] is None and {name for name, _ in calls} == {'rm_book'}
    calls.clear()
    restricted = run("Mark Michelle's fraud ticket resolved", None)  # specialist status and card actions are never chat writes
    assert restricted['status'] == 'denied' and restricted['intent'] == 'access_check' and 'Nothing has been changed' in restricted['summary']
    assert {name for name, _ in calls} == {'rm_book', 'rm_match_clients'}


def test_a_workspace_button_skips_the_planner_and_one_reply_keeps_its_shape(records, monkeypatch):
    async def no_plan(rt, message):raise AssertionError('a button sets the intent')
    monkeypatch.setattr(graph, 'model_plan', no_plan)
    monkeypatch.setitem(handlers.HANDLERS, 'ticket_inbox', sample('ticket_inbox', ('rm_tickets',)))
    reply = run('What needs my attention?', None, intent='ticket_inbox')
    assert reply['intent'] == 'ticket_inbox' and reply['summary'] == 'ticket_inbox for None' and 'drafts' not in reply


SECTION = {'article_id': 'card-limit-change', 'version': 1, 'anchor': 'main', 'cite': 'kb:card-limit-change@v1#main',
    'title': 'Changing a card limit', 'heading': 'Changing a card limit', 'body': 'Card limit changes are decided by the cards team.',
    'publisher': 'DemoBank', 'scope': 'Demo guidance', 'reference_url': None, 'checked_on': None, 'review_due': None, 'score': 0.0}


def test_ticket_guidance_is_read_with_the_service_desk_permission(records, monkeypatch):
    """Regression: the article a ticket's request type names is read by the Service Desk through its own spec. The real
    tool, kb.get_article and policy run; only the database client and RPC response are synthetic."""
    from contextlib import nullcontext
    from app.services import audit, kb
    from app.services.rm import evidence as ev
    rpcs = []
    async def rpc(call, name):
        rpcs.append(name)
        return type('Response', (), {'data': [SECTION]})()
    monkeypatch.setattr(tools.data_api, 'connect', lambda ctx: nullcontext(None))
    monkeypatch.setattr(kb, 'execute', rpc)
    detail = {'data': {'reference': 'TCK-1001', 'customer_id': 1002, 'category': 'card_limit',
        'category_info': {'label': 'Card limit change', 'kb_article': 'card-limit-change'}}, 'as_of': 'now'}
    async def main():
        desk = rm_ctx().model_copy(update={'agent': 'rm_service_desk'})
        batch = audit.start_batch(session_id=desk.session_id)
        try:
            guidance = await ev.category_guidance(detail, lambda name, args: tools.call(name, args, desk))
            assert [e.outcome for e in batch.events if e.action == 'policy.decided'] == ['success']
        finally:audit.current_batch.set(None)
        bundle = ev.ticket_detail_bundle(detail, guidance)
        assert 'kb-card-limit-change' in bundle.ids() and rpcs == ['kb.search']
        assert await ev.category_guidance({'data': {'category_info': {}}}, None) == []  # no article named: nothing read
        insight = desk.model_copy(update={'agent': 'rm_client_insight'})
        with pytest.raises(PolicyDenied):await ev.category_guidance(detail, lambda name, args: tools.call(name, args, insight))
    asyncio.run(main())


def test_each_rm_agent_may_call_only_its_own_tools(records):
    async def main():
        desk = rm_ctx().model_copy(update={'agent': 'rm_service_desk'})
        await tools.call('rm_tickets', {}, desk)
        with pytest.raises(PolicyDenied):await tools.call('rm_review_status', {}, desk)
        insight = desk.model_copy(update={'agent': 'rm_client_insight'})
        with pytest.raises(PolicyDenied):await tools.call('rm_tickets', {}, insight)
    asyncio.run(main())


def test_several_tasks_without_names_take_the_one_client_named_in_the_message(records, monkeypatch):
    calls, data = records
    plan(monkeypatch, [{**task, 'client_name': ''} for task in R23])
    monkeypatch.setitem(handlers.HANDLERS, 'handover_brief', sample('handover_brief', ('rm_handover_sources',)))
    reply = run("I just inherited David. If he's agreed to share his handover note, include it in a brief", None)
    assert reply['clarification'] is None and any(s['heading'].startswith('Handover note shared by David') for s in reply['sections'])
    calls.clear()
    plan(monkeypatch, [{'intent': 'client_overview', 'depends_on': 0}, {'intent': 'ticket_inbox', 'depends_on': 0}])
    monkeypatch.setitem(handlers.HANDLERS, 'client_overview', sample('client_overview'))
    monkeypatch.setitem(handlers.HANDLERS, 'ticket_inbox', sample('ticket_inbox'))
    reply = run('Compare Michelle and David, then show my inbox', None)  # two clients named: the task must name its own
    assert 'Which client is this about?' in str(reply) and 'client_overview for' not in str(reply)  # the overview asks
