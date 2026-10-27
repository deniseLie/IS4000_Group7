"""Ledger validation for card.dispute: a receipt must repeat the reviewed charges and the customer's recorded card answer."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.schemas.actions import ActionOutcome
from app.schemas.disputes import DisputeTarget

ITEMS=[{'transaction_id':'txn_100116','date':'2026-10-17','merchant':'SUNSET GRILL LA','amount':-86.4,'currency':'SGD'},
    {'transaction_id':'txn_100111','date':'2026-10-17','merchant':'TECHVAULT ELECTRONICS','amount':-2400.0,'currency':'SGD'}]
TARGET={'card_id':'card_10011','transaction_ids':['txn_100111','txn_100116'],'last4':'4821','items':ITEMS}


def outcome(state: str='succeeded',answer: str | None='yes',result: dict | None=None) -> dict:
    now=datetime.now(timezone.utc)
    command_id=uuid4()
    receipt=None if state=='proposed' else {'receipt_id':'rcpt_'+uuid4().hex,'command_id':str(command_id),'customer_id':1001,'outcome':state,
        'result':result if result is not None else {**TARGET,'card_in_possession':answer or 'yes','status':'submitted_for_review'},
        'audit_event_id':7,'created_at':now.isoformat()}
    return {'command':{'command_id':str(command_id),'customer_id':1001,'case_id':str(uuid4()),'type':'card.dispute','target':TARGET,
        'idempotency_key':'k','state':state,'proposed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=10)).isoformat(),
        'confirmed_at':None if state=='proposed' else now.isoformat(),'confirmation_input':None if answer is None else {'card_in_possession':answer}},
        'receipt':receipt}


def test_dispute_targets_are_canonical() -> None:
    assert DisputeTarget.model_validate({'card_id':'card_10011','transaction_ids':['txn_100111','txn_100115']}).transaction_ids==['txn_100111','txn_100115']
    for ids in [['txn_100115','txn_100111'],['txn_100111','txn_100111'],[]]:
        with pytest.raises(ValidationError):DisputeTarget.model_validate({'card_id':'card_10011','transaction_ids':ids})


def test_a_dispute_receipt_repeats_the_reviewed_charges_and_the_card_answer() -> None:
    assert ActionOutcome.model_validate(outcome()).receipt is not None
    assert ActionOutcome.model_validate(outcome('proposed',None)).command.confirmation_input is None
    bad=[outcome(answer=None),outcome('proposed','yes'),outcome(result={**TARGET,'card_in_possession':'no','status':'submitted_for_review'}),
        outcome(result={**TARGET,'items':ITEMS[:1],'transaction_ids':['txn_100115'],'card_in_possession':'yes','status':'submitted_for_review'})]
    for value in bad:
        with pytest.raises(ValidationError):ActionOutcome.model_validate(value)
