"""External read-only tools (build plan phase 2). Unit tests answer HTTP with saved real responses
(tests/fixtures/external) and check inputs, hosts, failures and audit; TEST_EXTERNAL_LIVE=1 makes one real call to each API."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
from app.core.config import settings
from app.schemas.contracts import Principal, RequestContext
from app.services import audit, external, tools
from app.services.agents.registry import EXTERNAL, PUBLIC_EXTERNAL, CHAT_SPECS
from app.services.authorization import PolicyDenied

SAMPLES=Path(__file__).parent/'fixtures'/'external'
HOLIDAYS=json.loads((SAMPLES/'nager_jp_2026.json').read_text(encoding='utf-8'))
RATES=json.loads((SAMPLES/'frankfurter_sgd_jpy.json').read_text(encoding='utf-8'))


@pytest.fixture
def served(monkeypatch):
    """Answer external requests from `routes` (url path -> (status, body or exception)) and record what was sent."""
    routes,sent={},[]
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        status,body=routes[request.url.path]
        if isinstance(body,Exception):raise body
        return httpx.Response(status,json=body) if not isinstance(body,str) else httpx.Response(status,text=body)
    real=httpx.AsyncClient
    monkeypatch.setattr(external.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    monkeypatch.setattr(external,'this_year',lambda:2026)
    external.CACHE.clear()
    yield routes,sent
    external.CACHE.clear()


def run(coro):
    batch=audit.start_batch(session_id=uuid4())
    try:return asyncio.run(coro),batch.events
    finally:audit.current_batch.set(None)


def test_holidays_parse_the_saved_response_and_are_cached(served):
    routes,sent=served
    routes['/api/v3/PublicHolidays/2026/JP']=(200,HOLIDAYS)
    result,events=run(external.public_holidays('JP',2026))
    assert result['country']=='JP' and len(result['items'])==len(HOLIDAYS)
    assert result['items'][0]=={'date':'2026-01-01','name':"New Year's Day",'local_name':'元日','national':True,'counties':None,'types':['Public']}
    assert sent[0].url.host=='date.nager.at' and sent[0].url.scheme=='https'
    assert [(e.action,e.outcome,e.details['tool'],e.details['host'],e.details['status']) for e in events]==[
        ('data.external_called','success','public_holidays','date.nager.at',200)]
    assert audit.is_safe(events[0].details)
    again,events=run(external.public_holidays('JP',2026))
    assert again==result and len(sent)==1 and events==[]  # cached: no second call, nothing to audit


def test_fx_rate_is_indicative_and_dated(served):
    routes,sent=served
    routes['/v1/latest']=(200,RATES)
    result,_=run(external.fx_rate('SGD','JPY'))
    assert result=={'base':'SGD','quote':'JPY','rate':RATES['rates']['JPY'],'date':RATES['date'],
        'source':'Indicative ECB reference rate (Frankfurter)','indicative':True}
    assert sent[0].url.host=='api.frankfurter.dev' and dict(sent[0].url.params)=={'base':'SGD','symbols':'JPY'}


@pytest.mark.parametrize('call',[lambda:external.public_holidays('jp',2026),lambda:external.public_holidays('Japan',2026),
    lambda:external.public_holidays('SG 1234 5678',2026),lambda:external.public_holidays('JP',2030),lambda:external.public_holidays('JP','2026'),
    lambda:external.fx_rate('SGD','SGD'),lambda:external.fx_rate('sgd','JPY'),lambda:external.fx_rate('SGD','4111111111111111')])
def test_bad_codes_are_refused_before_any_request(served,call):
    _,sent=served
    with pytest.raises(external.ExternalError,match='bad_input'):run(call())
    assert sent==[]


def test_only_allow_listed_hosts_are_called(served,monkeypatch):
    _,sent=served
    monkeypatch.setattr(settings,'external_hosts','api.frankfurter.dev')
    with pytest.raises(external.ExternalError,match='host_not_allowed'):run(external.public_holidays('SG',2026))
    assert sent==[]


@pytest.mark.parametrize('status,body,reason',[(0,httpx.ReadTimeout('slow'),'timeout'),(0,httpx.ConnectError('down'),'unavailable'),
    (503,{'error':'busy'},'unavailable'),(404,{'title':'Unknown country code'},'not_found'),(301,'moved','unavailable'),
    (200,'<html>not json</html>','bad_response'),(200,{'unexpected':True},'bad_response')])
def test_failures_are_typed_audited_and_not_cached(served,status,body,reason):
    routes,sent=served
    routes['/api/v3/PublicHolidays/2026/SG']=(status,body)
    with pytest.raises(external.ExternalError,match=reason) as error:run(external.public_holidays('SG',2026))
    assert error.value.tool=='public_holidays' and error.value.reason==reason
    assert not external.CACHE
    routes['/api/v3/PublicHolidays/2026/SG']=(200,HOLIDAYS)
    assert run(external.public_holidays('SG',2026))[0]['items']  # a later call tries again


def test_failed_calls_are_audited_as_failed(served):
    routes,_=served
    routes['/v1/latest']=(503,{})
    batch=audit.start_batch(session_id=uuid4())
    try:
        with pytest.raises(external.ExternalError):asyncio.run(external.fx_rate('SGD','JPY'))
        assert [(e.action,e.outcome,e.details['status']) for e in batch.events]==[('data.external_called','failed',503)]
    finally:audit.current_batch.set(None)


def test_agents_reach_only_the_external_tools_in_their_spec(served):
    routes,_=served
    routes['/v1/latest']=(200,RATES)
    assert all(spec.external<=EXTERNAL for spec in CHAT_SPECS.values()) and PUBLIC_EXTERNAL==set(external.TOOLS)<=set(tools.TOOLS) and EXTERNAL<=set(tools.TOOLS)
    ctx=lambda agent:RequestContext(session_id=uuid4(),agent=agent,principal=Principal(kind='customer',user_id=uuid4(),customer_id=1001,jwt='synthetic'))
    result,_=run(tools.call('fx_rate',{'base':'SGD','quote':'JPY'},ctx('accounts')))
    assert result['rate']==RATES['rates']['JPY']
    with pytest.raises(PolicyDenied,match='agent_tool_forbidden'):run(tools.call('fx_rate',{'base':'SGD','quote':'JPY'},ctx('rm_requests')))


@pytest.mark.skipif(os.getenv('TEST_EXTERNAL_LIVE')!='1',reason='set TEST_EXTERNAL_LIVE=1 for one real call to each API')
def test_live_apis_answer():
    external.CACHE.clear()
    year=external.this_year()
    holidays,_=run(external.public_holidays('SG',year))
    assert holidays['items'] and all(h['date'].startswith(str(year)) for h in holidays['items'])
    rate,_=run(external.fx_rate('SGD','JPY'))
    assert rate['rate']>0 and rate['indicative']
