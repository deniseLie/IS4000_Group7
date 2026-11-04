"""v5 offers (build plan phase 4): reply words, policy and refusals before any database call. SQL is in test_offer_groups.py."""
import asyncio
from uuid import uuid4
import pytest
from app.schemas.contracts import Principal, RequestContext
from app.services import offers
from app.services.actions.store import ActionIntegrityError
from app.services.authorization import PolicyDenied, decide
from app.services.offers import Proposal, reply_kind


def ctx(agent=None, kind='customer', case=True) -> RequestContext:
    principal = Principal(kind='customer', user_id=uuid4(), customer_id=1001, jwt='synthetic-test-token') if kind == 'customer' else Principal(kind='guest')
    return RequestContext(session_id=uuid4(), agent=agent, principal=principal, case_id=uuid4() if case else None)


@pytest.mark.parametrize('text,kind', [('yes', 'yes'), ('Yes.', 'yes'), ('YES please!', 'yes'), ('ok', 'yes'), ('go ahead', 'yes'),
    ('Yes, do it', 'yes'), ('sure', 'yes'), ('no', 'no'), ('No thanks.', 'no'), ('cancel', 'no'), ("don't", 'no'), ('nope', 'no'),
    ('yes but also what is my balance?', 'other'), ('yes and no', 'other'), ('no, I meant the other card', 'other'),
    ('ya', 'other'), ('oui', 'other'), ('是', 'other'), ('', 'other'), ('maybe', 'other'), ('yes yes yes yes yes yes', 'other')])
def test_reply_kind_reads_whole_english_replies_only(text, kind):
    assert reply_kind(text) == kind


def test_every_agent_offer_requires_confirmation_even_an_automatic_type():
    base = ctx()
    def obligations(action, agent):
        return decide(base.principal, action, {'customer_id': 1001}, base.model_copy(update={'agent': agent})).obligations
    assert 'require:confirmation' in obligations('ticket.create', 'accounts')
    assert 'require:confirmation' in obligations('handoff.create', 'security')
    assert 'require:confirmation' not in obligations('handoff.create', None)  # a UI form path


def test_an_offer_outside_the_agents_spec_is_refused_before_any_database_call():
    proposal = Proposal(task_id='t1', agent='security', type='rm.share_summary', target={}, summary='Share a summary')
    with pytest.raises(PolicyDenied, match='agent_tool_forbidden'):asyncio.run(offers.save([proposal], ctx()))
    with pytest.raises(PolicyDenied, match='agent_tool_forbidden'):
        asyncio.run(offers.save([Proposal(task_id='t1', agent='guide', type='handoff.create', target={}, summary='x')], ctx()))


def test_offers_need_a_customer_case_and_a_bounded_group():
    proposal = Proposal(task_id='t1', agent='accounts', type='ticket.create', target={}, summary='Raise a request')
    with pytest.raises(ActionIntegrityError):asyncio.run(offers.save([proposal], ctx(case=False)))
    with pytest.raises(ActionIntegrityError):asyncio.run(offers.save([proposal], ctx(kind='guest')))
    with pytest.raises(ActionIntegrityError):asyncio.run(offers.save([proposal] * 11, ctx()))
    assert asyncio.run(offers.save([], ctx(case=False))) == []


def test_only_the_customer_confirms_or_cancels():
    for call in (offers.confirm, offers.cancel):
        with pytest.raises(PolicyDenied):asyncio.run(call(uuid4(), ctx('accounts')))
        with pytest.raises(PolicyDenied):asyncio.run(call(uuid4(), ctx(kind='guest')))


def test_a_replayed_turn_saves_the_same_group():
    case = uuid4()
    assert offers.group_for(case) == offers.group_for(case) != offers.group_for(uuid4())
