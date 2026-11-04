"""CR12: natural-language fraud reports must route urgently, without falsely reporting an action."""
import pytest
from app.schemas.chat import ChatRequest
from app.services.guardrail import assess_request
from app.schemas.contracts import PlanTask
from app.services.orchestrator import critical_task, overridden

@pytest.mark.parametrize('message',[
    'I think someone used my card without permission.',
    'Someone used my credit card.',
    'My card was used by somebody else.',
    'Somebody has been using my card.',
    'I think my card has been compromised.',
    'Someone made purchases with my card.',
    "I wasn't even there for this charge.",
    'There is an unauthorised payment on my card.',
])
def test_informal_fraud_is_urgent_and_routes_to_fraud(message):
    request=ChatRequest(mode='personal',messages=[{'role':'user','content':message}])
    risk=assess_request(request)
    assert 'fraud_suspected' in risk.risk_flags
    assert risk.priority=='P0'
    assert risk.fixed_response is None
    assert critical_task(message,'English').agent=='security' and critical_task(message,'English').intent=='unrecognised_charge'
    routed=overridden([PlanTask(id='t1',agent='accounts',intent='transactions',question=message)],risk)[0]
    assert (routed.agent,routed.intent)==('security','unrecognised_charge')

@pytest.mark.parametrize('message',[
    'I need more spending power for my overseas trip.',
    'What is card fraud?',
    'My travel card is useful overseas.',
    'How much can I spend with my card?',
    'My card was used by me yesterday.',
    'How can I report a card charge?',
])
def test_non_reports_are_not_automatically_urgent(message):
    risk=assess_request(ChatRequest(mode='public',messages=[{'role':'user','content':message}]))
    assert 'fraud_suspected' not in risk.risk_flags
