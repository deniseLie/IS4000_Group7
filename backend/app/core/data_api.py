"""Public-key/JWT Data API calls with bounded retries. Writes require server-side idempotency keys.

Callers enforce scope and validate external data. Business conflicts use non-transient error codes.
"""
import asyncio
import logging
import os
import re
import ssl
from collections.abc import Callable
from typing import TypeVar
import certifi
import httpx
from postgrest.constants import DEFAULT_POSTGREST_CLIENT_TIMEOUT
from postgrest.exceptions import APIError
from postgrest import SyncPostgrestClient
from starlette.concurrency import run_in_threadpool
from app.core.observability import log_event
from app.schemas.contracts import RequestContext

T=TypeVar('T')
TRANSIENT_CODES={'PGRST000','PGRST001','PGRST002','PGRST003','53300','57P01','40001','40P01'}
NETWORK_ERRORS=(httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError)
# Built once: loading the CA bundle for every new client cost 1-2 s of GIL-bound time per request on Windows.
TLS=ssl.create_default_context(cafile=certifi.where())


def database_code(error: APIError) -> str | None:
    """Keep only vendor codes in audit/log metadata; the original SDK error retains its full body."""
    code=error.code
    return code if isinstance(code,str) and re.fullmatch(r'(?:[A-Z0-9]{5}|PGRST[0-9]{3})',code) else None


def http_client() -> httpx.Client:
    """A fresh client per request, so caller JWT headers are never shared, over the process-wide TLS context."""
    return httpx.Client(verify=TLS,timeout=DEFAULT_POSTGREST_CLIENT_TIMEOUT)


def connect(ctx: RequestContext) -> SyncPostgrestClient:
    db=SyncPostgrestClient(os.environ['SUPABASE_URL'].rstrip('/')+'/rest/v1',
        headers={'apikey':os.environ['SUPABASE_ANON_KEY'],'X-Request-ID':str(ctx.request_id)},http_client=http_client())
    if ctx.principal.jwt is not None:db.auth(ctx.principal.jwt.get_secret_value())
    return db


async def execute(operation: Callable[[],T],tool: str) -> T:
    attempt: int=0
    while True:
        try:return await run_in_threadpool(operation)
        except (APIError,*NETWORK_ERRORS) as error:
            code=database_code(error) if isinstance(error,APIError) else None
            if attempt==2 or isinstance(error,APIError) and code not in TRANSIENT_CODES:
                log_event('data_api_failed',logging.WARNING,tool=tool,attempt=attempt+1,error_type=type(error).__name__,db_code=code)
                raise
            attempt+=1
            log_event('data_api_retry',logging.WARNING,tool=tool,attempt=attempt,error_type=type(error).__name__,db_code=code)
            await asyncio.sleep(0.5*2**(attempt-1))
