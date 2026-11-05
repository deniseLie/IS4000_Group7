"""Code renders the records, the model writes the judgement (docs/planning/rm-answer-speed.md): the record sections of
the client reports are built here from synthetic records with no model, every item cites a record the RM can open, and
the model's facts are a bounded extract with the same ids."""
from app.services.rm import brief_facts as bf
from app.services.rm import evidence as ev

CID = 1003


def data(**extra) -> dict:
    return {'profile': {'customer_id': CID, 'name': 'David Lee (DEMO)', 'employment_status': 'employed', 'income_range': '120k-150k'},
        'relationship': {'assigned_since_text': '1 Oct 2026', 'inherited': True, 'previous_advisor': 'Rachel Ng (DEMO)', 'previous_to_text': '30 Sep 2026'},
        'accounts': [{'type': 'savings', 'currency': 'SGD', 'balance': 80000, 'status': 'active', 'account_no': '123'}],
        'cards': [{'name': 'Visa Signature', 'status': 'active', 'currency': 'SGD', 'credit_limit': 20000, 'outstanding_balance': 1200}],
        'loans': [], 'tickets': [{'reference': f'TCK-00100{i}', 'category_label': 'Card limit change', 'status': 'open', 'rm_owned': i == 1,
            'owner_team': 'cards', 'summary': 'x' * 400, 'customer_id': CID} for i in range(1, 6)],
        'compliance': {'title': 'Periodic review', 'status': 'not_submitted', 'due_text': '30 Nov 2026', 'outstanding_items': [{'label': 'Proof of address'}],
            'open_bank_questions': [{}]},
        'rm_requests': [{'what': 'Call me about the transfer', 'status': 'open', 'urgency': 'normal'}],
        'tasks': [{'title': 'Send rate sheet', 'due_text': '10 Nov 2026', 'overdue': False}], 'milestones': [],
        'preferences': [{'preference': f'Preference {i}', 'recorded_text': '1 Oct 2026'} for i in range(5)],
        'appointments': [], 'gaps': ['no_interaction_in_180_days'], 'interactions': [], **extra}


NOTES = [{'note_id': f'n{i}', 'met_on': f'2026-0{9 - i}-01', 'met_on_text': f'1 {m} 2026', 'format': 'phone', 'written_by_current_rm': i == 0,
    'author': 'Rachel Ng (DEMO)', 'title': f'Note {i}', 'summary': f'Summary {i}', 'commitments': ['Review the transfer'] if i == 0 else [],
    'client_requests': [], 'decisions': []} for i, m in enumerate(('Sep', 'Aug', 'Jul'))]


def bundle(d: dict, notes=NOTES, withheld=0, points=None, shared=None, cash=None) -> ev.Bundle:
    b = ev.client_bundle({'data': d, 'as_of': 'now'})
    ev.add_notes(b, notes, 'now', CID, withheld)
    if not notes:b.add(ev.Evidence('notes-none', 'Meeting notes', 'record', {'notes_recorded': 0}, 'now', CID))
    b.add(ev.shared_evidence(shared or {'data': {'consent_active': False}, 'as_of': 'now'}, CID))
    b.add(ev.cash_evidence(cash or {'shared': True, 'data': []}, CID))
    b.add(ev.insights_evidence({'shared': False, 'category': 'transaction_insights'}, CID))
    b.add(ev.talking_points_evidence(points or {'shared': True, 'data': [], 'pending': True}, CID))
    return b


def cited(sections: list[dict]) -> set[str]:return {i for s in sections for x in s['items'] for i in x['source_ids']}


def test_record_sections_follow_the_brief_order_and_cite_only_records_in_the_bundle():
    d = data()
    b = bundle(d, withheld=2)
    sections = bf.brief_sections(d, NOTES, 2, b, None, [])
    assert [s['heading'] for s in sections] == ['The meeting', 'Who the client is', 'History and commitments', 'Open matters',
        'What the client has told us', 'Gaps to check']
    assert all(x['source_ids'] for s in sections for x in s['items']) and cited(sections) <= set(b.ids()) | {'selected-appointment'}
    who = [x['text'] for x in sections[1]['items']]
    assert who[0] == 'David Lee (DEMO) has been with you since 1 Oct 2026. Inherited from Rachel Ng (DEMO) on 30 Sep 2026.'
    assert who[2] == 'Products: 1 account (savings) and 1 credit card (Visa Signature).' and '80000' not in str(who)  # no balances in prose
    assert sections[0]['items'][0]['kind'] == 'gap'  # no confirmed meeting


def test_history_shows_the_latest_note_and_counts_the_rest():
    history = bf.history_section(NOTES, withheld=1)
    texts = [x['text'] for x in history['items']]
    assert texts[0] == 'Latest note: 1 Sep 2026, phone (your note): Note 0.' and 'Summary 0' in texts and 'Summary 1' not in str(texts)
    assert 'Commitment: Review the transfer (recorded, not necessarily completed).' in texts
    assert texts[-2] == '2 earlier notes are recorded, the most recent on 1 Aug 2026. Ask for the meeting history to see them.'
    assert history['items'][-2]['source_ids'] == ['note-2026-08-01', 'note-2026-07-01'] and history['items'][-1]['source_ids'] == ['notes-withheld']
    assert bf.history_section([])['items'] == [bf.gap('No meeting notes are recorded.', ['notes-none'])]


def test_open_matters_show_five_items_then_a_count_line_citing_the_rest():
    rows = bf.open_section(data())['items']
    assert len(rows) == 6 and rows[0]['text'] == 'Periodic review: not sent yet, due 30 Nov 2026; still to send: Proof of address; 1 bank question open.'
    assert rows[1]['owner'] == 'rm' and rows[2]['owner'] == 'specialist' and 'specialist-managed (cards), read-only' in rows[2]['text']
    assert rows[-1]['text'] == '3 more open items are not listed here; the client page shows them.'
    assert rows[-1]['source_ids'] == ['ticket-TCK-001005', 'rm-requests', 'tasks']
    empty = bf.open_section(data(compliance=None, tickets=[], rm_requests=[], tasks=[]))
    assert empty['items'][0]['text'].startswith('No open review')


def test_what_the_client_told_us_keeps_inferred_topics_as_suggestions_and_states_each_consent_gap():
    point = {'point_id': '5b0e7f6a-0000-4000-8000-000000000002', 'topic': 'home_loan', 'stage': 'planning', 'timeframe': 'within_12m',
        'concerns': ['rates'], 'origin': 'inferred', 'first_seen': '2026-10-01', 'last_seen': '2026-10-28'}
    confirmed = {**point, 'point_id': '5b0e7f6a-0000-4000-8000-000000000003', 'topic': 'investments', 'origin': 'confirmed'}
    d = data()
    told = bf.told_section(d, bundle(d, points={'shared': True, 'data': [point, confirmed]}))['items']
    assert [x['kind'] for x in told] == ['client_stated'] * 3 + ['suggestion', 'client_stated', 'gap', 'gap']  # top 3 preferences
    assert told[3]['text'].endswith('Not confirmed by the client.') and not told[4]['text'].endswith('Not confirmed by the client.')
    assert told[5]['text'] == 'No handover note is shared with you under active consent.'
    assert told[6]['text'] == 'The client has not shared transaction insights with you.'
    pending = bf.told_section(d, bundle(d))['items']
    assert bf.gap("Some topics from the client's chats are not available yet.", ['talking-points']) in pending
    gated = bf.told_section(d, bundle(d, points={'shared': False, 'category': 'chat_topics', 'data': [confirmed]}))['items']
    assert [(x['kind'], x['text']) for x in gated if 'chats' in x['text']] == [('client_stated', "Topic from the client's chats: Investments · "
        'planning · within 12 months · asks about rates · shared by the client (last mentioned 28 Oct 2026).'),
        ('gap', 'The client has not shared topics from their chats with you.')]  # a topic she shared herself still shows


def test_the_model_facts_are_bounded_and_keep_the_full_records_ids():
    d = data(appointments=[{'appointment_id': 'a1', 'status': 'confirmed', 'when': 'Mon 9 Nov 2026, 10:00 am'}])
    cash = {'shared': True, 'data': [{'amount': a, 'currency': 'SGD', 'date': '2026-10-01'} for a in (10000, -90000, 25000, 12000, 50000)]}
    full = bundle(d, cash=cash)
    facts = bf.facts(full, NOTES)
    assert set(facts.ids()) <= set(full.ids()) and {'appointments', 'note-2026-08-01', 'note-2026-07-01'} & set(facts.ids()) == set()
    assert [r['amount'] for r in facts.get('cash-events').data] == [-90000, 50000, 25000]  # the 3 largest movements either way
    assert len(facts.get('ticket-TCK-001001').data['summary']) == bf.TEXT_CHARS and 'customer_id' not in facts.get('ticket-TCK-001001').data
    assert facts.get('accounts').data == [{'type': 'savings', 'currency': 'SGD', 'balance': 80000, 'status': 'active'}]  # no account number
    assert len(facts.get('preferences').data) == bf.TOP and facts.get('note-2026-09-01').data['written_by'] == 'you'
    assert all(facts.get(i).title == full.get(i).title for i in facts.ids())
    tickets = [i for i in facts.ids() if i.startswith('ticket-')]
    assert tickets == ['ticket-TCK-001001', 'ticket-TCK-001002', 'ticket-TCK-001003']  # hers first, then as listed; however many are open


def test_open_tickets_reach_the_follow_ups_model_counted_by_category():
    from app.services.rm.handlers import open_by_category
    tickets = [{'reference': f'TCK-0010{i:02d}', 'category': 'suspected_fraud' if i % 3 else 'rm_handover', 'status': 'submitted'} for i in range(40, 0, -1)]
    assert open_by_category(tickets) == {'suspected_fraud': {'open': 27, 'latest': 'TCK-001040'}, 'rm_handover': {'open': 13, 'latest': 'TCK-001039'}}


def test_only_the_models_points_are_its_own_and_each_heading_is_capped():
    schema = bf.points_schema(['profile', 'note-2026-09-01'])
    assert schema['title'] == 'rm_brief_points' and schema['required'] == ['summary', 'conversation_starters', 'discussion_points', 'questions']
    assert schema['properties']['questions']['items']['properties']['source_ids']['items']['enum'] == ['profile', 'note-2026-09-01']
    sections = bf.point_sections({'summary': 's', 'conversation_starters': [{'text': f'q{i}', 'source_ids': ['profile']} for i in range(6)],
        'discussion_points': ['not a point'], 'questions': []})
    assert [(s['heading'], len(s['items'])) for s in sections] == [('Conversation starters', 3), ('Suggested discussion points', 0), ('Questions to ask', 0)]
    assert {x['kind'] for x in sections[0]['items']} == {'suggestion'}
    ordered = bf.in_order([*sections, bf.meeting_section(None)], bf.BRIEF_ORDER)
    assert [s['heading'] for s in ordered] == ['The meeting', 'Conversation starters']  # empty headings are dropped
