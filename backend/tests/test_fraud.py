"""Direct validator cases; these sample drafts do not replace the real AI client."""
from datetime import datetime,timezone
import asyncio
import os
import pytest
from fastapi.testclient import TestClient
from app.schemas.contracts import Task
from app.schemas.fraud import DisputeProposal,FraudProposal,SecurityRecords
from app.services.agents import fraud
from app.services.agents.fraud import decision_for,evidence_for,pattern_evidence
from app.services.agents.fraud_records import dispute_for,eligible_proposals,related_charges
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
    """Alex's card history after migration 0064, plus edge rows the pattern rule must leave out."""
    rows=[('txn_100111','card_10011','2026-10-17','ONLINE MERCHANT 8831',-2400.0,'SGD','posted'),
        ('txn_100113','card_10011','2026-10-15','Demo Supermarket',-64.2,'SGD','posted'),
        ('txn_100115','card_10011','2026-10-12','ONLINE MERCHANT 8831',-1.0,'SGD','posted'),
        ('txn_100116','card_10011','2026-10-14','online merchant  8831',-4.99,'SGD','posted'),
        ('txn_100117','card_10011','2026-08-17','ONLINE MERCHANT 8831',-2.0,'SGD','posted'),
        ('txn_100118','card_10011','2026-10-16','ONLINE MERCHANT 8831',-10.01,'SGD','posted'),
        ('txn_100119','card_10011','2026-10-18','ONLINE MERCHANT 8831',-1.0,'SGD','posted'),
        ('txn_100120','card_10011','2026-10-16','ONLINE MERCHANT 8831',-3.0,'USD','posted'),
        ('txn_100121','card_10011','2026-10-16','ONLINE MERCHANT 8831',-3.0,'SGD','pending'),
        ('txn_100122','card_10011','2026-10-16','ONLINE MERCHANT 8831',3.0,'SGD','posted'),
        ('txn_100123','card_10012','2026-10-16','ONLINE MERCHANT 8831',-3.0,'SGD','posted'),
        ('txn_100124','card_10011','2026-08-18','ONLINE MERCHANT 8831',-10.0,'SGD','posted')]
    return SecurityRecords.model_validate({'captured_at':datetime.now(timezone.utc),'cards':[
        {'card_id':'card_10011','last4':'4821','status':'active'},{'card_id':'card_10012','last4':'9876','status':'active'}],
        'transactions':[{'txn_id':txn,'card_id':card,'card_last4':'4821' if card=='card_10011' else '9876','date':day,'description':name,
            'amount':amount,'currency':currency,'status':status} for txn,card,day,name,amount,currency,status in rows]})


def test_pattern_rule_groups_only_small_earlier_charges_from_the_same_merchant_and_card() -> None:
    value=alex_records()
    # Kept: 60 days earlier at most, up to SGD 10.00, posted debits in the same currency. Merchant case and spacing are ignored.
    assert [item.txn_id for item in related_charges(value,'txn_100111')]==['txn_100124','txn_100115','txn_100116']
    assert related_charges(value,'txn_100113')==[] and related_charges(value,'txn_999999')==[]
    assert dispute_for(value,FraudProposal(card_id='card_10011',transaction_id='txn_100111'))==DisputeProposal(card_id='card_10011',
        transaction_ids=['txn_100111','txn_100115','txn_100116','txn_100124'])
    with pytest.raises(ValueError):DisputeProposal(card_id='card_10011',transaction_ids=['txn_100115','txn_100111'])


def test_pattern_evidence_supports_the_grouped_figures_but_not_new_ones() -> None:
    value=alex_records().model_copy(update={'transactions':[item for item in alex_records().transactions if item.txn_id in {'txn_100111','txn_100113','txn_100115','txn_100116'}]})
    pattern=pattern_evidence(value,'txn_100111')
    assert pattern is not None and pattern.id=='fraud-pattern' and 'not a fraud decision' in pattern.text and 'txn_' not in pattern.text
    assert pattern_evidence(value,'txn_100113') is None
    evidence=[{'id':item.id,'text':item.text} for item in (evidence_for(value),pattern)]
    grouped=('Two smaller charges from ONLINE MERCHANT 8831, SGD -1.00 on 2026-10-12 and SGD -4.99 on 2026-10-14, came before the '
        'SGD -2,400.00 charge on 2026-10-17. A DemoBank rule check says this can match a card-testing pattern; it is not proof of fraud.')
    assert check_sources(grouped,['fraud-pattern'],evidence) is None
    echoed='The $2,400 charge came after two smaller charges of $1.00 and $4.99 from ONLINE MERCHANT 8831, a possible card-testing pattern.'
    assert check_sources(echoed,['fraud-pattern'],evidence) is None
    assert check_sources(echoed.replace('$4.99','$5.99'),['fraud-pattern'],evidence)=='unsupported_figure'
    assert check_sources(grouped.replace('-4.99','-5.99'),['fraud-pattern'],evidence)=='unsupported_figure'
    assert check_sources(grouped+' The total is SGD -2,405.99.',['fraud-pattern'],evidence)=='unsupported_figure'


@pytest.mark.skipif(os.getenv('TEST_FRAUD_LIVE')!='1',reason='Opt in to real personal-fraud provider acceptance')
def test_real_fraud_provider_returns_owned_grounded_proposal(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    task=Task(task_id='live1',domain='fraud',intent='answer_question',priority='P0',entities={
        'question':'This txn_100111 is an unfamiliar charge on my card ending 4821. Please propose a temporary block for review.'})
    decision,evidence=asyncio.run(fraud.respond(task,[],'English',context(local_data_api,1001).model_copy(update={'agent':'fraud'}),[]))
    assert decision.result.status=='needs_human',decision.result.model_dump_json()
    assert decision.proposal==FraudProposal(card_id='card_10011',transaction_id='txn_100111')
    assert {item.source_id for item in decision.result.citations}<={'customer-records','fraud-pattern','unrecognised-charge'}
    assert decision.prompt_id.startswith('v4+') and [item.id for item in evidence]==['customer-records','fraud-pattern']
    assert decision.dispute==DisputeProposal(card_id='card_10011',transaction_ids=['txn_100111','txn_100115','txn_100116'])
