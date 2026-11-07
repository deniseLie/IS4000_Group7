"""Local, stateless Codex CLI transport. No API keys or shell interpolation."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
from pydantic import JsonValue

_request_lock=asyncio.Lock()

def timeout_s() -> float:
    """Keep the existing 75-second deadline unless CODEX_CLI_TIMEOUT_S explicitly allows a longer subscription turn."""
    try:value=float(os.getenv('CODEX_CLI_TIMEOUT_S','75'))
    except ValueError as error:raise RuntimeError('CODEX_CLI_TIMEOUT_S must be a number of seconds from 1 to 900') from error
    if not 1<=value<=900:raise RuntimeError('CODEX_CLI_TIMEOUT_S must be a number of seconds from 1 to 900')
    return value

# These are request-local overrides; the user's Codex configuration is untouched.
DISABLED_FEATURES = (
    'shell_tool','unified_exec','shell_snapshot','apps','plugins','hooks',
    'browser_use','computer_use','image_generation','view_image','multi_agent',
    'multi_agent_v2','memories','skill_search','artifact','goals','code_mode',
    'code_mode_host','sleep_tool','workspace_dependencies',
)

def cli_environment():
    # In particular, never pass OpenRouter/Supabase keys, API auth overrides,
    # parent agent IDs or user-supplied request values to the child environment.
    allowed = ('PATH','HOME','USER','LOGNAME','TMPDIR','TMP','TEMP','LANG','LC_ALL',
               'SYSTEMROOT','WINDIR','APPDATA','LOCALAPPDATA','USERPROFILE','CODEX_HOME')
    return {key:os.environ[key] for key in allowed if key in os.environ}

def command(binary, directory, model):
    args = [binary,'--no-daemon','exec','--ignore-user-config','--strict-config',
            '--ephemeral','--skip-git-repo-check','--sandbox','read-only',
            '--cd',str(directory),'--model',model,'--json','--color','never',
            '--output-schema',str(directory / 'schema.json')]
    config = {
        'forced_login_method':'chatgpt','approval_policy':'never',
        'web_search':'disabled','project_doc_max_bytes':0,
        'history.persistence':'none','model_reasoning_effort':'low',
        'model_instructions_file':str(directory / 'instructions.md'),
        'log_dir':str(directory / 'logs'),
        'suppress_unstable_features_warning':True,
        'features.skip_host_skill_discovery':True,
    }
    config.update({f'features.{name}':False for name in DISABLED_FEATURES})
    for key,value in config.items():args += ['-c',f'{key}={json.dumps(value)}']
    return args + ['-']

def parse_events(output):
    """Accept only a completed text-only turn; never return progress or stderr."""
    answer = None
    usage = None
    for line in output.splitlines():
        event = json.loads(line)
        kind = event.get('type')
        if kind in ('error','turn.failed'):
            raise RuntimeError('Codex request failed; check CLI sign-in and subscription limits')
        if kind in ('item.started','item.updated','item.completed'):
            item = event.get('item',{})
            # This CLI emits a diagnostic when its execution host is disabled.
            # Keep that restriction; it is not a model/tool result.
            if item.get('type')=='error' and item.get('message','').startswith('Code Mode is unavailable because code-mode host is disabled.'):
                continue
            if item.get('type') not in ('agent_message','reasoning'):
                kind=item.get('type')
                label=kind if isinstance(kind,str) and kind.isascii() and kind.replace('_','').isalpha() and len(kind)<=40 else 'unrecognised_item'
                raise RuntimeError(f'Codex attempted a non-answer action ({label})')
            if kind=='item.completed' and item.get('type')=='agent_message':answer=item.get('text')
        if kind=='turn.completed':usage=event.get('usage',{})
    if usage is None or not answer:
        raise RuntimeError('Codex did not complete an answer')
    parsed=json.loads(answer)
    if not isinstance(parsed,dict):raise RuntimeError('Codex returned an invalid answer')
    for key in ('input_tokens','output_tokens'):
        if key in usage and (type(usage[key]) is not int or usage[key]<0):raise ValueError('Codex '+key+' must be a non-negative integer')
    return parsed,{key:usage[key] for key in ('input_tokens','output_tokens') if key in usage}

async def answer(prompt, schema, messages):
    return await answer_with_model(prompt,schema,messages,os.getenv('CODEX_MODEL','gpt-5.6-luna'))

async def answer_with_model(prompt: str,schema: dict[str,JsonValue],messages: list[tuple[str,str]],model: str) -> tuple[dict[str,JsonValue],dict[str,int]]:
    """Queue subscription calls serially, including parallel agents, without changing application or user settings."""
    async with _request_lock:
        return await _answer(prompt,schema,messages,model)

async def _answer(prompt, schema, messages,model):
    binary=shutil.which(os.getenv('CODEX_CLI_PATH','codex'))
    if not binary:raise RuntimeError('Codex CLI not found; install it and sign in with ChatGPT')
    # A new private directory/turn for each request; no resume and no repo access
    # through the working directory. Files are removed even on timeout/failure.
    with tempfile.TemporaryDirectory(prefix='demobank-codex-') as folder:
        directory=Path(folder)
        (directory/'schema.json').write_text(json.dumps(schema),encoding='utf-8')
        (directory/'instructions.md').write_text(prompt,encoding='utf-8')
        payload=json.dumps({'conversation':[{'role':role,'content':content} for role,content in messages]},ensure_ascii=False)
        proc=await asyncio.create_subprocess_exec(*command(binary,directory,model),
            cwd=directory,env=cli_environment(),stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        try:
            stdout,_=await asyncio.wait_for(proc.communicate(payload.encode()),timeout=timeout_s())
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        if proc.returncode:
            raise RuntimeError(f'Codex CLI exited with status {proc.returncode}; check CLI version, ChatGPT sign-in and model access')
        return parse_events(stdout.decode('utf-8'))
