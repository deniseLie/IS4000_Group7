"""Admin dashboard: role gate through the real gateway and JWT checks (locally minted tokens, as in test_gateway), and the
mapping from LangSmith feedback stats. LangSmith reads are replaced only in the gate tests."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import json
import os
import pytest
from app.schemas.quality import Check
from app.services import quality
from tests.test_gateway import bearer,client,code,env  # noqa: F401  (env is the autouse JWKS/database fixture)

EXPERIMENT={'id':'p1','name':'demobank-qa1-client-v2-abc','dataset':'demobank-qa1-client','started_at':datetime(2026,10,31,tzinfo=timezone.utc),
    'url':None,'model':'haiku','partial':None,'cases':[]}
ADMIN={'demobank_role':'admin'}


@pytest.mark.skipif(os.getenv('TEST_LANGSMITH_ADMIN_LIVE')!='1',reason='Opt in to real LangSmith read-back')
def test_admin_reads_the_actual_langsmith_project() -> None:
    """Every synced dataset is listed from LangSmith (graph and planner included) and an existing RM run reads back."""
    names={d['name'] for d in quality.datasets()}
    assert {'demobank-qa1-client','demobank-qa1-rm','demobank-graph','demobank-planner'}<=names
    expected=os.environ.get('TEST_LANGSMITH_RM_RUN_ID')
    if expected:
        rows=[row for row in quality.traces(30) if row.run_id==expected]
        assert len(rows)==1 and rows[0].mode=='rm'
        assert any(span.name=='rm_assistant' for span in quality.trace(expected))


def test_only_the_admin_role_from_app_metadata_can_read(monkeypatch):
    monkeypatch.setattr(quality,'experiment_runs',lambda:[EXPERIMENT])
    monkeypatch.setattr(quality,'datasets',lambda:[{'name':'demobank-qa1-client','id':'d1','examples':20}])
    assert code(client.get('/admin/evaluations'))=='auth_required'
    assert code(client.get('/admin/evaluations',headers=bearer()))=='admin_only'
    assert code(client.get('/admin/evaluations',headers=bearer(user_metadata=ADMIN)))=='admin_only'
    for role in ('customer','rm','support'):
        assert code(client.get('/admin/evaluations',headers=bearer(app_metadata={'demobank_role':role,'rm_id':'00000000-0000-4000-8000-100200000002'})))=='admin_only'
    response=client.get('/admin/evaluations',headers=bearer(app_metadata=ADMIN))
    assert response.status_code==200 and response.json()['datasets'][0]['timeline'][0]['experiment']==EXPERIMENT['name']


def test_the_quality_page_routes_are_gone():
    assert client.get('/quality/experiments',headers=bearer(app_metadata={'demobank_role':'rm'})).status_code==404


def test_an_admin_is_not_a_relationship_manager():
    assert code(client.get('/rm/me',headers=bearer(app_metadata=ADMIN)))=='rm_only'


def test_langsmith_failure_is_reported_and_names_are_bounded(monkeypatch):
    def down():raise ConnectionError('down')
    monkeypatch.setattr(quality,'experiment_runs',down)
    admin=bearer(app_metadata=ADMIN)
    assert code(client.get('/admin/evaluations',headers=admin))=='admin_unavailable'
    assert code(client.get('/admin/evaluations/someone-elses-project',headers=admin))=='not_found'
    assert client.get('/admin/evaluations/compare?a=x&b=demobank-a',headers=admin).status_code==422
    assert client.get('/admin/traces/not-a-uuid',headers=admin).status_code==422
    assert client.get('/admin/traces?limit=500',headers=admin).status_code==422
    assert client.get('/admin/prompts?window=1y',headers=admin).status_code==422


def test_feedback_stats_become_checks_and_a_status():
    stats={'nodes':{'avg':0.0,'comments':['did not act: settle_actions']},'completed':{'avg':1.0,'comments':['2 turns']},
        'judge_rubric':{'avg':0.5,'comments':[]}}
    checks=quality.checks_of(stats)
    assert checks==[Check(key='completed',score=1.0,comment='2 turns'),Check(key='judge_rubric',score=0.5),
        Check(key='nodes',score=0.0,comment='did not act: settle_actions')]
    assert quality.status(checks,None)=='fail' and quality.status(checks[:2],None)=='pass'
    assert quality.status([Check(key='judge_rubric',score=0)],None)=='skipped' and quality.status(checks,'Boom')=='error'


class Pages:
    """LangSmith's v2 run query as the HTTP client sees it: pages with a next_cursor (only the transport is replaced)."""
    def __init__(self,*pages):self.pages,self.bodies=list(pages),[]
    def request_with_retries(self,method,path,request_kwargs):
        assert (method,path)==('POST','/api/v2/runs/query')
        self.bodies.append(json.loads(request_kwargs['data']))
        return SimpleNamespace(json=lambda:self.pages.pop(0))


def run(rid,name,parents=(),**more):
    return {'id':rid,'name':name,'run_type':'chain','trace_id':(parents or [rid])[0],'parent_run_ids':list(parents),'project_id':'p1',
        'start_time':'2026-11-05T18:02:12.729295Z','latency_seconds':1.5,'status':'success','metadata':{'agent':'guide'},**more}


def test_the_v2_run_query_pages_up_to_its_cap_and_reads_like_list_runs(monkeypatch):
    pages=Pages({'items':[run('a','chat'),run('b','agent',['a'])],'next_cursor':'c1'},{'items':[run('c','llm',['a','b'])],'next_cursor':'c2'})
    monkeypatch.setattr(quality,'client',lambda:pages)
    runs=quality.query({'project_ids':['p1'],'min_start_time':'2026-11-01T00:00:00+00:00'},3)
    assert [r.id for r in runs]==['a','b','c'] and len(pages.bodies)==2  # stopped at the cap although page 2 has a next_cursor
    assert [b['page_size'] for b in pages.bodies]==[3,1] and 'cursor' not in pages.bodies[0] and pages.bodies[1]['cursor']=='c1'
    step=quality.as_step(runs[2])
    assert step['parent_id']=='b' and step['trace_id']=='a' and step['meta']=={'agent':'guide'} and step['latency_s']==1.5
    assert runs[0].start_time==datetime(2026,11,5,18,2,12,729295,tzinfo=timezone.utc) and runs[0].parent_run_id is None


def test_a_request_counts_the_tokens_of_the_model_runs_under_it():
    asked=[quality.as_run(run('a','chat')),quality.as_run(run('d','chat',metadata={'llm_tokens':70}))]
    models=[quality.as_run(run(i,'ChatOpenAI',['a','b'],run_type='llm',total_tokens=t)) for i,t in (('m1',100),('m2',23))]
    quality.with_tokens(asked,models+[quality.as_run(run('x','ChatOpenAI',['z'],run_type='llm',total_tokens=9))])
    assert [quality.as_request(r,'live')['tokens'] for r in asked]==[123,70]  # no model runs: the request's own llm_tokens


def test_a_shorter_window_is_cut_from_a_wider_one_still_cached(monkeypatch):
    monkeypatch.setattr(quality,'_cache',{})
    now=quality.now();old,recent={'started_at':now-timedelta(days=10)},{'started_at':now-timedelta(days=2)}
    monkeypatch.setattr(quality,'read_runs',lambda *a,**k:([old,recent],[old,recent]))
    monkeypatch.setattr(quality,'live_project',lambda:'p1')
    assert quality.live_runs(14)==([old,recent],[old,recent])
    monkeypatch.setattr(quality,'read_runs',lambda *a,**k:pytest.fail('read again'))
    assert quality.live_runs(7)==([recent],[recent]) and quality.requests('live',7)==[recent]


def test_a_quoted_v2_error_reads_as_its_text():
    quoted=json.dumps("LLMError('Model request failed')\n\nTraceback (most recent call last)")
    assert quality.as_run(run('a','agent',error=quoted)).error=="LLMError('Model request failed')\n\nTraceback (most recent call last)"
    assert quality.as_run(run('a','agent',error='CancelledError()')).error=='CancelledError()'
    assert quality.as_run(run('a','agent',latency_seconds=12.609659910202026)).latency==12.60966
