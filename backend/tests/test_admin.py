"""Admin dashboard: role gate through the real gateway and JWT checks (locally minted tokens, as in test_gateway), and the
mapping from LangSmith feedback stats. LangSmith reads are replaced only in the gate tests."""
from datetime import datetime,timezone
import os
import pytest
from app.schemas.quality import Check,Experiment
from app.services import quality
from tests.test_gateway import bearer,client,code,env  # noqa: F401  (env is the autouse JWKS/database fixture)

EXPERIMENT=Experiment(name='demobank-qa1-client-v2-abc',dataset='demobank-qa1-client',started_at=datetime(2026,10,31,tzinfo=timezone.utc))
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
    monkeypatch.setattr(quality,'experiments',lambda limit:[EXPERIMENT])
    assert code(client.get('/admin/experiments'))=='auth_required'
    assert code(client.get('/admin/experiments',headers=bearer()))=='admin_only'
    assert code(client.get('/admin/experiments',headers=bearer(user_metadata=ADMIN)))=='admin_only'
    for role in ('customer','rm','support'):
        assert code(client.get('/admin/experiments',headers=bearer(app_metadata={'demobank_role':role,'rm_id':'00000000-0000-4000-8000-100200000002'})))=='admin_only'
    response=client.get('/admin/experiments',headers=bearer(app_metadata=ADMIN))
    assert response.status_code==200 and response.json()[0]['name']==EXPERIMENT.name


def test_the_quality_page_routes_are_gone():
    assert client.get('/quality/experiments',headers=bearer(app_metadata={'demobank_role':'rm'})).status_code==404


def test_an_admin_is_not_a_relationship_manager():
    assert code(client.get('/rm/me',headers=bearer(app_metadata=ADMIN)))=='rm_only'


def test_langsmith_failure_is_reported_and_names_are_bounded(monkeypatch):
    def down(limit):raise ConnectionError('down')
    monkeypatch.setattr(quality,'experiments',down)
    admin=bearer(app_metadata=ADMIN)
    assert code(client.get('/admin/experiments',headers=admin))=='admin_unavailable'
    assert code(client.get('/admin/experiments/someone-elses-project',headers=admin))=='not_found'
    assert client.get('/admin/traces/not-a-uuid',headers=admin).status_code==422
    assert client.get('/admin/experiments?limit=500',headers=admin).status_code==422


def test_feedback_stats_become_checks_and_a_status():
    stats={'nodes':{'avg':0.0,'comments':['did not act: settle_actions']},'completed':{'avg':1.0,'comments':['2 turns']},
        'judge_rubric':{'avg':0.5,'comments':[]}}
    checks=quality.checks_of(stats)
    assert checks==[Check(key='completed',score=1.0,comment='2 turns'),Check(key='judge_rubric',score=0.5),
        Check(key='nodes',score=0.0,comment='did not act: settle_actions')]
    assert quality.status(checks,None)=='fail' and quality.status(checks[:2],None)=='pass'
    assert quality.status([Check(key='judge_rubric',score=0)],None)=='skipped' and quality.status(checks,'Boom')=='error'
