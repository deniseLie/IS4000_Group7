"""LLM Gateway checks on schema parsing, budget, retry rules and configuration. No fake model: nothing here returns an answer."""
import asyncio
import pytest
from app.core.config import settings
from app.core.observability import current_trace, start_trace
from app.services import llm_gateway as gw, prompts
from app.services.llm import answer_request

SCHEMA=answer_request([{'id':'cards','text':'Guidance'}],'English')[2]

def test_strict_schema_accepts_only_the_declared_shape():
    gw.check({'answer':'Hi','source_ids':['cards'],'answer_kind':'banking'},SCHEMA)
    for bad in ({'answer':'Hi','source_ids':['cards']},
                {'answer':'Hi','source_ids':['cards'],'answer_kind':'banking','extra':1},
                {'answer':'Hi','source_ids':['other'],'answer_kind':'banking'},
                {'answer':3,'source_ids':[],'answer_kind':'banking'},
                {'answer':'Hi','source_ids':'cards','answer_kind':'banking'}):
        with pytest.raises(gw.LLMBadOutput):gw.check(bad,SCHEMA)
    with pytest.raises(gw.LLMBadOutput):gw.check(True,{'type':'integer'})

def test_bad_output_errors_never_echo_model_text():
    with pytest.raises(gw.LLMBadOutput) as e:gw.check({'answer':'SECRET DRAFT','source_ids':['SECRET ID'],'answer_kind':'banking'},SCHEMA)
    assert 'SECRET' not in str(e.value)

def test_only_transient_failures_are_retried():
    class Status(Exception):
        def __init__(self,code):self.status_code=code
    class APIConnectionError(Exception):pass
    assert gw.transient(TimeoutError()) and gw.transient(APIConnectionError()) and gw.transient(Status(429)) and gw.transient(Status(503))
    assert not any(gw.transient(e) for e in (Status(400),Status(401),ValueError(),gw.LLMBadOutput('x')))

def configure(monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','api')
    for k,v in [('LLM_API_KEY','unused-construction-key'),('LLM_BASE_URL','https://openrouter.ai/api/v1'),('LLM_MODEL','openrouter/free')]:monkeypatch.setenv(k,v)
    monkeypatch.delenv('LLM_PROVIDER',raising=False)

def test_spent_budget_stops_before_any_model_call(monkeypatch):
    configure(monkeypatch)
    trace=start_trace()
    try:
        trace.tokens=settings.llm_request_token_budget
        with pytest.raises(gw.LLMBudgetExceeded):
            asyncio.run(gw.call(prompts.load('answer','v2'),'system',[('user','Hello')],SCHEMA))
        assert trace.llm_calls==[]
        trace.tokens=settings.llm_request_token_budget-100
        assert gw.remaining_budget()==100
    finally:current_trace.set(None)

def test_missing_configuration_is_reported(monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','api');monkeypatch.delenv('LLM_PROVIDER',raising=False);monkeypatch.delenv('LLM_API_KEY',raising=False)
    with pytest.raises(gw.LLMNotConfigured):asyncio.run(gw.call(prompts.load('answer','v2'),'system',[],SCHEMA))
    monkeypatch.setenv('CHAT_TRANSPORT','unknown')
    with pytest.raises(gw.LLMNotConfigured,match='Unknown CHAT_TRANSPORT'):gw.transport()

def test_client_uses_gateway_limits(monkeypatch):
    configure(monkeypatch)
    model=gw.build_model(123)
    assert model.max_tokens==123 and model.max_retries==0 and model.request_timeout==settings.llm_timeout_s
