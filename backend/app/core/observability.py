import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
import langsmith

request_id=ContextVar('request_id',default='-')
logger=logging.getLogger('demobank')
current_trace=ContextVar('current_trace',default=None)
span_sink=ContextVar('span_sink',default=None)  # evaluation/targets.py collects (name, attrs) of every span here; None in the app

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
        self.tokens=0  # LLM tokens used so far; the gateway enforces the per-request budget

def start_trace():
    trace=Trace()
    current_trace.set(trace)
    return trace

RUN_TYPES={'tool':'tool','agent':'chain','decision':'chain','llm':'chain'}

@contextmanager
def span(name,kind,**attrs):
    """One step as a LangSmith run nested under the current run (nothing is sent while tracing is off).
    Yields the attrs dict so callers can add results; they become the run's metadata."""
    with langsmith.trace(name,RUN_TYPES[kind],metadata={'kind':kind}) as run:
        try:
            yield attrs
        except BaseException as e:
            attrs['error_type']=type(e).__name__[:64]
            raise
        finally:
            run.add_metadata(attrs)
            if (sink:=span_sink.get()) is not None:sink.append((name,dict(attrs)))

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
