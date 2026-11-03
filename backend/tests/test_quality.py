"""Team-only Quality page: role gate through the real gateway and JWT checks (locally minted tokens, as in
test_gateway), and the mapping from LangSmith feedback stats. LangSmith reads are replaced only in the gate tests."""
from datetime import datetime,timezone
from app.schemas.quality import Check,Experiment
from app.services import quality
from tests.test_gateway import bearer,client,code,env  # noqa: F401  (env is the autouse JWKS/database fixture)

EXPERIMENT=Experiment(name='demobank-qa1-client-v2-abc',dataset='demobank-qa1-client',started_at=datetime(2026,10,31,tzinfo=timezone.utc))


def test_only_team_roles_from_app_metadata_can_read(monkeypatch):
    monkeypatch.setattr(quality,'experiments',lambda limit:[EXPERIMENT])
    assert code(client.get('/quality/experiments'))=='auth_required'
    assert code(client.get('/quality/experiments',headers=bearer()))=='team_only'
    assert code(client.get('/quality/experiments',headers=bearer(user_metadata={'demobank_role':'rm'})))=='team_only'
    assert code(client.get('/quality/experiments',headers=bearer(app_metadata={'demobank_role':'customer'})))=='team_only'
    response=client.get('/quality/experiments',headers=bearer(app_metadata={'demobank_role':'rm'}))
    assert response.status_code==200 and response.json()[0]['name']==EXPERIMENT.name


def test_langsmith_failure_is_reported_and_names_are_bounded(monkeypatch):
    def down(limit):raise ConnectionError('down')
    monkeypatch.setattr(quality,'experiments',down)
    team=bearer(app_metadata={'demobank_role':'support'})
    assert code(client.get('/quality/experiments',headers=team))=='quality_unavailable'
    assert code(client.get('/quality/experiments/someone-elses-project',headers=team))=='not_found'
    assert client.get('/quality/traces/not-a-uuid',headers=team).status_code==422


def test_feedback_stats_become_checks_and_a_status():
    stats={'nodes':{'avg':0.0,'comments':['did not act: settle_actions']},'completed':{'avg':1.0,'comments':['2 turns']},
        'judge_rubric':{'avg':0.5,'comments':[]}}
    checks=quality.checks_of(stats)
    assert checks==[Check(key='completed',score=1.0,comment='2 turns'),Check(key='judge_rubric',score=0.5),
        Check(key='nodes',score=0.0,comment='did not act: settle_actions')]
    assert quality.status(checks,None)=='fail' and quality.status(checks[:2],None)=='pass'
    assert quality.status([Check(key='judge_rubric',score=0)],None)=='skipped' and quality.status(checks,'Boom')=='error'
