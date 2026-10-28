"""Request tracing goes to LangSmith. These tests record what the redacting client would upload; nothing leaves the machine."""
import json
import langsmith
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core import langsmith_client
from app.core.observability import current_trace, span, start_trace
from app.services import answers

client=TestClient(app)

class Recorder(langsmith.Client):
    """The app's redacting client, stopped at the point where it would send each run."""
    def _create_run(self,run,**kw):self.sink.created.append(run)
    def _update_run(self,run,**kw):self.sink.updated.append(run)

class Runs:
    def __init__(self):
        self.created,self.updated=[],[]
        self.client=Recorder(api_key='test',auto_batch_tracing=False,anonymizer=langsmith_client.scrub)
        self.client.sink=self
    def named(self,name):return [r for r in self.created if r['name']==name]
    def end(self,run):return next(u for u in self.updated if u['id']==run['id'])

@pytest.fixture
def runs():
    recorded=Runs()
    langsmith.configure(enabled=True,client=recorded.client)  # global, so the TestClient's thread traces too
    try:yield recorded
    finally:langsmith.configure(enabled=None,client=None)

def test_fixed_route_is_a_root_chat_run_with_a_route_child(runs):
    client.post('/chat',json={'messages':[{'role':'user','content':'I lost my card'}]})
    [root]=runs.named('chat')
    assert root.get('parent_run_id') is None and root['inputs']['messages'][0]['content']=='I lost my card'
    [route]=runs.named('route')
    assert route['parent_run_id']==root['id'] and route['run_type']=='chain' and not runs.named('llm')
    end=runs.end(root)
    meta=end['extra']['metadata']
    assert meta['route']=='lost_card' and meta['outcome']=='ok' and meta['mode']=='public' and meta['request_id']
    assert end['outputs']['response_origin']=='safety' and end.get('error') is None
    route_meta=runs.end(route)['extra']['metadata']
    assert route_meta['route']=='lost_card' and route_meta['kind']=='agent' and route_meta['risk_flags']==['lost_card']

def test_uploaded_chat_text_is_redacted(runs):
    client.post('/chat',json={'messages':[{'role':'user','content':'My PIN is 4821 and my card 4111 1111 1111 1111'}]})
    [root]=runs.named('chat')
    sent=json.dumps(root['inputs'])+json.dumps(runs.end(root)['outputs'])
    assert '4821' not in sent and '4111' not in sent and '[private detail removed]' in sent

def test_validation_is_a_decision_run_with_its_reason(runs):
    answers.validate('{"answer":"I have blocked your card","source_ids":["cards"]}',[{'id':'cards','text':'Guidance'}])
    [run]=runs.named('validate')
    meta=runs.end(run)['extra']['metadata']
    assert meta['passed'] is False and meta['reason']=='action_claim' and meta['kind']=='decision'

def test_failed_step_ends_its_run_with_the_error(runs):
    with pytest.raises(RuntimeError):
        with span('documents.inspect','tool'):raise RuntimeError('broken')
    [run]=runs.named('documents.inspect')
    end=runs.end(run)
    assert run['run_type']=='tool' and 'RuntimeError' in end['error'] and end['extra']['metadata']['error_type']=='RuntimeError'

def test_nothing_is_sent_while_tracing_is_off():
    recorded=Runs()
    with langsmith.tracing_context(enabled=False,client=recorded.client):
        with span('route','agent',route='llm'):pass
    assert recorded.created==recorded.updated==[]

def test_trace_keeps_only_budget_audit_and_request_facts():
    trace=start_trace()
    try:assert vars(trace)=={'attrs':{},'llm_calls':[],'tokens':0}
    finally:current_trace.set(None)
