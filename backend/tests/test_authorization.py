"""Pure policy boundaries and enforcement through real service entry points; no model substitutes."""
import asyncio
from uuid import uuid4
import pytest
from app.schemas.contracts import AgentName, Principal, RequestContext
from app.services import audit, customer_data
from app.services.authorization import PolicyDenied, decide, require_permission


def customer(agent: AgentName | None) -> RequestContext:
    return RequestContext(session_id=uuid4(),agent=agent,
        principal=Principal(kind='customer',user_id=uuid4(),customer_id=1001,jwt='synthetic-test-token'))


def test_unknown_tools_and_untrusted_identity_are_denied():
    ctx=customer(None)
    assert decide(ctx.principal,'customer.get_everything',None,ctx).reasons==['unknown_tool']
    assert decide(ctx.principal,'kb.search',None,None).reasons==['missing_context']
    other=customer(None)
    assert decide(other.principal,'kb.search',None,ctx).reasons==['principal_mismatch']
    incomplete=ctx.model_copy(update={'principal':ctx.principal.model_copy(update={'jwt':None})})
    assert decide(incomplete.principal,'customer.get_accounts',None,incomplete).reasons==['customer_session_required']
    for changes in [{'jwt':' '},{'customer_id':0},{'customer_id':-1}]:
        invalid=ctx.model_copy(update={'principal':Principal.model_validate({**ctx.principal.model_dump(),'jwt':ctx.principal.jwt,**changes})})
        assert decide(invalid.principal,'customer.get_accounts',None,invalid).reasons==['customer_session_required']
    system=RequestContext(session_id=uuid4(),principal=Principal(kind='system'))
    assert decide(system.principal,'customer.get_accounts',None,system).reasons==['system_access_unconfigured']


def test_guests_only_get_registered_public_tools():
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    for tool in ['kb.search','kb.get_fact']:
        assert decide(ctx.principal,tool,None,ctx).allow
    for tool in ['customer.get_accounts','docs.precheck','handoff.create','consent.check','actions.propose']:
        assert not decide(ctx.principal,tool,None,ctx).allow
    assert not decide(ctx.principal,'kb.delete_all',None,ctx).allow
    assert not decide(ctx.principal,'kb.search',{'customer_id':1001},ctx).allow


def test_unlinked_customer_can_read_public_knowledge_but_not_personal_tools():
    original=customer(None)
    ctx=original.model_copy(update={'principal':original.principal.model_copy(update={'customer_id':None})})
    for tool in ['kb.search','kb.get_fact']:
        assert decide(ctx.principal,tool,None,ctx).allow
        assert not decide(ctx.principal,tool,{'customer_id':1001},ctx).allow
    for tool in ['customer.get_profile','customer.get_accounts','consent.check','actions.propose']:
        assert not decide(ctx.principal,tool,None,ctx).allow


@pytest.mark.parametrize('scope',[1003,True,'1001',0,-1])
def test_resource_scope_is_validated_and_cannot_override_identity(scope):
    ctx=customer(None)
    assert not decide(ctx.principal,'customer.get_accounts',{'customer_id':scope},ctx).allow


@pytest.mark.parametrize('agent,allowed,forbidden',[
    ('retail','customer.get_accounts','compliance.get_case'),
    ('fraud','customer.get_credit_cards','customer.get_loans'),
    ('compliance','compliance.update_checklist_item','customer.get_credit_cards'),
    ('document','docs.precheck','customer.get_profile'),
    ('relationship','rm.share_summary','customer.get_transactions'),
])
def test_agent_boundaries(agent,allowed,forbidden):
    ctx=customer(agent)
    assert decide(ctx.principal,allowed,{'customer_id':1001},ctx).allow
    assert decide(ctx.principal,forbidden,None,ctx).reasons==['agent_tool_forbidden']


def test_obligations_and_staff_restrictions():
    ctx=customer(None)
    assert decide(ctx.principal,'customer.get_credit_cards',None,ctx).obligations==['mask:pan']
    assert decide(ctx.principal,'rm.share_summary',None,ctx).obligations==['require:confirmation','require:consent:rm_brief']
    assert decide(ctx.principal,'consent.grant',None,ctx).obligations==['require:confirmation']
    assert decide(ctx.principal,'appointment.hold',None,ctx).obligations==[]
    for level in ['aal1','aal2']:
        elevated=ctx.model_copy(update={'principal':ctx.principal.model_copy(update={'aal':level})})
        for tool in ['handoff.claim','handoff.close']:
            assert decide(elevated.principal,tool,None,elevated).reasons==['staff_access_required']
        # Card actions are the bank's since 0081: a customer cannot block, dispute, unblock or cancel; staff keep a separate path.
        for tool in ['card.temporary_block','card.dispute','card.unblock','card.cancel_permanently']:
            assert decide(elevated.principal,tool,{'customer_id':1001},elevated).reasons==['bank_only_action']
            assert decide(elevated.principal,tool,{'customer_id':1003},elevated).reasons==['customer_scope_mismatch']


@pytest.mark.parametrize('agent',['fraud','retail','relationship','compliance','document'])
def test_no_agent_may_propose_a_card_action(agent: str):
    ctx=customer(agent)
    for tool in ['card.temporary_block','card.dispute','card.unblock','card.cancel_permanently']:
        assert decide(ctx.principal,tool,{'customer_id':1001},ctx).reasons==['bank_only_action']
    guest=Principal(kind='guest')
    assert not decide(guest,'card.unblock',None,RequestContext(session_id=uuid4(),principal=guest)).allow


def test_denial_is_audited_before_customer_database_access():
    ctx=customer('document')
    batch=audit.start_batch(session_id=ctx.session_id)
    try:
        # This must raise before constructing a Supabase client or attempting a network read.
        with pytest.raises(PolicyDenied,match='agent_tool_forbidden'):
            asyncio.run(customer_data.get_accounts(ctx))
        event=batch.events[0]
        assert event.action=='policy.decided' and event.outcome=='denied' and event.actor_id=='document'
        assert event.details['tool']=='customer.get_accounts'
        assert audit.is_safe(event.details)
    finally:audit.current_batch.set(None)


def test_policy_audit_never_copies_unregistered_tool_arguments():
    ctx=customer(None)
    batch=audit.start_batch(session_id=ctx.session_id)
    before=ctx.model_dump()
    try:
        with pytest.raises(PolicyDenied):require_permission(ctx.principal,'UNTRUSTED PRIVATE TEXT',None,ctx)
        assert 'UNTRUSTED' not in batch.events[0].model_dump_json()
        assert ctx.model_dump()==before
        decision=require_permission(ctx.principal,'kb.search',None,ctx)
        assert decision.allow and batch.events[1].outcome=='success'
    finally:audit.current_batch.set(None)
