import json
import logging
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from functools import lru_cache
from typing import TYPE_CHECKING
from contextlib import contextmanager
from contextvars import ContextVar
import langsmith
if TYPE_CHECKING:
    from app.schemas.agents import Evidence

request_id=ContextVar('request_id',default='-')
logger=logging.getLogger('demobank')
current_trace=ContextVar('current_trace',default=None)
span_sink=ContextVar('span_sink',default=None)  # evaluation/targets.py collects (name, attrs) of every span here; None in the app
evidence_sink: ContextVar[list['Evidence'] | None]=ContextVar('evidence_sink',default=None)  # Opt-in evaluation only; never logs record text.

class JsonFormatter(logging.Formatter):
    def format(self,record):
        data={'ts':self.formatTime(record,'%Y-%m-%dT%H:%M:%S'),'level':record.levelname,'logger':record.name,'request_id':request_id.get(),'event':record.getMessage()}
        data.update(getattr(record,'fields',{}))
        return json.dumps(data,ensure_ascii=False,default=str)

def setup_logging():
    """JSON lines on stdout. Callers log ids, counts and outcomes only, never user text or records."""
    handler=logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.handlers=[handler]
    logger.setLevel(logging.INFO)
    logger.propagate=False

def log_event(event,level=logging.INFO,**fields):
    logger.log(level,event,extra={'fields':fields})

class timer:
    def __enter__(self):
        self.start=time.perf_counter()
        return self
    def __exit__(self,*exc):
        self.ms=round((time.perf_counter()-self.start)*1000)

class Trace:
    """Request state LangSmith does not keep: the per-request token budget and the model calls the audit log records."""
    def __init__(self):
        self.attrs={}  # route, outcome and other request facts; the audit reads them and the root run carries them as metadata
        self.llm_calls=[]  # one dict per model call, for agent.llm_call audit events
        self.agents=[]  # one dict per agent run (names, statuses, counts and ms only), for the root run's summary
        self.tokens=0  # LLM tokens used so far; the gateway enforces the per-request budget
        self.tools=[]  # one dict per tool call (tool, agent, allowed, reason, shared, category; never arguments or rows)

AGENT_FACTS=('agent','intent','path','status','outcome','tool_calls','wave','stop','refused')
TOOL_FACTS=('tool','agent','allowed','reason','shared','category')
WORST=('ok','needs_clarification','needs_human','withheld','timeout','error')  # an agent that ran twice keeps its worst status

@lru_cache
def app_version():
    """APP_VERSION when the deployment sets it, else the checkout's short git sha, else None (never fails a request)."""
    if version:=os.getenv('APP_VERSION'):return version[:40]
    try:
        out=subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True,timeout=2,cwd=os.path.dirname(__file__))
        return (out.stdout.strip()[:40] or None) if out.returncode==0 else None
    except (OSError,subprocess.SubprocessError):return None

def request_facts(trace):
    """The per-request summary the root run carries as metadata (ids, names, counts and ms only, never text), so the admin
    dashboard reads one run per request instead of every step."""
    status={}
    for a in trace.agents:
        s=a.get('status') or ('timeout' if a.get('outcome')=='timeout' else a.get('outcome')) or 'error'
        old=status.get(a['agent'])
        status[a['agent']]=s if old is None or WORST.index(s if s in WORST else 'error')>WORST.index(old if old in WORST else 'error') else old
    calls=trace.llm_calls
    tokens=[(c.get('input_tokens') or 0)+(c.get('output_tokens') or 0) for c in calls if c.get('input_tokens') is not None]
    models=Counter(c['model'] for c in calls)
    facts=trace.attrs
    role=facts.get('role') or (('customer' if facts['authenticated'] else 'guest') if 'authenticated' in facts else None)
    refused=[t for t in trace.tools if t.get('allowed') is False]
    return {'agents':[a['agent'] for a in trace.agents],'agent_status':status,'model_calls':len(calls),'model_failures':sum(not c['ok'] for c in calls),
        'llm_ms':sum(c.get('ms') or 0 for c in calls),'llm_tokens':sum(tokens) if tokens else None,'model':models.most_common(1)[0][0] if models else None,
        'prompt_ids':sorted({f"{c['agent']}/{c['prompt_version']}" for c in calls if c.get('agent')}),'reasoning_off':sum(c.get('reasoning')=='off' for c in calls),
        'app_version':app_version(),
        # Build plan 3a: who asked and what the agents did with their tools, so the Agents and Failures pages can filter.
        'side':facts.get('side') or 'client','role':role,'tool_calls':len(trace.tools),
        'loop_stops':dict(Counter(x['stop'] for x in trace.agents if x.get('stop'))),
        'refusals':len(refused)+int(facts.get('plan_route')=='refused'),'step_refusals':sum(x.get('refused') or 0 for x in trace.agents),
        'refused_tools':sorted({f"{t.get('tool')}:{t.get('reason')}" for t in refused})[:10],
        'consent_gaps':sum(1 for t in trace.tools if t.get('shared') is False)}

def start_trace():
    trace=Trace()
    current_trace.set(trace)
    return trace

RUN_TYPES={'tool':'tool','agent':'chain','decision':'chain','llm':'chain','embedding':'embedding'}

@contextmanager
def span(name,kind,**attrs):
    """One step as a LangSmith run nested under the current run (nothing is sent while tracing is off).
    Yields the attrs dict so callers can add results; they become the run's metadata."""
    codex=kind=='llm' and attrs.get('transport')=='codex'
    inputs=attrs.pop('model_inputs',None)
    start=time.perf_counter()
    with langsmith.trace(name,'llm' if codex else RUN_TYPES[kind],inputs=inputs,metadata={'kind':kind}) as run:
        try:
            yield attrs
        except BaseException as e:
            attrs['error_type']=type(e).__name__[:64]
            raise
        finally:
            outputs=attrs.pop('model_outputs',None)
            if outputs is not None:run.set(outputs=outputs)
            run.add_metadata(attrs)
            if codex:
                run.add_metadata({'ls_provider':'openai','ls_model_name':attrs['model']})
                if isinstance(attrs.get('input_tokens'),int) and isinstance(attrs.get('output_tokens'),int):
                    run.set(usage_metadata={'input_tokens':attrs['input_tokens'],'output_tokens':attrs['output_tokens'],
                        'total_tokens':attrs['input_tokens']+attrs['output_tokens']})
            if (sink:=span_sink.get()) is not None:sink.append((name,dict(attrs)))
            if kind=='agent' and (trace:=current_trace.get()) is not None:
                if name=='agent' and attrs.get('agent'):
                    trace.agents.append({**{k:attrs[k] for k in AGENT_FACTS if attrs.get(k) is not None},'ms':round((time.perf_counter()-start)*1000)})
                elif name=='planner' and attrs.get('route'):trace.attrs.update(plan_route=attrs['route'],tasks=attrs.get('tasks'))
            if name=='tool_call' and (trace:=current_trace.get()) is not None:
                trace.tools.append({k:attrs[k] for k in TOOL_FACTS if attrs.get(k) is not None})

def add_span(name,kind,**attrs):
    with span(name,kind,**attrs):pass

class RequestIdMiddleware:
    """Pure ASGI so streaming (SSE) responses are not buffered."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        rid=next((v.decode('latin-1') for k,v in scope['headers'] if k==b'x-request-id'),'')
        try:rid=str(uuid.UUID(rid))  # RequestContext.request_id is a uuid; anything else is replaced
        except ValueError:rid=str(uuid.uuid4())
        token=request_id.set(rid)
        status=[0]
        async def send_with_id(message):
            if message['type']=='http.response.start':
                status[0]=message['status']
                message.setdefault('headers',[]).append((b'x-request-id',rid.encode()))
            await send(message)
        try:
            with timer() as t:
                await self.app(scope,receive,send_with_id)
        finally:
            if scope['path']!='/health':log_event('request',method=scope['method'],path=scope['path'],status=status[0],ms=getattr(t,'ms',None))
            request_id.reset(token)
