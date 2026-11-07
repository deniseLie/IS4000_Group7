"""Transport contract checks, not a fake AI service or generated answers."""
import json
import os
from pathlib import Path
import pytest
from app.services.codex_cli import cli_environment, command, parse_events

@pytest.mark.parametrize('value',['0','901','NaN','inf','invalid'])
def test_invalid_cli_deadline_fails_before_an_external_turn(monkeypatch,value):
    from app.services.codex_cli import timeout_s
    monkeypatch.setenv('CODEX_CLI_TIMEOUT_S',value)
    with pytest.raises(RuntimeError,match='CODEX_CLI_TIMEOUT_S'):timeout_s()

@pytest.mark.skipif(os.getenv('TEST_CODEX_LIVE')!='1',reason='Opt in to real concurrent Codex subscription calls')
def test_parallel_requests_wait_for_the_subscription_connector():
    import asyncio
    from app.services.codex_cli import answer_with_model
    schema={'title':'connector_check','type':'object','additionalProperties':False,'required':['acknowledged'],
        'properties':{'acknowledged':{'type':'boolean'}}}
    async def run():
        return await asyncio.gather(*(answer_with_model('Acknowledge the synthetic connector check with JSON.',schema,
            [('user',message)],os.getenv('CODEX_MODEL','gpt-5.6-luna')) for message in ['First check','Second check']))
    results=asyncio.run(run())
    assert len(results)==2
    for answer,usage in results:
        assert answer=={'acknowledged':True} and usage['input_tokens']>0 and usage['output_tokens']>0

def stream(*events):return '\n'.join(json.dumps(e) for e in events)

def test_cli_environment_does_not_forward_application_secrets(monkeypatch):
    for key in ('LLM_API_KEY','SUPABASE_ANON_KEY','OPENAI_API_KEY','CODEX_API_KEY','CODEX_THREAD_ID'):
        monkeypatch.setenv(key,'synthetic-secret')
    assert 'synthetic-secret' not in cli_environment().values()

def test_request_text_is_not_a_command_argument():
    args=command('/bin/codex',Path('/tmp/private-request'),'gpt-5.6-luna')
    assert args[-1]=='-'
    assert '--ignore-user-config' in args and '--ephemeral' in args
    assert 'read-only' in args and 'forced_login_method="chatgpt"' in args
    assert 'features.shell_tool=false' in args and 'features.code_mode_host=false' in args
    assert 'features.apps=false' in args and 'features.plugins=false' in args
    assert 'web_search="disabled"' in args

@pytest.mark.parametrize('kind',['command_execution','mcp_tool_call','file_change','web_search'])
def test_non_answer_events_are_rejected(kind):
    with pytest.raises(RuntimeError,match='non-answer'):
        parse_events(stream({'type':'item.completed','item':{'type':kind}}))

@pytest.mark.parametrize('events',[
    [{'type':'turn.failed','error':{'message':'private error'}}],
    [{'type':'error','message':'private error'}],
    [{'type':'item.completed','item':{'type':'agent_message','text':'{}'}}],
    [{'type':'turn.completed','usage':{}}],
])
def test_failure_or_incomplete_output_is_never_an_answer(events):
    with pytest.raises(RuntimeError) as e:parse_events(stream(*events))
    assert 'private error' not in str(e.value)

def test_parser_only_returns_final_json_and_numeric_usage():
    result,usage=parse_events(stream(
        {'type':'item.completed','item':{'type':'reasoning','text':'do not return this'}},
        {'type':'item.completed','item':{'type':'agent_message','text':'{"answer":"parser sample"}'}},
        {'type':'turn.completed','usage':{'input_tokens':3,'output_tokens':4,'private':'discard'}},
    ))
    assert result=={'answer':'parser sample'} and usage=={'input_tokens':3,'output_tokens':4}

@pytest.mark.parametrize('tokens',[-1,True,'3'])
def test_invalid_usage_cannot_be_reported_as_zero(tokens):
    with pytest.raises(ValueError,match='non-negative integer'):
        parse_events(stream({'type':'item.completed','item':{'type':'agent_message','text':'{}'}},
            {'type':'turn.completed','usage':{'input_tokens':tokens,'output_tokens':1}}))

def test_disabled_execution_host_warning_is_not_an_answer():
    with pytest.raises(RuntimeError,match='did not complete'):
        parse_events(stream({'type':'item.completed','item':{'type':'error','message':'Code Mode is unavailable because code-mode host is disabled.'}}))

def test_missing_cli_is_visible_error_without_api_fallback(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    monkeypatch.setenv('CODEX_CLI_PATH','/not-installed/demobank-codex')
    response=TestClient(app).post('/chat',json={'messages':[{'role':'user','content':'Hello'}]})
    assert 'event: error' in response.text
    assert 'event: answer' not in response.text and 'event: done' not in response.text

def test_unknown_transport_does_not_fall_back(monkeypatch):
    import asyncio
    from app.services.llm import llm
    monkeypatch.setenv('CHAT_TRANSPORT','unknown')
    with pytest.raises(RuntimeError,match='Unknown CHAT_TRANSPORT'):
        asyncio.run(llm.answer([],[],'English'))
