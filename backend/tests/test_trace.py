"""Request tracing goes to LangSmith. These tests record what the redacting client would upload; nothing leaves the machine."""
import json
import langsmith
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core import langsmith_client
from app.core.observability import current_trace, request_facts, span, span_sink, start_trace
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
    with pytest.raises(RuntimeError),langsmith.trace('chat','chain'):
        with span('documents.inspect','tool'):raise RuntimeError('broken')
    [root],[run]=runs.named('chat'),runs.named('documents.inspect')
    end=runs.end(run)
    assert run['parent_run_id']==root['id'] and run['run_type']=='tool'
    assert 'RuntimeError' in end['error'] and end['extra']['metadata']['error_type']=='RuntimeError'

def test_a_read_outside_a_turn_is_audited_not_traced(runs):
    # A page load's read (My requests, RM Today) would be a trace of its own; the audit log records it instead.
    sink=[];token=span_sink.set(sink)
    try:
        with span('tickets.list','tool',tool='tickets.list'):pass
        with span('embed','embedding',model='m',count=1):pass
    finally:span_sink.reset(token)
    assert runs.created==[] and [n for n,_ in sink]==['tickets.list','embed']  # the eval sink still sees both
    with span('talking_points','llm',agent='talking_points'):pass  # a model call after the reply keeps its own trace
    with langsmith.trace('chat','chain'):
        with span('tickets.list','tool',tool='tickets.list'):pass
    [root],[read],[model]=runs.named('chat'),runs.named('tickets.list'),runs.named('talking_points')
    assert read['parent_run_id']==root['id'] and model.get('parent_run_id') is None

def test_an_agent_run_is_named_after_its_agent(runs):
    sink=[];token=span_sink.set(sink)
    try:
        with langsmith.trace('chat','chain'):
            with span('agent','agent',agent='accounts',intent='open'):pass
            with span('route','agent',route='llm'):pass
    finally:span_sink.reset(token)
    [run]=runs.named('accounts')
    assert runs.end(run)['extra']['metadata']['step_name']=='agent' and not runs.named('agent') and runs.named('route')
    assert [n for n,_ in sink]==['agent','route']  # evaluation checks still see the step name

def test_nothing_is_sent_while_tracing_is_off():
    recorded=Runs()
    with langsmith.tracing_context(enabled=False,client=recorded.client):
        with span('route','agent',route='llm'):pass
    assert recorded.created==recorded.updated==[]

def test_trace_keeps_only_budget_audit_and_request_facts():
    trace=start_trace()
    try:assert vars(trace)=={'attrs':{},'llm_calls':[],'agents':[],'tokens':0,'tools':[]}
    finally:current_trace.set(None)


def test_agent_runs_and_model_calls_become_the_request_summary():
    """The root run's summary (admin dashboard): agent names with their worst status, the plan route and model-call counts."""
    trace=start_trace()
    try:
        with langsmith.tracing_context(enabled=False):
            with span('planner','agent') as a:a.update(route='model',tasks=2)
            with span('agent','agent',agent='guide',intent='faq') as a:a.update(path='reads',status='ok')
            with span('agent','agent',agent='security',intent='login_help') as a:a.update(path='rule',status='needs_human')
            with span('agent','agent',agent='security',intent='unrecognised_charge') as a:a.update(outcome='timeout',status='withheld')
            with span('route','agent',route='llm'):pass
        trace.llm_calls+=[{'ok':True,'agent':'planner','model':'m1','prompt_version':'v7+abc','transport':'api','reasoning':'default','input_tokens':10,'output_tokens':5,'ms':100},
            {'ok':False,'agent':'guide','model':'m1','prompt_version':'v1+def','transport':'api','reasoning':'off','input_tokens':None,'output_tokens':None,'ms':50}]
        facts=request_facts(trace)
    finally:current_trace.set(None)
    assert trace.attrs=={'plan_route':'model','tasks':2}
    assert [a['agent'] for a in trace.agents]==['guide','security','security'] and all(isinstance(a['ms'],int) for a in trace.agents)
    assert facts['agents']==['guide','security','security'] and facts['agent_status']=={'guide':'ok','security':'withheld'}
    assert {k:facts[k] for k in ('model_calls','model_failures','llm_ms','llm_tokens','model','prompt_ids','reasoning_off')}=={'model_calls':2,
        'model_failures':1,'llm_ms':150,'llm_tokens':15,'model':'m1','prompt_ids':['guide/v1+def','planner/v7+abc'],'reasoning_off':1}


def test_the_request_summary_says_who_asked_and_what_the_tools_refused():
    """Build plan 3a: side and role for the admin filters; tool refusals, loop stops and consent gaps as counts."""
    trace=start_trace()
    try:
        trace.attrs['authenticated']=True
        with langsmith.tracing_context(enabled=False):
            with span('tool_call','tool',tool='customer_accounts',agent='accounts') as t:t.update(allowed=True,rows=2)
            with span('tool_call','tool',tool='rm_tickets',agent='accounts') as t:t.update(allowed=False,reason='agent_tool_forbidden')
            with span('agent','agent',agent='accounts',intent='open') as a:a.update(path='loop',status='ok',stop='cap',tool_calls=4,refused=2)
        facts=request_facts(trace)
    finally:current_trace.set(None)
    assert {k:facts[k] for k in ('side','role','tool_calls','loop_stops','refusals','step_refusals','refused_tools','consent_gaps')}=={'side':'client',
        'role':'customer','tool_calls':2,'loop_stops':{'cap':1},'refusals':1,'step_refusals':2,'refused_tools':['rm_tickets:agent_tool_forbidden'],'consent_gaps':0}
    assert trace.tools[1]=={'tool':'rm_tickets','agent':'accounts','allowed':False,'reason':'agent_tool_forbidden'}  # never arguments or rows
