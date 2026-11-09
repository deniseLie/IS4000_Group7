"""LLM Gateway (docs/architecture.md §5.11): the one model client for the planner and all agents.
Strict JSON output, timeout, limited retries, per-request token budget, one `llm` LangSmith run per call
(LangChain nests the provider call under it) and one model-call record for the audit log.
Failures raise LLMError and are reported; they are never replaced with a sample answer."""
import asyncio
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import get_usage_metadata_callback
from app.core.config import settings
from app.core.observability import current_trace, span

class LLMError(RuntimeError):
    reason='provider_error'
class LLMNotConfigured(LLMError):reason='not_configured'
class LLMTimeout(LLMError):reason='timeout'
class LLMBudgetExceeded(LLMError):reason='budget_exceeded'
class LLMBadOutput(LLMError):reason='bad_output'

@dataclass(frozen=True)
class LLMResult:
    data:dict
    model:str
    prompt_id:str
    input_tokens:int|None
    output_tokens:int|None
    latency_ms:int
    retries:int=0

TRANSIENT_STATUS={408,409,429,500,502,503,504}

def transient(e):
    """Retry only timeouts, connection failures, rate limits and 5xx; never bad output or auth errors."""
    return isinstance(e,TimeoutError) or type(e).__name__ in ('APIConnectionError','APITimeoutError') or getattr(e,'status_code',None) in TRANSIENT_STATUS

def transport():
    name=os.getenv('CHAT_TRANSPORT','api')
    if name not in ('api','codex','claude'):raise LLMNotConfigured('Unknown CHAT_TRANSPORT; choose api, codex or claude')
    return name

def openrouter():
    return (os.getenv('LLM_PROVIDER') or 'openai')=='openai' and urlsplit(os.getenv('LLM_BASE_URL','')).hostname=='openrouter.ai'

def reasoning_mode(reasoning=True):
    """'off' only where the switch reaches the model: the API transport on OpenRouter. The Codex and Claude CLIs cannot turn
    thinking off, and other providers keep their default, so the span says 'default' there."""
    return 'off' if not reasoning and transport()=='api' and openrouter() else 'default'

def build_model(max_tokens=None,model=None,reasoning=True):
    """LLM_PROVIDER picks the LangChain provider (default 'openai', which also covers OpenRouter via LLM_BASE_URL).
    model overrides LLM_MODEL for one caller (TALKING_POINTS_MODEL); same provider, key and base URL.
    reasoning=False asks OpenRouter to turn the model's hidden reasoning off (it counts against max_tokens and varies per call)."""
    provider = os.getenv('LLM_PROVIDER') or 'openai'
    kwargs = {'timeout':settings.llm_timeout_s,'max_retries':0,'max_tokens':max_tokens or settings.max_tokens}  # retries happen in call()
    if provider == 'openai':
        kwargs.update(api_key=os.environ['LLM_API_KEY'],base_url=os.environ['LLM_BASE_URL'])
        if urlsplit(kwargs['base_url']).hostname == 'openrouter.ai':
            kwargs['extra_body'] = {'provider':{'require_parameters':True},**({} if reasoning else {'reasoning':{'enabled':False}})}
    return init_chat_model(model or os.environ['LLM_MODEL'],model_provider=provider,**kwargs)

def model_name(override=None):
    if transport()=='codex':return os.getenv('CODEX_MODEL','gpt-5.6-luna')
    if transport()=='claude':return os.getenv('CLAUDE_MODEL','haiku')
    needed = ('LLM_MODEL',) + (('LLM_API_KEY','LLM_BASE_URL') if (os.getenv('LLM_PROVIDER') or 'openai') == 'openai' else ())
    if not all(os.getenv(k) for k in needed):raise LLMNotConfigured('Model is not configured')
    return override or os.environ['LLM_MODEL']

def remaining_budget():
    """Tokens left for this request (the current trace). Outside a request only the per-call cap applies."""
    trace=current_trace.get()
    return settings.llm_request_token_budget-(trace.tokens if trace else 0)

def check(value,schema,path='$'):
    """Validate the subset of JSON Schema the gateway accepts. Errors name the path only, never the model's text."""
    kind=schema.get('type')
    types={'object':dict,'array':list,'string':str,'boolean':bool,'integer':int,'number':(int,float)}
    if kind and (not isinstance(value,types[kind]) or (kind in ('integer','number') and isinstance(value,bool))):
        raise LLMBadOutput(f'{path}: expected {kind}')
    if 'enum' in schema and value not in schema['enum']:raise LLMBadOutput(f'{path}: value not allowed')
    if kind=='object':
        props=schema.get('properties',{})
        if missing:=[k for k in schema.get('required',[]) if k not in value]:raise LLMBadOutput(f'{path}: missing {missing[0]}')
        if schema.get('additionalProperties') is False and (extra:=set(value)-set(props)):raise LLMBadOutput(f'{path}: unexpected {sorted(extra)[0]}')
        for k,v in value.items():
            if k in props:check(v,props[k],f'{path}.{k}')
    if kind=='array':
        for i,v in enumerate(value):check(v,schema.get('items',{}),f'{path}[{i}]')

async def _invoke_api(system,messages,schema,max_tokens,a,name=None,retries=None,reasoning=True):
    model=build_model(max_tokens,name,reasoning).with_structured_output(schema,method='json_schema',strict=True)
    retries=settings.llm_max_retries if retries is None else retries
    with get_usage_metadata_callback() as usage:
        for attempt in range(retries+1):
            try:
                data=await asyncio.wait_for(model.ainvoke([('system',system),*messages]),settings.llm_timeout_s)
                break
            except Exception as e:
                if attempt==retries or not transient(e):raise
                a['retries']=attempt+1
                await asyncio.sleep(0.5*2**attempt)
        used=list(usage.usage_metadata.values())  # counts every attempt
    tokens=(sum(u.get('input_tokens',0) for u in used),sum(u.get('output_tokens',0) for u in used)) if used else (None,None)
    return data,tokens

async def call(prompt,system,messages,schema,model=None,retries=None,reasoning=True):
    """prompt: a prompts.load() dict (agent, version, id). system: the full system text built from it.
    messages: (role, content) pairs. schema: strict JSON schema for the reply. Returns LLMResult or raises LLMError.
    model and retries override LLM_MODEL and LLM_MAX_RETRIES for one caller (API transport; the CLIs never retry).
    reasoning=False turns hidden reasoning off for one caller where reasoning_mode() can (the RM intents in handlers.REASONING)."""
    name=transport()
    model=model_name(model if name=='api' else None)
    left=remaining_budget()
    if left<=0:raise LLMBudgetExceeded('Per-request token budget is used up')
    max_tokens=min(settings.max_tokens,left)
    start=time.perf_counter()
    outcome='error'
    inputs={'system':system,'messages':[{'role':role,'content':content} for role,content in messages]} if name=='codex' else None
    mode=reasoning_mode(reasoning)
    with span('llm','llm',agent=prompt['agent'],model=model,prompt_version=prompt['id'],transport=name,reasoning=mode,model_inputs=inputs) as a:
        try:
            try:
                if name=='codex':
                    from app.services import codex_cli  # own timeout; no retries, each run is a subscription turn
                    data,usage=await codex_cli.answer(system,schema,messages)
                    a['model_outputs']=data
                    tokens=(usage.get('input_tokens'),usage.get('output_tokens'))
                elif name=='claude':
                    from app.services import claude_cli  # own timeout; no retries, each run is a subscription turn
                    data,usage=await claude_cli.answer(system,schema,messages)
                    tokens=(usage.get('input_tokens'),usage.get('output_tokens'))
                    a.update({k:usage[k] for k in ('thinking_tokens','num_turns','api_ms','answer_chars') if k in usage})  # counts only
                else:data,tokens=await _invoke_api(system,messages,schema,max_tokens,a,model,retries,mode!='off')
            except TimeoutError as e:
                raise LLMTimeout('Model request timed out') from e
            except LLMError:raise
            except Exception as e:
                a['cause']=type(e).__name__[:64]
                if type(e).__name__=='OutputParserException':raise LLMBadOutput('Model output was not valid JSON') from e
                raise LLMError(str(e) if name in ('codex','claude') else f'Model request failed ({type(e).__name__})') from e
            tin,tout=tokens
            trace=current_trace.get()
            # Unknown usage is charged at the output cap so the budget still holds.
            if trace:trace.tokens+=(tin or 0)+(tout if tout is not None else max_tokens)
            if tin is not None:a.update(input_tokens=tin,output_tokens=tout)
            if not isinstance(data,dict):raise LLMBadOutput('$: expected object')
            check(data,schema)
            outcome='ok'
            return LLMResult(data,model,prompt['id'],tin,tout,round((time.perf_counter()-start)*1000),a.get('retries',0))
        except LLMError as e:
            outcome=e.reason
            raise
        finally:
            a['outcome']=outcome
            if trace:=current_trace.get():
                trace.llm_calls.append({'ok':outcome=='ok','agent':prompt['agent'],'model':model,'prompt_version':prompt['id'],'transport':name,'reasoning':mode,
                    'input_tokens':a.get('input_tokens'),'output_tokens':a.get('output_tokens'),'ms':round((time.perf_counter()-start)*1000)})
