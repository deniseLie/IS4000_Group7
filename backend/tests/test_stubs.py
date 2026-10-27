import inspect
import pytest
from uuid import uuid4
from app.schemas.contracts import Decision,Principal,RequestContext
from app.services import actions, customer_data, kb, policy
SIGNATURES={  # the agreed names and parameters; change only together with the callers
    kb.search:['query','ctx','db'], kb.get_fact:['fact_id','ctx','db'],
    customer_data.get_profile:['ctx'], customer_data.get_accounts:['ctx'], customer_data.get_transactions:['ctx','from_','to','filters'],
    customer_data.get_credit_cards:['ctx'], customer_data.get_loans:['ctx'],
    customer_data.get_requests:['ctx'], customer_data.get_free_slots:['query','ctx'],
    actions.run_action:['command','ctx','confirmed','confirmation_input'], policy.decide:['subject','action','resource','context'],
    policy.has_consent:['query','ctx','db'],
}
def ctx(kind='customer',aal='aal1'):return RequestContext(session_id=uuid4(),principal=Principal(kind=kind,user_id=uuid4() if kind=='customer' else None,customer_id=1001 if kind=='customer' else None,jwt='synthetic-test-token' if kind=='customer' else None,aal=aal))

@pytest.mark.parametrize('fn,params',SIGNATURES.items(),ids=lambda x:getattr(x,'__qualname__',''))
def test_stub_signatures(fn,params):assert list(inspect.signature(fn).parameters)==params

def test_decide_rules():
    c=ctx()
    assert isinstance(policy.decide(c.principal,'kb.search',None,c),Decision)
    assert 'require:confirmation' in policy.decide(c.principal,'card.temporary_block',None,c).obligations
    assert 'require:confirmation' in policy.decide(c.principal,'card.dispute',None,c).obligations
    assert policy.decide(c.principal,'handoff.create',None,c).obligations==[]
    for level in ['aal1','aal2']:
        customer=ctx(aal=level)
        assert not policy.decide(customer.principal,'handoff.claim',None,customer).allow
        assert 'require:confirmation' in policy.decide(customer.principal,'card.unblock',None,customer).obligations
