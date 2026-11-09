"""External read-only tools (architecture §14.4, build plan phase 2): public holidays, indicative FX rates and the RM's
Zoom meeting check.

They never receive customer data: inputs are codes only (ISO 3166 alpha-2 country, ISO 4217 currency, a year), checked
before any request. Only hosts named in EXTERNAL_HOSTS are called, with EXTERNAL_TIMEOUT_S per call and an in-memory
cache. Every network call is audited as data.external_called {tool, host, status, ms}. A failure raises ExternalError,
which the agent reports in its section; nothing is ever made up in its place.

- public_holidays: Nager.Date (date.nager.at), any country.
- fx_rate: Frankfurter (api.frankfurter.dev), European Central Bank reference rates. Indicative only: never the
  client's card rate; the overseas fee comes from the knowledge base.
- zoom_meeting: one GET /v2/meetings/{number} on api.zoom.us with the bank's own Zoom login (services/meetings/zoom.py
  refreshed_token), for tools.zoom_meeting_check only. It is not in TOOLS (public data): the caller has already checked
  the meeting is in the RM's book (rm_zoom_check, 0150), so the shared cache never answers a meeting she may not see.
  The token is sent as a header and is never in the cache key, the audit, a log or an error.
"""
import asyncio
import re
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StrictInt, ValidationError
from app.core.config import settings
from app.services import audit

HOLIDAYS_HOST, FX_HOST, ZOOM_HOST = 'date.nager.at', 'api.frankfurter.dev', 'api.zoom.us'
HOLIDAYS_TTL_S, FX_TTL_S, ZOOM_TTL_S = 24*3600, 60, 60
COUNTRY, CURRENCY = re.compile(r'[A-Z]{2}'), re.compile(r'[A-Z]{3}')
SGT = ZoneInfo('Asia/Singapore')
CACHE: dict[tuple, tuple[float, Any]] = {}


class ExternalError(RuntimeError):
    """An external tool could not answer: bad_input, host_not_allowed, not_found, timeout, unavailable, bad_response,
    not_configured or not_authorised."""
    def __init__(self, tool: str, reason: str):
        super().__init__(f'{tool}: {reason}')
        self.tool, self.reason = tool, reason


class Holiday(BaseModel):
    model_config = ConfigDict(extra='ignore', populate_by_name=True)
    date: date
    name: str = Field(max_length=200)
    local_name: str = Field(alias='localName', max_length=200)
    national: bool = Field(alias='global')  # false: only some regions (see `counties`)
    counties: list[str] | None = Field(default=None, max_length=100)
    types: list[str] = Field(default=[], max_length=10)


class ZoomMeeting(BaseModel):
    """Only what the check compares: names, emails, join URLs and passwords in Zoom's reply are never parsed."""
    model_config = ConfigDict(extra='ignore', hide_input_in_errors=True)
    id: StrictInt
    topic: str = Field(max_length=300)
    start_time: AwareDatetime
    duration: StrictInt = Field(ge=0, le=1440)
    status: str = Field(max_length=20)


class Rates(BaseModel):
    model_config = ConfigDict(extra='ignore')
    base: str = Field(pattern=r'^[A-Z]{3}$')
    date: date
    rates: dict[str, float]


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


def this_year() -> int:
    return datetime.now(SGT).year


async def public_holidays(country: str, year: int | None = None) -> dict[str, Any]:
    """Public holidays in one country for one year (this year ±1; default this year). National holidays and regional ones are marked."""
    year = this_year() if year is None else year
    if not (isinstance(country, str) and COUNTRY.fullmatch(country)):raise ExternalError('public_holidays', 'bad_input')
    if not (isinstance(year, int) and abs(year-this_year()) <= 1):raise ExternalError('public_holidays', 'bad_input')
    holidays = await fetch('public_holidays', HOLIDAYS_HOST, f'/api/v3/PublicHolidays/{year}/{country}', {}, HOLIDAYS_TTL_S,
        lambda body: [Holiday.model_validate(item) for item in body[:100]])
    return {'country': country, 'year': year, 'source': 'Nager.Date public holidays',
        'items': [h.model_dump(mode='json') for h in holidays]}


async def fx_rate(base: str, quote: str) -> dict[str, Any]:
    """Indicative ECB reference rate for 1 `base` in `quote` (via Frankfurter), with its date. Never a card rate."""
    if not all(isinstance(c, str) and CURRENCY.fullmatch(c) for c in (base, quote)) or base == quote:
        raise ExternalError('fx_rate', 'bad_input')
    rates = await fetch('fx_rate', FX_HOST, '/v1/latest', {'base': base, 'symbols': quote}, FX_TTL_S, Rates.model_validate)
    if rates.base != base or quote not in rates.rates:raise ExternalError('fx_rate', 'bad_response')
    return {'base': base, 'quote': quote, 'rate': rates.rates[quote], 'date': rates.date.isoformat(),
        'source': 'Indicative ECB reference rate (Frankfurter)', 'indicative': True}


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


TOOLS = {'public_holidays': public_holidays, 'fx_rate': fx_rate}  # public data, codes only (registry.PUBLIC_EXTERNAL)
