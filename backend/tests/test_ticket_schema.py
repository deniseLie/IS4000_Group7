"""Ticket action contracts without a database: validation, redaction, registry and policy."""
from uuid import uuid4
import pytest
from pydantic import SecretStr,ValidationError
from app.schemas.actions import CreateAction
from app.schemas.contracts import ActionCommand,Principal,RequestContext
from app.schemas.tickets import TicketTarget
from app.services.actions.registry import EXECUTORS
from app.services.authorization import decide

TARGET={'category':'card_limit_change','summary':'Higher limit for a trip.','urgency':'normal',
    'details':{'card':'7310','new_limit':'30000','duration':'temporary','reason':'Travel'}}


def test_ticket_create_is_a_registered_customer_action_with_a_typed_target() -> None:
    action=CreateAction.model_validate({'command_id':str(uuid4()),'case_id':str(uuid4()),'task_id':'ticket','type':'ticket.create','target':TARGET})
    assert isinstance(action.target,TicketTarget) and action.target.details['card']=='7310'


@pytest.mark.parametrize('change',[{'urgency':'urgent'},{'category':'Card Limit'},{'summary':' '},{'details':{'Bad Key':'x'}},
    {'details':{f'k_{i}':'x' for i in range(11)}},{'summary':'token sk-or-v1-abcdefghijklmnop'}])
def test_bad_ticket_targets_are_refused(change: dict) -> None:
    with pytest.raises(ValidationError):TicketTarget.model_validate({**TARGET,**change})


def test_executor_redacts_the_customers_own_words_before_sql_sees_them() -> None:
    command=ActionCommand(command_id=uuid4(),type='ticket.create',idempotency_key='a:b:ticket.create',
        target={**TARGET,'summary':'My card 4111 1111 1111 1111 was declined','details':{**TARGET['details'],'reason':'OTP is 123456'}})
    target=EXECUTORS['ticket.create'](command)
    assert '4111 1111' not in target.summary and '123456' not in target.details['reason']


def test_ticket_policy_needs_confirmation_and_categories_are_public() -> None:
    guest=RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    assert decide(guest.principal,'tickets.categories',None,guest).allow
    assert not decide(guest.principal,'tickets.list',None,guest).allow
    assert not decide(guest.principal,'ticket.create',None,guest).allow
    customer=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt=SecretStr('x')))
    decision=decide(customer.principal,'ticket.create',{'customer_id':1002},customer)
    assert decision.allow and 'require:confirmation' in decision.obligations
    agent=customer.model_copy(update={'agent':'retail'})
    assert not decide(agent.principal,'ticket.create',{'customer_id':1002},agent).allow   # agents never raise tickets themselves
