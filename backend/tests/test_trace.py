import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api import chat as api
from app.services import answers, trace_store

client=TestClient(app)
SECRET_TEXT='my secret question text'

@pytest.fixture
def saved(monkeypatch):
    out=[]
    monkeypatch.setattr(api,'save_trace',out.append)
    return out

def names(trace):return [s['name'] for s in sorted(trace.spans,key=lambda s:s['seq'])]

def test_fixed_route_records_route_and_no_llm_span(saved):
    client.post('/chat',json={'messages':[{'role':'user','content':'I lost my card'}]})
    t=saved[0]
    assert t.attrs['route']=='lost_card' and t.attrs['outcome']=='ok' and t.attrs['mode']=='public' and t.attrs['authenticated'] is False
    assert names(t)==['route']

def test_validation_records_reason_without_simulating_model():
    from app.core.observability import start_trace, current_trace
    trace=start_trace()
    try:
        answers.validate('{"answer":"I have blocked your card","source_ids":["cards"]}',[{'id':'cards','text':'Guidance'}])
        assert names(trace)==['validate']
        assert trace.spans[0]['attrs']=={'passed':False,'reason':'action_claim'}
    finally:current_trace.set(None)

def test_error_span_and_usage_payload_do_not_include_text():
    from app.core.observability import start_trace, span, current_trace
    trace=start_trace()
    try:
        with pytest.raises(RuntimeError):
            with span('llm','llm'):raise RuntimeError(SECRET_TEXT)
        assert trace.spans[0]['error_type']=='RuntimeError'
        trace.spans.append({'seq':1,'name':'llm','kind':'llm','started_at':'2026-10-13T00:00:00Z','ms':5,'status':'ok','attrs':{'input_tokens':10,'output_tokens':4,'cost_usd':0.001}})
        payload=trace_store.build_payload(trace)
        assert SECRET_TEXT not in json.dumps(payload)
        assert payload['input_tokens']==10 and payload['output_tokens']==4
    finally:current_trace.set(None)

def test_failed_write_never_breaks_saving(monkeypatch):
    monkeypatch.setenv('TRACE_STORE','1');monkeypatch.setenv('SUPABASE_URL','http://x');monkeypatch.setenv('SUPABASE_ANON_KEY','k')
    def boom(payload):raise RuntimeError('db down')
    monkeypatch.setattr(trace_store,'_write',boom)
    from app.core.observability import Trace
    async def run():
        trace_store.save_trace(Trace())
        await asyncio.gather(*trace_store._pending)
    asyncio.run(run())

def test_disabled_by_default_makes_no_task(monkeypatch):
    monkeypatch.delenv('TRACE_STORE',raising=False)
    assert not trace_store.enabled()
