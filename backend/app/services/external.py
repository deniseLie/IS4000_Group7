"""External read-only gateway (architecture §14.4): the RM's Zoom meeting check. The public holiday and FX tools were
removed on 8 Nov 2026.

It never receives customer data: the input is a Zoom meeting number, checked before any request. Only hosts named in EXTERNAL_HOSTS are called, with EXTERNAL_TIMEOUT_S per call and an in-memory
cache. Every network call is audited as data.external_called {tool, host, status, ms}. A failure raises ExternalError,
which the agent reports in its section; nothing is ever made up in its place.

- zoom_meeting: one GET /v2/meetings/{number} on api.zoom.us with the bank's own Zoom login (services/meetings/zoom.py
  refreshed_token), for tools.zoom_meeting_check only. It is not in TOOLS (public data): the caller has already checked
  the meeting is in the RM's book (rm_zoom_check, 0150), so the shared cache never answers a meeting she may not see.
  The token is sent as a header and is never in the cache key, the audit, a log or an error.
"""
import asyncio
import time
from collections.abc import Callable
from typing import Any
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictInt, ValidationError
from app.core.config import settings
from app.services import audit

ZOOM_HOST, ZOOM_TTL_S = 'api.zoom.us', 60
CACHE: dict[tuple, tuple[float, Any]] = {}


class ExternalError(RuntimeError):
    """An external tool could not answer: bad_input, host_not_allowed, not_found, timeout, unavailable, bad_response,
    not_configured or not_authorised."""
    def __init__(self, tool: str, reason: str):
        super().__init__(f'{tool}: {reason}')
        self.tool, self.reason = tool, reason


class ZoomMeeting(BaseModel):
    """Only what the check compares: names, emails, join URLs and passwords in Zoom's reply are never parsed."""
    model_config = ConfigDict(extra='ignore', hide_input_in_errors=True)
    id: StrictInt
    topic: str = Field(max_length=300)
    start_time: AwareDatetime
    duration: StrictInt = Field(ge=0, le=1440)
    status: str = Field(max_length=20)



def allowed_hosts() -> frozenset[str]:
    return frozenset(h.strip().lower() for h in settings.external_hosts.split(',') if h.strip())


def scope_missing(host: str, response: httpx.Response) -> bool:
    """Zoom answers a token without the needed scope with 400 and code 4711 (seen live 9 Nov 2026), not 401/403."""
    if host != ZOOM_HOST or response.status_code != 400:return False
    try:body = response.json()
    except ValueError:return False
    return isinstance(body, dict) and body.get('code') == 4711


async def fetch(tool: str, host: str, path: str, params: dict[str, str], ttl: float, parse: Callable[[Any], Any],
        token: SecretStr | None = None) -> Any:
    """One cached GET to an allow-listed host; the parsed result, or ExternalError. Redirects are not followed.
    `token` is sent as a bearer header only: it is not part of the cache key and never reaches the audit or an error."""
    if host not in allowed_hosts():raise ExternalError(tool, 'host_not_allowed')
    key = (host, path, tuple(sorted(params.items())))
    if (hit := CACHE.get(key)) and hit[0] > time.monotonic():return hit[1]
    start, status = time.monotonic(), 'error'
    try:
        async with httpx.AsyncClient(timeout=settings.external_timeout_s, follow_redirects=False) as client:
            response = await client.get(f'https://{host}{path}', params=params,
                headers={'Authorization': 'Bearer '+token.get_secret_value()} if token is not None else None)
        status = response.status_code
        if response.status_code == 404:raise ExternalError(tool, 'not_found')
        if response.status_code in (401, 403) or scope_missing(host, response):raise ExternalError(tool, 'not_authorised')
        if response.status_code != 200:raise ExternalError(tool, 'unavailable')
        value = parse(response.json())
    except httpx.TimeoutException as error:
        status = 'timeout'
        raise ExternalError(tool, 'timeout') from error
    except httpx.HTTPError as error:
        raise ExternalError(tool, 'unavailable') from error
    except (ValueError, TypeError, KeyError, ValidationError) as error:  # not JSON, or not the expected shape
        raise ExternalError(tool, 'bad_response') from error
    finally:
        audit.add('data', 'data.external_called', 'success' if status == 200 else 'failed',
            details={'tool': tool, 'host': host, 'status': status, 'ms': int((time.monotonic()-start)*1000)})
    CACHE[key] = (time.monotonic()+ttl, value)
    return value


async def zoom_token(tool: str) -> SecretStr:
    """The bank's own Zoom login, refreshed under the lock it shares with the meeting worker, within EXTERNAL_TIMEOUT_S.
    A refresh failure becomes a short code: ZoomFailure messages can carry Zoom's response body."""
    from app.services.meetings.zoom import ZoomFailure, refreshed_token
    if settings.zoom_oauth_directory is None:raise ExternalError(tool, 'not_configured')
    try:return await asyncio.wait_for(asyncio.to_thread(refreshed_token, settings.zoom_oauth_directory), settings.external_timeout_s)
    except TimeoutError:raise ExternalError(tool, 'timeout') from None
    except (ZoomFailure, OSError, ValueError, httpx.HTTPError):raise ExternalError(tool, 'not_authorised') from None


async def zoom_meeting(tool: str, provider_id: int) -> ZoomMeeting:
    """One registered Zoom meeting as Zoom has it now (cached 60 s). The caller has already checked the RM may see it."""
    if not (isinstance(provider_id, int) and 0 < provider_id < 10**16):raise ExternalError(tool, 'bad_input')
    token = await zoom_token(tool)
    meeting = await fetch(tool, ZOOM_HOST, f'/v2/meetings/{provider_id}', {}, ZOOM_TTL_S, ZoomMeeting.model_validate, token=token)
    if meeting.id != provider_id:raise ExternalError(tool, 'bad_response')
    return meeting

