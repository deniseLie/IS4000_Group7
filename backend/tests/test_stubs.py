import asyncio
import inspect
import pathlib
import pytest
from uuid import uuid4
from app.schemas.contracts import ActionCommand, Decision, Fact, KbChunk, Principal, ProposedCommand, Receipt, RequestContext, ToolResult
from app.services import actions, customer_data, kb, policy
from app.services.actions import cards, handoffs

STUBS=[actions,cards,handoffs]  # customer_data, knowledge and policy/consent are real services
SIGNATURES={  # the agreed names and parameters; change only together with the callers
    kb.search:['query','ctx','db'], kb.get_fact:['fact_id','ctx','db'],
    customer_data.get_profile:['ctx'], customer_data.get_accounts:['ctx'], customer_data.get_transactions:['ctx','from_','to','filters'],
    customer_data.get_credit_cards:['ctx'], customer_data.get_loans:['ctx'],
    customer_data.get_requests:['ctx'], customer_data.get_free_slots:['query','ctx'],
    actions.run_action:['command','ctx','confirmed'], policy.decide:['subject','action','resource','context'],
    policy.has_consent:['query','ctx','db'],
}
def ctx(kind='customer',aal='aal1'):return RequestContext(session_id=uuid4(),principal=Principal(kind=kind,user_id=uuid4() if kind=='customer' else None,customer_id=1001 if kind=='customer' else None,jwt='synthetic-test-token' if kind=='customer' else None,aal=aal))
def cmd(type_,**target):return ActionCommand(idempotency_key=f'case:{uuid4().hex}:{type_}',type=type_,target=target)

@pytest.mark.parametrize('fn,params',SIGNATURES.items(),ids=lambda x:getattr(x,'__qualname__',''))
def test_stub_signatures(fn,params):assert list(inspect.signature(fn).parameters)==params

def test_every_stub_is_marked():assert all(m.STUB for m in STUBS)

def test_live_pipeline_never_imports_stubs():
    app=pathlib.Path(__file__).parents[1]/'app'
    for live in ['services/answers.py','api/chat.py','main.py']:
        text=(app/live).read_text(encoding='utf-8')
        assert not any(f'services.{m}' in text or f'services import {m}' in text for m in ['kb','customer_data','policy','actions'])

def test_decide_rules():
    c=ctx()
    assert isinstance(policy.decide(c.principal,'kb.search',None,c),Decision)
    assert 'require:confirmation' in policy.decide(c.principal,'card.temporary_block',None,c).obligations
    assert policy.decide(c.principal,'handoff.create',None,c).obligations==[]
    for level in ['aal1','aal2']:
        customer=ctx(aal=level)
        assert not policy.decide(customer.principal,'card.unblock',None,customer).allow

def test_run_action_proposes_then_confirms_once():
    c,command=ctx(),cmd('card.temporary_block',card_id='card_1')
    proposed=asyncio.run(actions.run_action(command,c))
    assert isinstance(proposed,ProposedCommand) and proposed.command_id==command.command_id
    receipt=asyncio.run(actions.run_action(command,c,confirmed=True))
    assert isinstance(receipt,Receipt) and receipt.outcome=='succeeded'
    assert asyncio.run(actions.run_action(command,c,confirmed=True))==receipt  # duplicate confirm returns the same receipt

def test_run_action_auto_type_and_guest():
    assert isinstance(asyncio.run(actions.run_action(cmd('handoff.create',priority='P0'),ctx())),Receipt)
    with pytest.raises(PermissionError):asyncio.run(actions.run_action(cmd('card.temporary_block'),ctx('guest')))
