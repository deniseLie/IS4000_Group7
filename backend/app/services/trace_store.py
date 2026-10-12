import asyncio
import logging
import os
import time
from starlette.concurrency import run_in_threadpool
from supabase import create_client
from app.core.observability import log_event

KEYS=('mode','language','authenticated','history_len','history_trimmed','route','source_check','needs_human','privacy_warning','reject_reason','prompt_version','article_ids','outcome','error_type')
_client=None
_pending=set()

def enabled():
    """Opt-in (TRACE_STORE=1) so tests and offline eval never write to a real project."""
    return os.getenv('TRACE_STORE')=='1' and bool(os.getenv('SUPABASE_URL') and os.getenv('SUPABASE_ANON_KEY'))

def build_payload(trace):
    payload={k:trace.attrs[k] for k in KEYS if trace.attrs.get(k) is not None}
    payload.update(trace_id=trace.id,total_ms=round((time.perf_counter()-trace.start)*1000),spans=trace.spans)
    llm=[s for s in trace.spans if s['name']=='llm']
    for field in ('input_tokens','output_tokens','cost_usd'):
        values=[s['attrs'][field] for s in llm if s['attrs'].get(field) is not None]
        if values:payload[field]=sum(values)
    return payload

def _write(payload):
    global _client
    if _client is None:_client=create_client(os.environ['SUPABASE_URL'],os.environ['SUPABASE_ANON_KEY'])
    _client.rpc('log_trace',{'payload':payload}).execute()

async def _save(payload):
    try:
        await asyncio.wait_for(run_in_threadpool(_write,payload),timeout=10)
    except Exception as e:
        log_event('trace_store_failed',logging.WARNING,error=type(e).__name__)

def save_trace(trace):
    """Fire and forget: a failed or slow write is logged and never affects the chat response."""
    if not enabled():return
    try:
        task=asyncio.get_running_loop().create_task(_save(build_payload(trace)))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception as e:
        log_event('trace_store_failed',logging.WARNING,error=type(e).__name__)
