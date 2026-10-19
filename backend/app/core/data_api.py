"""Public-key/JWT Data API calls with bounded retries. Writes require server-side idempotency keys.

Callers enforce scope and validate external data. Business conflicts use non-transient error codes.
"""
import asyncio
import logging
import os
import re
from collections.abc import Callable
from typing import TypeVar
import httpx
from postgrest.exceptions import APIError
from postgrest import SyncPostgrestClient
from starlette.concurrency import run_in_threadpool
from app.core.observability import log_event
from app.schemas.contracts import RequestContext

T=TypeVar('T')
TRANSIENT_CODES={'PGRST000','PGRST001','PGRST002','PGRST003','53300','57P01','40001','40P01'}
NETWORK_ERRORS=(httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError)


def database_code(error: APIError) -> str | None:
    """Keep only vendor codes in audit/log metadata; the original SDK error retains its full body."""
    code=error.code
    return code if isinstance(code,str) and re.fullmatch(r'(?:[A-Z0-9]{5}|PGRST[0-9]{3})',code) else None


def connect(ctx: RequestContext) -> SyncPostgrestClient:
    db=SyncPostgrestClient(os.environ['SUPABASE_URL'].rstrip('/')+'/rest/v1',
        headers={'apikey':os.environ['SUPABASE_ANON_KEY']})
    if ctx.principal.jwt is not None:db.auth(ctx.principal.jwt.get_secret_value())
    return db


async def execute(operation: Callable[[],T],tool: str) -> T:
    attempt: int=0
    while True:
        try:return await run_in_threadpool(operation)
        except (APIError,*NETWORK_ERRORS) as error:
            code=database_code(error) if isinstance(error,APIError) else None
            if attempt==2 or isinstance(error,APIError) and code not in TRANSIENT_CODES:raise
            attempt+=1
            log_event('data_api_retry',logging.WARNING,tool=tool,attempt=attempt,error_type=type(error).__name__,db_code=code)
            await asyncio.sleep(0.5*2**(attempt-1))
