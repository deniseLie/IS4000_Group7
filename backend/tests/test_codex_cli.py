"""Transport contract checks, not a fake AI service or generated answers."""
import json
from pathlib import Path
import pytest
from app.services.codex_cli import cli_environment, command, parse_events

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
