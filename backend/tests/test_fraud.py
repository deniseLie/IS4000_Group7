"""Direct validator cases; these sample drafts do not replace the real AI client."""
from datetime import datetime,timezone
import asyncio
import os
import pytest
from fastapi.testclient import TestClient
from app.schemas.contracts import Task
from app.schemas.fraud import DisputeProposal,FraudProposal,SecurityRecords
from app.services.agents import fraud
from app.services.agents.fraud import decision_for,evidence_for,travel_evidence
from app.services.agents.fraud_records import dispute_for,eligible_proposals,travel_flags
from app.services.llm_gateway import LLMBadOutput
from app.services.source_checks import check_sources
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context


def records() -> SecurityRecords:
    return SecurityRecords.model_validate({'captured_at':datetime.now(timezone.utc),'cards':[
        {'card_id':'card_10011','last4':'4821','status':'active'},{'card_id':'card_10012','last4':'9876','status':'active'}],
        'transactions':[
            {'txn_id':'txn_100111','card_id':'card_10011','card_last4':'4821','date':'2026-10-17','description':'Synthetic merchant',
                'amount':-2400.0,'currency':'SGD','status':'posted'},
            {'txn_id':'txn_100112','card_id':'card_10012','card_last4':'9876','date':'2026-10-17','description':'Another merchant',
                'amount':-64.20,'currency':'USD','status':'posted'}]})


def test_targets_require_one_unambiguous_owned_active_card() -> None:
    value=records()
    assert eligible_proposals('Unfamiliar charge',value)==[]
    assert eligible_proposals('Unfamiliar SGD 2400 charge',value)==[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    assert eligible_proposals('Unfamiliar USD 64.20 charge',value)==[FraudProposal(card_id='card_10012',transaction_id='txn_100112')]
    assert eligible_proposals('Unfamiliar SGD 64.20 charge',value)==[]
    assert eligible_proposals('Unfamiliar txn_999999 charge',value)==[]
    assert eligible_proposals('Unfamiliar charge on card ending 5555',value)==[]
    assert eligible_proposals('Unfamiliar charge on card ending 4821',value)==[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    blocked=value.model_copy(update={'cards':[item.model_copy(update={'status':'temporarily_blocked'}) for item in value.cards]})
    assert eligible_proposals('Unfamiliar txn_100111 charge',blocked)==[]


def test_invalid_grounding_or_foreign_target_cannot_propose() -> None:
    import json
    task=Task(task_id='t1',domain='fraud',intent='answer_question')
    evidence=[evidence_for(records())]
    pairs=[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    draft={'answer':'The synthetic transaction is SGD -2,400.00.','source_ids':['customer-records'],'answer_kind':'banking',
        'status':'needs_human','clarification_question':'','proposals':[pairs[0].model_dump()]}
    assert decision_for(task,json.dumps(draft),evidence,pairs,'test').proposal==pairs[0]
    invalid={**draft,'answer':'The synthetic transaction is USD -2,400.00.'}
    assert decision_for(task,json.dumps(invalid),evidence,pairs,'test').proposal is None
    foreign={**draft,'proposals':[{'card_id':'card_10031','transaction_id':'txn_100311'}]}
    with pytest.raises(LLMBadOutput):decision_for(task,json.dumps(foreign),evidence,pairs,'test')


def alex_records() -> SecurityRecords:
    """Alex's card history after migration 0070 (times in SGT), plus edge rows the travel rule must leave out."""
    rows=[('txn_100115','card_10011','2026-10-17 19:15','Hawker Delight, Tanjong Pagar',-12.5,'SGD','posted','SG'),
        ('txn_100116','card_10011','2026-10-17 21:42','SUNSET GRILL LA',-86.4,'SGD','posted','US'),
        ('txn_100111','card_10011','2026-10-17 21:58','TECHVAULT ELECTRONICS',-2400.0,'SGD','posted','US'),
        ('txn_100113','card_10011','2026-10-15 18:30','Demo Supermarket',-64.2,'SGD','posted','SG'),
        # Left out: a refund, a failed charge, another card, a charge with no recorded country, and a country change 4 hours or more later.
        ('txn_100117','card_10011','2026-10-17 21:50','Refund (DEMO)',20.0,'SGD','posted','US'),
        ('txn_100118','card_10011','2026-10-17 21:51','Declined (DEMO)',-5.0,'SGD','failed','JP'),
        ('txn_100119','card_10012','2026-10-17 20:00','Other card shop',-9.0,'SGD','posted','MY'),
        ('txn_100120','card_10011','2026-10-17 21:55','No country (DEMO)',-1.0,'SGD','posted',None),
        ('txn_100121','card_10011','2026-10-15 22:30','Night market (DEMO)',-3.0,'SGD','posted','MY')]
    def at(value: str) -> str:return value.replace(' ','T')+':00+08:00'
    return SecurityRecords.model_validate({'captured_at':datetime.now(timezone.utc),'cards':[
        {'card_id':'card_10011','last4':'4821','status':'active'},{'card_id':'card_10012','last4':'9876','status':'active'}],
        'transactions':[{'txn_id':txn,'card_id':card,'card_last4':'4821' if card=='card_10011' else '9876','date':when[:10],'txn_at':at(when),
            'description':name,'amount':amount,'currency':currency,'status':status,'merchant_country':country}
            for txn,card,when,name,amount,currency,status,country in rows]})


def test_travel_rule_flags_later_charges_in_another_country_too_soon_after_one_on_the_same_card() -> None:
    value=alex_records()
    flags=travel_flags(value)
    assert [(flag.anchor.txn_id,flag.charge.txn_id,flag.gap.total_seconds()/60) for flag in flags]==[
        ('txn_100115','txn_100116',147.0),('txn_100115','txn_100111',163.0)]
    # The reported charge brings every charge flagged with it into the dispute, including the one the customer did not name.
    assert dispute_for(value,FraudProposal(card_id='card_10011',transaction_id='txn_100111'))==DisputeProposal(card_id='card_10011',
        transaction_ids=['txn_100111','txn_100116'])
    # An unflagged report (the customer's own Singapore charge) disputes only itself.
    assert dispute_for(value,FraudProposal(card_id='card_10011',transaction_id='txn_100115'))==DisputeProposal(card_id='card_10011',
        transaction_ids=['txn_100115'])
    later=value.model_copy(update={'transactions':[item.model_copy(update={'txn_at':item.txn_at.replace(hour=23,minute=15)})
        if item.txn_id=='txn_100116' else item for item in value.transactions if item.txn_id!='txn_100111']})
    assert travel_flags(later)==[]
    with pytest.raises(ValueError):DisputeProposal(card_id='card_10011',transaction_ids=['txn_100116','txn_100111'])


def test_travel_evidence_supports_the_flagged_times_and_figures_but_not_new_ones() -> None:
    value=alex_records()
    travel=travel_evidence(value,'txn_100111')
    assert travel is not None and travel.id=='travel-check' and 'not a fraud decision' in travel.text and 'txn_' not in travel.text
    assert 'Flagged with the reported charge as well: SUNSET GRILL LA.' in travel.text and 'cannot tell which place the cardholder was in' in travel.text
    assert travel_evidence(value,'txn_100113') is None and travel_evidence(value,'txn_100115') is None
    evidence=[{'id':item.id,'text':item.text} for item in (evidence_for(value),travel)]
    answer=('Your card ending 4821 was used at Hawker Delight, Tanjong Pagar in Singapore at 7:15 pm on 17 Oct, then in the United States '
        'at SUNSET GRILL LA ($86.40, 9:42 pm) and TECHVAULT ELECTRONICS ($2,400.00, 21:58), about 2 hours later. That is too soon to travel, '
        'so a DemoBank rule check flagged both US charges; you were in Singapore, so they do not fit. It is not proof of fraud.')
    assert check_sources(answer,['travel-check'],evidence) is None
    assert check_sources(answer.replace('$86.40','$68.40'),['travel-check'],evidence)=='unsupported_figure'
    assert check_sources(answer.replace('9:42 pm','9:52 pm'),['travel-check'],evidence)=='unsupported_figure'
    assert check_sources(answer+' Together they total $2,486.40.',['travel-check'],evidence)=='unsupported_figure'


@pytest.mark.skipif(os.getenv('TEST_FRAUD_LIVE')!='1',reason='Opt in to real personal-fraud provider acceptance')
def test_real_fraud_provider_returns_owned_grounded_proposal(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    task=Task(task_id='live1',domain='fraud',intent='answer_question',priority='P0',entities={
        'question':'This txn_100111 is an unfamiliar charge on my card ending 4821. Please propose a temporary block for review.'})
    decision,evidence=asyncio.run(fraud.respond(task,[],'English',context(local_data_api,1001).model_copy(update={'agent':'fraud'}),[]))
    assert decision.result.status=='needs_human',decision.result.model_dump_json()
    assert decision.proposal==FraudProposal(card_id='card_10011',transaction_id='txn_100111')
    assert {item.source_id for item in decision.result.citations}<={'customer-records','travel-check','unrecognised-charge'}
    assert decision.prompt_id.startswith('v5+') and [item.id for item in evidence]==['customer-records','travel-check']
    assert decision.dispute==DisputeProposal(card_id='card_10011',transaction_ids=['txn_100111','txn_100116'])
