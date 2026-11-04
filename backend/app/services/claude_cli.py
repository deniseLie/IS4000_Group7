"""Local, stateless Claude Code CLI transport (the signed-in subscription). No API keys, tools or shell interpolation."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile

# Agents run in parallel (Send x N); a few CLI turns at once, the rest wait instead of failing.
_slots=asyncio.Semaphore(int(os.getenv('CLAUDE_CLI_CONCURRENCY','3')))

def cli_environment():
    # Never pass OpenRouter/Supabase/Anthropic keys or parent session variables: the CLI signs in with its own login.
    allowed = ('PATH','HOME','USER','LOGNAME','TMPDIR','TMP','TEMP','LANG','LC_ALL',
               'SYSTEMROOT','WINDIR','APPDATA','LOCALAPPDATA','USERPROFILE','CLAUDE_CONFIG_DIR')
    return {key:os.environ[key] for key in allowed if key in os.environ}

def command(binary, directory, model, schema):
    # No tools, MCP servers, settings, skills, hooks or saved session; the conversation comes on stdin.
    return [binary,'-p','--output-format','json','--model',model,'--tools','','--strict-mcp-config',
            '--setting-sources','','--disable-slash-commands','--no-session-persistence','--permission-mode','default',
            '--system-prompt-file',str(directory / 'instructions.md'),'--json-schema',json.dumps(schema)]

def parse_result(output):
    """Accept only a successful turn's structured output; never return the CLI's text, errors or stderr."""
    event=json.loads(output)
    if not isinstance(event,dict) or event.get('type')!='result' or event.get('subtype')!='success' or event.get('is_error'):
        raise RuntimeError('Claude Code request failed; check CLI sign-in and subscription limits')
    if event.get('permission_denials'):raise RuntimeError('Claude Code attempted a non-answer action')
    parsed=event.get('structured_output')
    if not isinstance(parsed,dict):raise RuntimeError('Claude Code did not complete an answer')
    usage=event.get('usage') or {}
    counted=[v for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens') if isinstance(v:=usage.get(k),int)]
    tokens={'input_tokens':sum(counted)} if counted else {}  # cached prompt tokens are still input
    if isinstance(usage.get('output_tokens'),int):tokens['output_tokens']=usage['output_tokens']
    return parsed,tokens

async def answer(prompt, schema, messages):
    async with _slots:
        return await _answer(prompt,schema,messages)

async def _answer(prompt, schema, messages):
    binary=shutil.which(os.getenv('CLAUDE_CLI_PATH','claude'))
    if not binary:raise RuntimeError('Claude Code CLI not found; install it and sign in')
    model=os.getenv('CLAUDE_MODEL','haiku')
    # A new private directory per request, removed even on timeout/failure; the CLI never sees the repository.
    with tempfile.TemporaryDirectory(prefix='demobank-claude-') as folder:
        directory=Path(folder)
        (directory/'instructions.md').write_text(prompt,encoding='utf-8')
        payload=json.dumps({'conversation':[{'role':role,'content':content} for role,content in messages]},ensure_ascii=False)
        proc=await asyncio.create_subprocess_exec(*command(binary,directory,model,schema),
            cwd=directory,env=cli_environment(),stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            stdout,_=await asyncio.wait_for(proc.communicate(payload.encode()),timeout=90)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if proc.returncode:
            raise RuntimeError('Claude Code CLI failed; check CLI version, sign-in and model access')
        return parse_result(stdout.decode('utf-8'))
