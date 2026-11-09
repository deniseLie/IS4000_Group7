"""Transport contract checks, not a fake AI service or generated answers. The live check calls the real signed-in CLI."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import pytest
from app.services.claude_cli import cli_environment, command, parse_result

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}

def result(**fields):return json.dumps({'type':'result','subtype':'success','is_error':False,'permission_denials':[],**fields})

def test_cli_environment_does_not_forward_application_secrets(monkeypatch):
    for key in ('LLM_API_KEY','SUPABASE_ANON_KEY','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDECODE','CLAUDE_CODE_SESSION_ID'):
        monkeypatch.setenv(key,'synthetic-secret')
    assert 'synthetic-secret' not in cli_environment().values()

def test_request_text_is_not_a_command_argument_and_tools_are_off():
    args=command('/bin/claude',Path('/tmp/private-request'),'haiku',SCHEMA)
    assert args[args.index('--tools')+1]=='' and '--strict-mcp-config' in args
    assert args[args.index('--setting-sources')+1]=='' and '--no-session-persistence' in args
    assert '--disable-slash-commands' in args and json.loads(args[args.index('--json-schema')+1])==SCHEMA
    assert args[args.index('--system-prompt-file')+1].endswith('instructions.md')

@pytest.mark.parametrize('output',[
    json.dumps({'type':'result','subtype':'error_max_turns','is_error':True,'result':'private error'}),
    result(is_error=True,result='private error'),
    result(result='private error'),
    result(structured_output='private error'),
])
def test_failure_or_incomplete_output_is_never_an_answer(output):
    with pytest.raises(RuntimeError) as e:parse_result(output)
    assert 'private error' not in str(e.value)

def test_a_denied_tool_is_not_an_answer():
    with pytest.raises(RuntimeError,match='non-answer'):
        parse_result(result(structured_output={'answer':'x'},permission_denials=[{'tool_name':'Bash'}]))

def test_parser_only_returns_structured_output_and_numeric_usage():
    data,usage=parse_result(result(result='do not return this',structured_output={'answer':'parser sample'},
        usage={'input_tokens':3,'cache_creation_input_tokens':10,'cache_read_input_tokens':5,'output_tokens':4,'service_tier':'standard'}))
    assert data=={'answer':'parser sample'} and usage=={'input_tokens':18,'output_tokens':4,'answer_chars':27}
    _,usage=parse_result(result(structured_output={'answer':'x'},num_turns=1,duration_api_ms=1800,duration_ms='7000',
        usage={'output_tokens':1292,'output_tokens_details':{'thinking_tokens':930}}))
    assert usage=={'output_tokens':1292,'thinking_tokens':930,'num_turns':1,'api_ms':1800,'answer_chars':15}  # numbers only
    _,usage=parse_result(result(structured_output={'answer':'x'},usage={'output_tokens_details':{'thinking_tokens':'930'}}))
    assert 'thinking_tokens' not in usage  # a count that is not a number is left out

def test_the_turn_timeout_is_a_setting_and_a_slow_cli_is_stopped(monkeypatch):
    from app.services import claude_cli
    monkeypatch.delenv('CLAUDE_CLI_TIMEOUT_S',raising=False)
    assert claude_cli.timeout_s()==90
    for bad in ('0','-5','soon','901'):
        monkeypatch.setenv('CLAUDE_CLI_TIMEOUT_S',bad)
        with pytest.raises(RuntimeError,match='CLAUDE_CLI_TIMEOUT_S'):claude_cli.timeout_s()
    killed=[]
    class Slow:  # stands in for the CLI process only; no answer is ever produced
        returncode=None
        async def communicate(self,payload):await asyncio.sleep(5)
        def kill(self):killed.append(True);self.returncode=-9
        async def wait(self):return self.returncode
    async def start(*args,**kwargs):return Slow()
    monkeypatch.setenv('CLAUDE_CLI_TIMEOUT_S','0.05')
    monkeypatch.setattr(claude_cli.shutil,'which',lambda name:'/bin/claude')
    monkeypatch.setattr(claude_cli.asyncio,'create_subprocess_exec',start)
    with pytest.raises(TimeoutError):asyncio.run(claude_cli._answer('instructions',SCHEMA,[('user','q')],'haiku'))
    assert killed==[True]

def test_missing_cli_is_visible_error_without_api_fallback(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv('CHAT_TRANSPORT','claude')
    monkeypatch.setenv('CLAUDE_CLI_PATH','/not-installed/demobank-claude')
    response=TestClient(app).post('/chat',json={'messages':[{'role':'user','content':'Hello'}]})
    assert 'event: error' in response.text
    assert 'event: answer' not in response.text and 'event: done' not in response.text

@pytest.mark.skipif(not (os.getenv('TEST_CLAUDE_CLI_LIVE')=='1' and shutil.which(os.getenv('CLAUDE_CLI_PATH','claude'))),
    reason='Opt-in: TEST_CLAUDE_CLI_LIVE=1 with a signed-in Claude Code CLI')
def test_live_cli_returns_schema_checked_json(monkeypatch):
    from app.services import llm_gateway
    monkeypatch.setenv('CHAT_TRANSPORT','claude')
    prompt={'agent':'transport_check','version':'v0','id':'transport_check+live'}
    out=asyncio.run(llm_gateway.call(prompt,'Reply with the number the customer asks for, in words, in the answer field.',
        [('user','What is two plus two?')],SCHEMA))
    assert set(out.data)=={'answer'} and 'four' in out.data['answer'].lower() and out.output_tokens
