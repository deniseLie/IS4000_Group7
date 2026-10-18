"""Audit batch client (services/audit.py) and its wiring in POST /chat. The database side (migrations 0005/0006) is
checked against Postgres; here the write itself is replaced so no real project is touched."""
import asyncio
import json
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.schemas.contracts import AuditEvent
from app.services import audit

client=TestClient(app)

@pytest.fixture
def store(monkeypatch):
    """Enable the store and capture each batch; set fail=True to make the write fail."""
    monkeypatch.setenv('AUDIT_STORE','1'); monkeypatch.setenv('SUPABASE_URL','https://demo.invalid'); monkeypatch.setenv('SUPABASE_ANON_KEY','anon')
    state={'batches':[],'fail':False}
    def write(db,payload):
        if state['fail']:raise ConnectionError('down')
        state['batches'].append(payload)
    monkeypatch.setattr(audit,'_write',write)
    return state

def events(response):
    return [b.split('\n')[0][7:] for b in response.text.replace('\r\n','\n').split('\n\n') if b.startswith('event: ')]

@pytest.mark.parametrize('v',[{'count':3,'flag':True,'none':None},{'route':'lost_card','source_ids':['cards','customer-records']},
    {'cite':'card-dispute@v3#steps','model':'openai/gpt-4o-mini:free'},{'new':{'status':'temporarily_blocked'},'changed':['status','last4']}])
def test_safe_details_pass(v):assert audit.is_safe(v)

@pytest.mark.parametrize('v',[{'text':'my card was stolen'},{'pan':'4111111111111111'},{'email':'demo.alex@example.com'},{'Bad':1},
    {'a':{'b':{'c':1}}},{'x':list(range(21))},{'x':'y'*81},{'x':object()}])
def test_free_text_and_records_rejected(v):assert not audit.is_safe(v)

def test_event_contract_checks_category_and_actor():
    AuditEvent(category='agent',action='agent.llm_call',actor_type='agent',actor_id='fraud_security')
    for bad in ({'category':'chat','action':'action.executed'},{'category':'chat','action':'chat.x','actor_type':'agent'},
                {'category':'chat','action':'Chat sent'},{'category':'chat','action':'chat.x','resource_id':'has space'}):
        with pytest.raises(ValidationError):AuditEvent(**bad)

def test_add_rejects_unsafe_details():
    with pytest.raises(ValueError):audit.AuditBatch().add('chat','chat.message_received',details={'question':'what is my balance'})

def test_disabled_store_writes_nothing(monkeypatch):
    monkeypatch.delenv('AUDIT_STORE',raising=False)
    monkeypatch.setattr(audit,'_write',lambda *a:pytest.fail('must not write'))
    b=audit.AuditBatch(); b.add('chat','chat.message_received')
    asyncio.run(b.flush())
    assert b.events==[]

def test_failed_write_raises(store):
    store['fail']=True
    b=audit.AuditBatch(); b.add('chat','chat.message_received')
    with pytest.raises(audit.AuditWriteError):asyncio.run(b.flush())

def test_chat_writes_one_batch_before_the_answer(store,monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    question='I lost my card, please block it'
    r=client.post('/chat',json={'messages':[{'role':'user','content':question}]})
    assert events(r)==['status','answer','done']
    [batch]=store['batches']
    assert [e['action'] for e in batch]==['chat.message_received','chat.fixed_response']
    assert batch[1]['details']['route']=='lost_card' and batch[1]['details']['source_ids']==['lost-card']
    assert UUID(batch[0]['request_id'])==UUID(r.headers['x-request-id'])
    assert 'lost my card' not in json.dumps(batch)

def test_failed_audit_write_withholds_the_answer(store,monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    store['fail']=True
    r=client.post('/chat',json={'messages':[{'role':'user','content':'I lost my card'}]})
    assert events(r)==['status','error']
