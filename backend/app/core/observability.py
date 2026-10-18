import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

request_id=ContextVar('request_id',default='-')
logger=logging.getLogger('demobank')
current_trace=ContextVar('current_trace',default=None)

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
    """One chat request: trace-level fields in attrs, timed steps in spans. Ids, counts, flags and timings only, never text."""
    def __init__(self):
        self.id=request_id.get()
        self.attrs={}
        self.spans=[]
        self.n=0
        self.start=time.perf_counter()

def start_trace():
    trace=Trace()
    current_trace.set(trace)
    return trace

@contextmanager
def span(name,kind,**attrs):
    """Time a step and record it on the current trace (no-op recording when none). Yields the attrs dict so callers can add results."""
    trace=current_trace.get()
    if trace:trace.n+=1
    record={'seq':trace.n-1 if trace else 0,'name':name,'kind':kind,'started_at':datetime.now(timezone.utc).isoformat(),'status':'ok','attrs':attrs}
    start=time.perf_counter()
    try:
        yield attrs
    except BaseException as e:
        record.update(status='error',error_type=type(e).__name__[:64])
        raise
    finally:
        record['ms']=round((time.perf_counter()-start)*1000)
        if trace:trace.spans.append(record)

def add_span(name,kind,**attrs):
    with span(name,kind,**attrs):pass

class RequestIdMiddleware:
    """Pure ASGI so streaming (SSE) responses are not buffered."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        rid=next((v.decode() for k,v in scope['headers'] if k==b'x-request-id' and len(v)<=64),None) or str(uuid.uuid4())
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
