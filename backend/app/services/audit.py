"""Audit Log Service client (system-architecture.md §6.5, F3, migration 0005). Events are collected per request and written
in one public.audit_record_batch call before the answer is sent; if that write fails, the request fails (AuditWriteError)
instead of continuing unaudited. State changes do not use this: they write their audit row in their own transaction
(audit.record() and the row triggers in 0006).
Always on: without SUPABASE_URL / SUPABASE_ANON_KEY the write fails, so the request fails. The only off switch is
AUDIT_STORE=0, set by the unit and browser test runners so they never add rows to the append-only table; offline eval
calls make_answer() directly and never opens a batch. Events hold ids, enums and counts only; add() rejects anything else,
matching audit.is_safe()."""
import asyncio
import logging
import os
import re
from contextvars import ContextVar
from uuid import UUID
from starlette.concurrency import run_in_threadpool
from supabase import create_client
from app.core.observability import log_event, request_id
from app.schemas.contracts import AuditEvent

TOKEN=re.compile(r'[A-Za-z0-9_.:#/+-]{1,80}')
CITE=re.compile(r'[a-z0-9_-]{1,60}@v[0-9]+(#[a-z0-9_-]{1,40})?')
KEY=re.compile(r'[a-z][a-z0-9_]{0,39}')
MAX_EVENTS=50
current_batch=ContextVar('current_batch',default=None)
_anon=None

class AuditWriteError(RuntimeError):
    """The batch could not be stored; the request must fail."""

def enabled():
    return os.getenv('AUDIT_STORE')!='0'  # test runners only

def is_safe(v,depth=0):
    """Numbers, booleans, null, short tokens (enums, ids, cite ids) and one level of lists/dicts of those. No spaces, no card-length digit runs."""
    if v is None or isinstance(v,(bool,int,float)):return True
    if isinstance(v,str):return not re.search(r'[0-9]{12,}',v) and bool(TOKEN.fullmatch(v) or CITE.fullmatch(v))
    if isinstance(v,(list,tuple)):return depth<2 and len(v)<=20 and all(is_safe(x,depth+1) for x in v)
    if isinstance(v,dict):return depth<2 and len(v)<=20 and all(isinstance(k,str) and KEY.fullmatch(k) and is_safe(x,depth+1) for k,x in v.items())
    return False

def _uuid(value):
    try:return UUID(value)
    except (TypeError,ValueError):return None

def _write(db,payload):
    global _anon
    if db is None:
        if not (os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_ANON_KEY')):raise RuntimeError('Supabase is not configured')
        if _anon is None:_anon=create_client(os.environ['SUPABASE_URL'],os.environ['SUPABASE_ANON_KEY'])
        db=_anon
    db.rpc('audit_record_batch',{'p_events':payload}).execute()

class AuditBatch:
    """One request's events. db is the customer's JWT client (events land on their trail); None writes as a guest with the anon key."""
    def __init__(self,db=None,session_id=None):
        self.db=db
        self.request_id=_uuid(request_id.get())
        self.session_id=session_id
        self.events=[]

    def add(self,category,action,outcome='success',details=None,**fields):
        details=details or {}
        if not isinstance(details,dict) or not is_safe(details):raise ValueError(f'audit details for {action} must hold ids, enums and counts only')
        if len(self.events)>=MAX_EVENTS:raise ValueError('too many audit events in one request')
        fields.setdefault('session_id',self.session_id)
        self.events.append(AuditEvent(category=category,action=action,outcome=outcome,details=details,request_id=self.request_id,**fields))

    async def flush(self):
        """Write and clear the batch: all events or none. Raises AuditWriteError on any failure or after 10 s."""
        events,self.events=self.events,[]
        if not events:return
        if not enabled():
            log_event('audit_skipped',events=len(events))
            return
        payload=[e.model_dump(mode='json',exclude_none=True) for e in events]
        try:
            await asyncio.wait_for(run_in_threadpool(_write,self.db,payload),timeout=10)
        except Exception as e:
            log_event('audit_write_failed',logging.WARNING,events=len(payload),error=type(e).__name__)
            raise AuditWriteError(type(e).__name__) from e

def start_batch(db=None,session_id=None):
    batch=AuditBatch(db,session_id)
    current_batch.set(batch)
    return batch

def add(category,action,outcome='success',details=None,**fields):
    """Add to the current request's batch, for modules that are not handed the batch. No-op outside a request (offline eval)."""
    batch=current_batch.get()
    if batch:batch.add(category,action,outcome,details,**fields)
