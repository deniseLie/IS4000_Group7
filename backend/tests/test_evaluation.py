import asyncio
import json
import pytest
from evaluation.run import evaluate, dataset_path, model_details, write_report

def test_offline_evaluation_uses_publisher_metadata():
    report=asyncio.run(evaluate())
    rows={r['id']:r for r in report['results']}
    assert rows['external-policy-attribution']['validation_reason']=='source_check'
    assert rows['external-policy-attribution']['status']=='pass'
    assert rows['external-publisher-explanation']['status']=='pass'
    assert report['model_config']=={'transport':'none','model':None}

def test_focused_dataset_excludes_deferred_cases():
    cases=json.loads(dataset_path(True,'focused').read_text())
    assert {c['id'] for c in cases}=={'minimum-payment','unknown-product-fee','publisher-scope'}
    with pytest.raises(ValueError):dataset_path(False,'focused')

def test_model_metadata_identifies_transport_without_credentials(monkeypatch,tmp_path):
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    monkeypatch.setenv('CODEX_MODEL','gpt-5.6-luna')
    monkeypatch.setenv('LLM_API_KEY','synthetic-key-must-not-appear')
    monkeypatch.setenv('LLM_BASE_URL','https://private-user:synthetic-secret@example.invalid')
    details=model_details(True)
    assert details=={'transport':'codex','model':'gpt-5.6-luna','provider':'codex-cli'}
    report={'mode':'live_manual_review','created_at':'test','suite':'focused','model_config':details,'summary':{},'limits':[],'results':[]}
    write_report(report,tmp_path/'report.json')
    for path in tmp_path.iterdir():
        content=path.read_text()
        assert 'gpt-5.6-luna' in content and 'synthetic' not in content and 'private-user' not in content
    monkeypatch.setenv('CHAT_TRANSPORT','api')
    monkeypatch.setenv('LLM_MODEL','openrouter/free')
    assert model_details(True)['model']=='openrouter/free'
