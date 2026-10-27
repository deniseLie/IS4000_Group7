"""Ledger validation for the customer's own card changes next to the staff operations of the same type."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.schemas.actions import ActionOutcome,CardChangeTarget,CreateAction,CustomerCardResult,StaffCardResult,StaffCardTarget,StoredCardChangeTarget
from app.services.actions.registry import EXECUTORS
from app.schemas.contracts import ActionCommand


def outcome(kind: str='card.unblock',state: str='succeeded',target: dict | None=None,result: dict | None=None,confirmed: bool=True) -> dict:
    now=datetime.now(timezone.utc)
    command_id=uuid4()
    target=target or {'card_id':'card_10011','last4':'4821','from_status':'temporarily_blocked'}
    status='active' if kind=='card.unblock' else 'cancelled'
    receipt=None if state=='proposed' else {'receipt_id':'rcpt_'+uuid4().hex,'command_id':str(command_id),'customer_id':1001,'outcome':state,
        'result':result if result is not None else {**target,'status':status,'handoffs_noted':[]},'audit_event_id':9,'created_at':now.isoformat()}
    return {'command':{'command_id':str(command_id),'customer_id':1001,'case_id':str(uuid4()),'type':kind,'target':target,'idempotency_key':'k',
        'state':state,'proposed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=5)).isoformat(),
        'confirmed_at':now.isoformat() if confirmed else None},'receipt':receipt}


def test_customer_and_staff_card_changes_share_a_type_but_not_a_target() -> None:
    customer=ActionOutcome.model_validate(outcome())
    assert isinstance(customer.command.target,StoredCardChangeTarget) and isinstance(customer.receipt.result,CustomerCardResult)
    assert ActionOutcome.model_validate(outcome(state='proposed',confirmed=False)).receipt is None
    staff=ActionOutcome.model_validate(outcome(target={'card_id':'card_10011','last4':'4821','handoff_id':str(uuid4())},result={'card_id':'card_10011','status':'active'}))
    assert isinstance(staff.command.target,StaffCardTarget) and isinstance(staff.receipt.result,StaffCardResult)
    cancel=ActionOutcome.model_validate(outcome('card.cancel_permanently',target={'card_id':'card_10011','last4':'4821','from_status':'active'}))
    assert cancel.receipt.result.status=='cancelled'


@pytest.mark.parametrize('change',[
    {'confirmed':False},
    {'target':{'card_id':'card_10011','last4':'4821','from_status':'active'}},
    {'result':{'card_id':'card_10011','last4':'4821','from_status':'temporarily_blocked','status':'cancelled','handoffs_noted':[]}},
    {'result':{'card_id':'card_10011','last4':'9999','from_status':'temporarily_blocked','status':'active','handoffs_noted':[]}},
    {'result':{'card_id':'card_10011','status':'active'}},
])
def test_a_customer_card_receipt_must_match_the_reviewed_card_and_operation(change: dict) -> None:
    with pytest.raises(ValidationError):ActionOutcome.model_validate(outcome(**change))


def test_a_staff_command_still_needs_an_immediate_terminal_result() -> None:
    with pytest.raises(ValidationError):
        ActionOutcome.model_validate(outcome(state='proposed',target={'card_id':'card_10011','last4':'4821','handoff_id':str(uuid4())},confirmed=False))


def test_the_customer_proposal_names_only_the_card() -> None:
    query=CreateAction.model_validate({'command_id':str(uuid4()),'case_id':str(uuid4()),'task_id':'manage_card','type':'card.unblock',
        'target':{'card_id':'card_10011','handoff_id':str(uuid4()),'from_status':'active'}})
    assert query.target==CardChangeTarget(card_id='card_10011')
    command=ActionCommand(command_id=uuid4(),type='card.cancel_permanently',target={'card_id':'card_10011','status':'active'},idempotency_key='k')
    assert EXECUTORS['card.cancel_permanently'](command).model_dump()=={'card_id':'card_10011'}
    with pytest.raises(ValidationError):CreateAction.model_validate({**query.model_dump(mode='json'),'target':{'card_id':'4821'}})
