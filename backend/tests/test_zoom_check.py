"""Read-only Zoom meeting check for the RM (tools.zoom_meeting_check, external.zoom_meeting, migration 0150).

HTTP is answered from tests/fixtures/external/zoom_meeting_waiting.json, shaped like Zoom's documented GET /v2/meetings/{id}
reply with synthetic values (no response has been captured from the lab account yet; replace it with a saved real one,
secrets replaced, once TEST_ZOOM_LIVE=1 has run). Our own record read (rm_zoom_check) is answered by a recorded sample here;
its SQL is checked natively in test_zoom_check_db.py. TEST_ZOOM_LIVE=1 with ZOOM_OAUTH_DIRECTORY and TEST_ZOOM_MEETING_NUMBER
makes one real Zoom read.
"""
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID,uuid4
import httpx
import pytest
from pydantic import SecretStr
from app.core.config import settings
from app.schemas.contracts import Principal,RequestContext
from app.services import audit,external,tools
from app.services.agents.registry import CHAT_SPECS,EXTERNAL,RM_CALENDAR,ZOOM_EXTERNAL
from app.services.authorization import PolicyDenied
from app.services.rm import records as rm_records
from app.services.rm.records import RmDenied

MEETING=json.loads((Path(__file__).parent/'fixtures'/'external'/'zoom_meeting_waiting.json').read_text(encoding='utf-8'))
APPOINTMENT=UUID('00000000-0000-4000-8000-100200000081')  # the fixture's topic names this appointment
NUMBER=MEETING['id']
TOKEN='synthetic-bearer-token-never-shown'
SECRETS=('join_url','synthetic-join-secret','synthetic-start-secret','syn123','zoom.host@example.com','sophie.lim@example.com',
    'synthetic-host-id-0001','Vm9TZXN0VXVpZDEyMw',str(NUMBER),TOKEN,'Home loan')


def record(link: str='set_up',starts: str='2026-11-12T10:00:00+08:00',ends: str='2026-11-12T10:30:00+08:00') -> dict:
    """rm_zoom_check's reply (0150): the booked time, link state and, for the tool only, the Zoom meeting number."""
    return {'denied':False,'customer_id':1002,'call_id':str(uuid4()),'as_of':'2026-11-08T12:00:00+08:00','data':{'meeting':'Thu 12 Nov 2026, 10:00 am',
        'starts_at':starts,'ends_at':ends,'format':'video','status':'confirmed','link':link,'provider_id':NUMBER if link!='none' else None}}


def rm_ctx(agent: str|None=None,kind: str='customer') -> RequestContext:
    return RequestContext(session_id=uuid4(),agent=agent,principal=Principal(kind=kind,user_id=uuid4(),jwt='synthetic-rm-jwt' if kind=='customer' else None))


@pytest.fixture
def zoom(monkeypatch):
    """Zoom answered from `routes` (path -> (status, body or exception)); our record read from `state['record']`."""
    routes,sent,state={},[],{'record':record(),'reads':[]}
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        status,body=routes[request.url.path]
        if isinstance(body,Exception):raise body
        return httpx.Response(status,json=body)
    real=httpx.AsyncClient
    monkeypatch.setattr(external.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    async def token(tool):return SecretStr(TOKEN)
    monkeypatch.setattr(external,'zoom_token',token)
    async def check(ctx,appointment_id):
        state['reads'].append(appointment_id)
        if isinstance(state['record'],Exception):raise state['record']
        return state['record']
    monkeypatch.setattr(rm_records,'zoom_check',check)
    external.CACHE.clear()
    yield routes,sent,state
    external.CACHE.clear()


def run(coro):
    batch=audit.start_batch(session_id=uuid4())
    try:return asyncio.run(coro),batch.events
    finally:audit.current_batch.set(None)


def direct(ctx: RequestContext|None=None) -> tuple[dict,list]:
    async def go():
        token=tools.TOOL_CTX.set(ctx or rm_ctx())
        try:return await tools.zoom_meeting_check(APPOINTMENT)
        finally:tools.TOOL_CTX.reset(token)
    return run(go())


def test_a_linked_meeting_is_read_from_zoom_with_the_bank_login_and_returns_codes_only(zoom):
    routes,sent,_=zoom
    routes[f'/v2/meetings/{NUMBER}']=(200,MEETING)
    result,events=direct()
    assert result['data']=={**result['data'],'meeting':'Thu 12 Nov 2026, 10:00 am','link':'set_up','zoom_time_matches':True,'call':'not_started','live':'ok'}
    assert set(result['data'])=={'meeting','format','status','link','zoom_time_matches','call','live','checked_at'}
    assert len(sent)==1 and sent[0].url.host=='api.zoom.us' and sent[0].url.scheme=='https' and sent[0].headers['authorization']=='Bearer '+TOKEN
    text=json.dumps(result)+json.dumps([e.details for e in events])
    assert not any(s in text for s in SECRETS)  # no link, ids, names, emails, purpose or token in the result or the audit
    assert [(e.action,e.outcome,e.details['tool'],e.details['host'],e.details['status']) for e in events]==[
        ('data.external_called','success','zoom_meeting_check','api.zoom.us',200)] and isinstance(events[0].details['ms'],int)


def test_the_call_state_and_time_match_come_from_zoom(zoom,monkeypatch):
    routes,_,state=zoom
    routes[f'/v2/meetings/{NUMBER}']=(200,{**MEETING,'status':'started'})
    assert direct()[0]['data']['call']=='in_progress'
    external.CACHE.clear()
    routes[f'/v2/meetings/{NUMBER}']=(200,{**MEETING,'start_time':'2026-11-12T03:00:00Z'})  # moved in Zoom, not in our booking
    assert direct()[0]['data']['zoom_time_matches'] is False
    external.CACHE.clear()
    state['record']=record(starts='2026-10-01T10:00:00+08:00',ends='2026-10-01T10:30:00+08:00')  # booked time has passed
    routes[f'/v2/meetings/{NUMBER}']=(200,{**MEETING,'start_time':'2026-10-01T02:00:00Z'})
    assert direct()[0]['data']=={**direct()[0]['data'],'call':'not_running','zoom_time_matches':True}  # waiting: no call open now


@pytest.mark.parametrize('link',['none','stale'])
def test_no_zoom_request_without_a_current_link(zoom,link):
    _,sent,state=zoom
    state['record']=record(link)
    result,events=direct()
    assert not sent and not events and result['data']=={**result['data'],'link':link,'live':'not_checked','call':None,'zoom_time_matches':None}


def test_a_link_zoom_does_not_have_or_that_is_not_ours_is_missing_in_zoom(zoom):
    routes,_,_=zoom
    routes[f'/v2/meetings/{NUMBER}']=(404,{'code':3001,'message':'Meeting does not exist'})
    assert direct()[0]['data']=={**direct()[0]['data'],'link':'missing_in_zoom','live':'ok','call':None}
    external.CACHE.clear()
    routes[f'/v2/meetings/{NUMBER}']=(200,{**MEETING,'topic':'Team stand-up'})
    assert direct()[0]['data']['link']=='missing_in_zoom'


@pytest.mark.parametrize('answer,live,audited',[((401,{'code':124,'message':'Invalid access token'}),'not_authorised','failed'),
    ((400,{'code':4711,'message':'Invalid access token, does not contain scopes:[meeting:read:meeting, meeting:read:meeting:admin].'}),
     'not_authorised','failed'),  # the bank's grant without the read scope, as Zoom answered live on 9 Nov 2026
    ((400,{'code':300,'message':'Invalid meeting id.'}),'unavailable','failed'),((503,{}),'unavailable','failed'),((200,httpx.ReadTimeout('slow')),'unavailable','failed'),
    ((200,{'id':NUMBER}),'unavailable','success')])  # answered, but not the shape we read: never guessed from
def test_zoom_failures_are_short_codes_and_the_record_part_stays(zoom,answer,live,audited):
    routes,_,_=zoom
    routes[f'/v2/meetings/{NUMBER}']=answer
    result,events=direct()
    assert result['data']=={**result['data'],'link':'set_up','live':live,'call':None,'zoom_time_matches':None}
    assert [e.outcome for e in events]==[audited] and TOKEN not in json.dumps([e.details for e in events])


def test_zoom_is_read_once_a_minute_however_often_she_asks(zoom):
    routes,sent,state=zoom
    routes[f'/v2/meetings/{NUMBER}']=(200,MEETING)
    direct(),direct()
    assert len(sent)==1 and len(state['reads'])==2  # her book is checked on every call, before the cache


def test_without_a_zoom_login_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(settings,'zoom_oauth_directory',None)
    with pytest.raises(external.ExternalError,match='not_configured'):asyncio.run(external.zoom_token('zoom_meeting_check'))


def test_a_refresh_failure_never_carries_zoom_s_reply(monkeypatch,tmp_path):
    from app.services.meetings import zoom as zoom_client
    def refused(directory):raise zoom_client.ZoomFailure('Zoom read failed; response={"access_token":"leaked"}')
    monkeypatch.setattr(settings,'zoom_oauth_directory',tmp_path)
    monkeypatch.setattr(zoom_client,'refreshed_token',refused)
    with pytest.raises(external.ExternalError) as error:asyncio.run(external.zoom_token('zoom_meeting_check'))
    assert error.value.reason=='not_authorised' and 'leaked' not in str(error.value) and error.value.__cause__ is None


def test_the_check_needs_the_rm_s_own_sign_in_and_her_book(zoom):
    routes,sent,state=zoom
    with pytest.raises(PolicyDenied,match='rm_login_required'):direct(rm_ctx(kind='guest'))
    state['record']=RmDenied('This client is not in your book.')  # rm_zoom_check returned rm.deny (audited in SQL)
    with pytest.raises(RmDenied):run(tools.call('zoom_meeting_check',{'appointment_id':str(APPOINTMENT)},rm_ctx('rm_calendar')))
    assert not sent


def test_only_the_calendar_agent_may_call_it_through_mcp(zoom):
    routes,_,_=zoom
    routes[f'/v2/meetings/{NUMBER}']=(200,MEETING)
    assert EXTERNAL==ZOOM_EXTERNAL=={'zoom_meeting_check'}
    assert [n for n,s in CHAT_SPECS.items() if 'zoom_meeting_check' in s.external|s.loop_tools]==['rm_calendar'] and 'zoom_meeting_check' in RM_CALENDAR.loop_tools
    result,_=run(tools.call('zoom_meeting_check',{'appointment_id':str(APPOINTMENT)},rm_ctx('rm_calendar')))
    assert result['data']['link']=='set_up'
    for agent in ('rm_client','rm_requests','accounts','guide'):
        with pytest.raises(PolicyDenied,match='agent_tool_forbidden'):run(tools.call('zoom_meeting_check',{'appointment_id':str(APPOINTMENT)},rm_ctx(agent)))


def test_the_mcp_server_lists_it_as_read_only_and_open_world():
    import mcp
    from app import mcp_server
    async def listed():
        async with mcp.Client(mcp_server.build_server()) as client:return {t.name:t for t in (await client.list_tools()).tools}
    served=asyncio.run(listed())
    zoom,own=served['zoom_meeting_check'],served['rm_appointments']
    assert zoom.annotations.read_only_hint is True and zoom.annotations.open_world_hint is True and zoom.annotations.destructive_hint is False
    assert list(zoom.input_schema['properties'])==['appointment_id']  # never a Zoom number or link
    assert own.annotations.read_only_hint is True and own.annotations.open_world_hint is False
    assert not any(n.startswith('zoom_') and n!='zoom_meeting_check' for n in served)  # no Zoom write tool


@pytest.mark.skipif(os.getenv('TEST_ZOOM_LIVE')!='1',reason='set TEST_ZOOM_LIVE=1, ZOOM_OAUTH_DIRECTORY and TEST_ZOOM_MEETING_NUMBER for one real Zoom read')
def test_live_zoom_answers():
    external.CACHE.clear()
    meeting,events=run(external.zoom_meeting('zoom_meeting_check',int(os.environ['TEST_ZOOM_MEETING_NUMBER'])))
    assert meeting.status in ('waiting','started') and [e.outcome for e in events]==['success']
