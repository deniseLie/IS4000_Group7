import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.schemas.contracts import ActionCommand, AgentResult, Principal, Receipt, RequestContext, RiskAssessment, TaskPlan
from app.schemas.events import ActionRequiredEvent, AnswerEvent, AnswerSection, StatusEvent

U=str(uuid4())
EXAMPLES={  # the §4 examples in docs/system-architecture.md, with real uuids
    RequestContext:{'request_id':U,'session_id':U,'case_id':None,'principal':{'kind':'customer','user_id':U,'customer_id':U,'auth_session_id':U,'aal':'aal1'},'locale':'en-SG','received_at':'2026-10-18T09:00:00+00:00'},
    RiskAssessment:{'redacted_message':'I lost my card','redactions':[{'type':'card_number','count':1}],'risk_flags':['lost_card'],'priority':'P0','fixed_response':{'code':'lost_card_v1','needs_human':True}},
    TaskPlan:{'plan_id':U,'tasks':[{'task_id':'t1','domain':'fraud','intent':'report_unrecognised_txn','priority':'P0','entities':{'amount':2400},'needs_auth':True,'status':'pending'}]},
    AgentResult:{'task_id':'t1','agent':'fraud','status':'needs_confirmation','answer_kind':'banking','text':'string','citations':[{'source_id':'kb:card-dispute@v3'}],'tool_calls':[{'call_id':U,'tool':'customer.get_transactions'}],'pending_action':{'command_id':U,'type':'card.temporary_block','summary':'Temporarily block card ending 4821'},'receipts':[{'receipt_id':'rcpt_1','command_id':U}]},
    ActionCommand:{'command_id':U,'idempotency_key':'case:t1:card.temporary_block','type':'card.temporary_block','target':{'card_id':U},'state':'proposed','expires_at':'2026-10-18T09:05:00+00:00'},
    Receipt:{'receipt_id':'rcpt_1','command_id':U,'outcome':'succeeded','audit_event_id':1042},
    ActionRequiredEvent:{'command_id':U,'type':'card.temporary_block','summary':'Temporarily block card ending 4821','expires_at':'2026-10-18T09:05:00+00:00'},
}

@pytest.mark.parametrize('model,data',EXAMPLES.items(),ids=lambda x:getattr(x,'__name__',''))
def test_architecture_examples_round_trip(model,data):
    obj=model.model_validate(data)
    assert model.model_validate_json(obj.model_dump_json())==obj

def test_jwt_is_never_serialised_or_shown():
    p=Principal(kind='customer',jwt='header.payload.signature')
    ctx=RequestContext(session_id=uuid4(),principal=p)
    assert p.jwt.get_secret_value()=='header.payload.signature'
    for text in (ctx.model_dump_json(),repr(ctx),str(ctx.model_dump())):assert 'signature' not in text

def test_contracts_reject_unknown_fields_and_values():
    with pytest.raises(ValidationError):RiskAssessment(redacted_message='x',priority='P9')
    with pytest.raises(ValidationError):Receipt(receipt_id='r',command_id=uuid4(),outcome='succeeded',extra=1)

def test_plan_orders_by_priority_and_keeps_message_order_on_ties():
    plan=TaskPlan(tasks=[{'task_id':t,'domain':'retail','intent':'x','priority':p} for t,p in [('t1','P3'),('t2','P0'),('t3','P3'),('t4','P1')]])
    assert [t.task_id for t in plan.by_priority()]==['t2','t4','t1','t3']

def test_v1_answer_validates_as_answer_event():
    r=TestClient(app).post('/chat',json={'messages':[{'role':'user','content':'I lost my card, please block it'}]})
    block=next(b for b in r.text.replace('\r\n','\n').split('\n\n') if b.startswith('event: answer'))
    event=AnswerEvent.model_validate(json.loads(next(l[6:] for l in block.splitlines() if l.startswith('data: '))))
    assert event.response_origin=='safety' and event.sections is None

def test_answer_event_accepts_sections_and_status_stage():
    section=AnswerSection(task_id='t1',domain='fraud',priority='P0',answer='We can offer a temporary block.',response_origin='ai')
    event=AnswerEvent(answer='',disclaimer='d',source_check='basic_checks_passed',response_origin='ai',sections=[section])
    assert event.sections[0].task_id=='t1'
    with pytest.raises(ValidationError):StatusEvent(stage='thinking')
