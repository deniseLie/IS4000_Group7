"""Build plan 2b: urgent card help without a Security agent. A charge the client doesn't recognise or a lost card gets fixed
steps (no model) and one urgent request to her RM, which is sent only when she confirms. These are the fixed texts and the
handoff target, built by code. Pure rules: no model or database (the native ticket and RM inbox check is in
test_card_dispute.py)."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from app.schemas.actions import HandoffResult, HandoffTarget
from app.services import orchestrator, rm_handover


def test_the_urgent_request_is_a_p0_rm_handover_with_its_own_topic() -> None:
    target = rm_handover.urgent_target('charge', 'someone used my card 4111 1111 1111 1111 in London', 'card ending 7310 (active)')
    HandoffTarget.model_validate(target)  # the executor's own target check
    assert target['reason'] == 'general_support' and target['priority'] == 'P0'  # 0082: an urgent rm_handover ticket for her RM
    summary = target['summary']
    assert summary.startswith('[Urgent card] Charge the client does not recognise. Cards on record: card ending 7310 (active).')
    assert rm_handover.urgent(summary) and rm_handover.topic_of(summary) == rm_handover.URGENT_TOPIC
    assert '4111 1111 1111 1111' not in summary and 'London' in summary and len(summary) <= rm_handover.SUMMARY_LIMIT
    lost = rm_handover.urgent_target('lost', 'i lsot my crad ' + 'x' * 900)['summary']
    assert lost.startswith('[Urgent card] Card reported lost or stolen.') and len(lost) <= rm_handover.SUMMARY_LIMIT
    assert not rm_handover.urgent(rm_handover.summary_for('Can you waive my annual fee?'))


def test_the_fixed_texts_never_promise_what_the_assistant_cannot_do() -> None:
    for text in (rm_handover.URGENT_STEPS, rm_handover.URGENT_LOST_STEPS):
        assert 'Freeze card' in text and 'fraud line on the back of your card' in text and 'PIN' in text and 'TCK-' not in text
    for text in (rm_handover.URGENT_STEPS, rm_handover.URGENT_LOST_STEPS, rm_handover.URGENT_SENT, rm_handover.URGENT_REUSED,
            rm_handover.URGENT_DECLINED, rm_handover.URGENT_FAILED, rm_handover.URGENT_SEPARATE):
        assert 'fraud team' not in text and 'Only the bank can unfreeze' not in text and 'is blocked' not in text
    assert 'nothing has been sent' in rm_handover.URGENT_FAILED and 'haven’t sent anything' in rm_handover.URGENT_DECLINED


def outcome(state: str, summary: str, created: bool = True):
    handoff = HandoffResult(handoff_id=uuid4(), priority='P0', status='open', created=created)
    return SimpleNamespace(command=SimpleNamespace(state=state, target=HandoffTarget(reason='general_support', priority='P0', summary=summary)),
        receipt=SimpleNamespace(outcome='succeeded', result=handoff) if state == 'succeeded' else None)


def test_her_yes_quotes_the_rm_ticket_and_her_no_sends_nothing(monkeypatch) -> None:
    async def reference(handoff_id, ctx):return 'TCK-001234'
    monkeypatch.setattr(rm_handover, 'ticket_reference', reference)
    urgent = rm_handover.urgent_target('charge', 'a charge I did not make')['summary']
    sent = asyncio.run(orchestrator.outcome_text(outcome('succeeded', urgent), None))
    assert sent.startswith('I’ve sent this to your relationship manager as urgent (reference TCK-001234).') and 'in Requests' in sent
    assert asyncio.run(orchestrator.outcome_text(outcome('rejected', urgent), None)) == rm_handover.URGENT_DECLINED
    plain = asyncio.run(orchestrator.outcome_text(outcome('succeeded', '[Fees or rates] Client question for you: waive my fee'), None))
    assert plain == rm_handover.handed_over('TCK-001234') and 'urgent' not in plain
    assert asyncio.run(orchestrator.outcome_text(outcome('rejected', '[Fees or rates] waive my fee'), None)) == orchestrator.CANCELLED


def test_a_yes_that_returns_the_chats_earlier_handoff_never_claims_a_new_urgent_request(monkeypatch) -> None:
    """One open general_support handoff per case (0003): after an RM request in the same chat, her yes returns that one."""
    urgent = rm_handover.urgent_target('lost', 'i lost my card')['summary']
    calls: list[tuple] = []
    async def reference(handoff_id, ctx):return 'TCK-001050'
    async def note(ref, text, ctx):calls.append(('note', ref, text))
    async def separate(text, ctx):
        calls.append(('separate', text))
        return 'TCK-001051'
    monkeypatch.setattr(rm_handover, 'ticket_reference', reference)
    monkeypatch.setattr(rm_handover, 'follow_up', note)
    monkeypatch.setattr(rm_handover, 'open_separate', separate)
    for topic, expected, call in ((rm_handover.URGENT_TOPIC, rm_handover.URGENT_REUSED.format(reference=' (reference TCK-001050)'), ('note', 'TCK-001050', urgent)),
            ('Fees or rates', rm_handover.URGENT_SEPARATE.format(reference=' (reference TCK-001051)'), ('separate', urgent))):
        async def topic_of(ref, ctx, topic=topic):return topic
        monkeypatch.setattr(rm_handover, 'topic_of_reference', topic_of)
        calls.clear()
        assert asyncio.run(orchestrator.outcome_text(outcome('succeeded', urgent, created=False), None)) == expected and calls == [call]
    async def broken(text, ctx):raise rm_handover.ActionIntegrityError('no reference')
    monkeypatch.setattr(rm_handover, 'open_separate', broken)
    assert asyncio.run(orchestrator.outcome_text(outcome('succeeded', urgent, created=False), None)) == rm_handover.URGENT_FAILED


def test_a_lost_card_offers_one_urgent_rm_request_for_her_confirmation(monkeypatch) -> None:
    saved = []
    async def none(kind, ctx):return None
    async def cards(ctx):return 'card ending 4821 (active)'
    async def save(proposals, ctx, turn):
        saved.extend(proposals)
        return []
    monkeypatch.setattr(rm_handover, 'open_urgent', none)
    monkeypatch.setattr(rm_handover, 'urgent_cards', cards)
    monkeypatch.setattr(orchestrator.offers, 'save', save)
    text, _ = asyncio.run(orchestrator.lost_card_offer('my card was stolen', None, uuid4()))
    assert rm_handover.URGENT_OFFER['lost'] in text and orchestrator.CONFIRM_LINE in text
    [proposal] = saved
    assert proposal.agent == 'relationship' and proposal.type == 'handoff.create' and proposal.target['priority'] == 'P0'
    assert proposal.target['summary'].startswith('[Urgent card] Card reported lost or stolen. Cards on record: card ending 4821 (active).')


def test_an_open_lost_card_request_is_quoted_and_a_failure_says_nothing_was_sent(monkeypatch) -> None:
    async def existing(kind, ctx):return 'TCK-000777'
    monkeypatch.setattr(rm_handover, 'open_urgent', existing)
    text, group = asyncio.run(orchestrator.lost_card_offer('i lost my card', None, uuid4()))
    assert group is None and 'already have an open urgent card request' in text and 'TCK-000777' in text
    async def broken(kind, ctx):raise PermissionError('denied')
    monkeypatch.setattr(rm_handover, 'open_urgent', broken)
    assert asyncio.run(orchestrator.lost_card_offer('i lost my card', None, uuid4())) == (rm_handover.URGENT_FAILED, None)
