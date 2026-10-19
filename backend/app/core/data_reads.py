"""Bounded retries for read-only SDK operations. Callers enforce scope and validate returned data."""
import asyncio
import logging
import re
from collections.abc import Callable
from typing import TypeVar
import httpx
from postgrest.exceptions import APIError
from starlette.concurrency import run_in_threadpool
from app.core.observability import log_event

T=TypeVar('T')
TRANSIENT_CODES={'PGRST000','PGRST001','PGRST002','PGRST003','53300','57P01','40001','40P01'}
NETWORK_ERRORS=(httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError)


def database_code(error: APIError) -> str | None:
    """Keep only vendor codes in audit/log metadata; the original SDK error retains its full body."""
    code=error.code
    return code if isinstance(code,str) and re.fullmatch(r'(?:[A-Z0-9]{5}|PGRST[0-9]{3})',code) else None


async def read(operation: Callable[[],T],tool: str) -> T:
    attempt: int=0
    while True:
        try:return await run_in_threadpool(operation)
        except (APIError,*NETWORK_ERRORS) as error:
            code=database_code(error) if isinstance(error,APIError) else None
            if attempt==2 or isinstance(error,APIError) and code not in TRANSIENT_CODES:raise
            attempt+=1
            log_event('data_read_retry',logging.WARNING,tool=tool,attempt=attempt,error_type=type(error).__name__,db_code=code)
            await asyncio.sleep(0.5*2**(attempt-1))
