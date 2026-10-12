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

def test_llm_route_records_spans_and_validation_reason(monkeypatch,saved):
    async def model(*a):return '{"answer":"I have blocked your card","source_ids":["cards"]}'
    monkeypatch.setattr(answers.llm,'answer',model)
    client.post('/chat',json={'messages':[{'role':'user','content':'credit card fees'}]})
    t=saved[0]
    assert names(t)==['retrieve','route','validate']
    assert t.attrs['route']=='llm' and t.attrs['reject_reason']=='action_claim' and t.attrs['needs_human'] is True
    validate=[s for s in t.spans if s['name']=='validate'][0]
    assert validate['attrs']=={'passed':False,'reason':'action_claim'}

def test_provider_failure_is_an_error_trace(monkeypatch,saved):
    async def model(*a):raise RuntimeError('boom')
    monkeypatch.setattr(answers.llm,'answer',model)
    client.post('/chat',json={'messages':[{'role':'user','content':'credit card fees'}]})
    t=saved[0]
    assert t.attrs['outcome']=='error' and t.attrs['error_type']=='RuntimeError'

def test_payload_has_no_user_text_and_sums_llm_tokens(monkeypatch,saved):
    async def model(*a):return '{"answer":"ok","source_ids":["cards"]}'
    monkeypatch.setattr(answers.llm,'answer',model)
    client.post('/chat',json={'messages':[{'role':'user','content':SECRET_TEXT+' credit card fees'}]})
    t=saved[0]
    t.spans.append({'seq':9,'name':'llm','kind':'llm','started_at':'2026-01-01T00:00:00Z','ms':5,'status':'ok','attrs':{'input_tokens':10,'output_tokens':4,'cost_usd':0.001}})
    p=trace_store.build_payload(t)
    assert SECRET_TEXT not in json.dumps(p)
    assert p['input_tokens']==10 and p['output_tokens']==4 and p['cost_usd']==0.001 and p['trace_id']==t.id

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
