"""Direct validator cases; these sample drafts do not replace the real AI client."""
from datetime import datetime,timezone
import asyncio
import os
import pytest
from fastapi.testclient import TestClient
from app.schemas.contracts import Task
from app.schemas.fraud import FraudProposal,SecurityRecords
from app.services.agents import fraud
from app.services.agents.fraud import decision_for,evidence_for
from app.services.agents.fraud_records import eligible_proposals
from app.services.llm_gateway import LLMBadOutput
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


@pytest.mark.skipif(os.getenv('TEST_FRAUD_LIVE')!='1',reason='Opt in to real personal-fraud provider acceptance')
def test_real_fraud_provider_returns_owned_grounded_proposal(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    task=Task(task_id='live1',domain='fraud',intent='answer_question',priority='P0',entities={
        'question':'This txn_100111 is an unfamiliar charge on my card ending 4821. Please propose a temporary block for review.'})
    decision,evidence=asyncio.run(fraud.respond(task,[],'English',context(local_data_api,1001).model_copy(update={'agent':'fraud'}),[]))
    assert decision.result.status=='needs_human',decision.result.model_dump_json()
    assert decision.proposal==FraudProposal(card_id='card_10011',transaction_id='txn_100111')
    assert {item.source_id for item in decision.result.citations}=={'customer-records'}
    assert decision.prompt_id.startswith('v3+') and len(evidence)==1
