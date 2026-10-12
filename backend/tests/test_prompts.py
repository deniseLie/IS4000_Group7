import asyncio
import pytest
from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.services import answers, prompts

def test_load_known_and_unknown_versions():
    pv=prompts.load('v1')
    assert pv['id'].startswith('v1+') and len(pv['id'])==11
    assert 'DemoBank' in pv['system'] and 'PUBLIC FAQ TEXT ONLY' in pv['public_only']
    for bad in ('v999','../x','latest'):
        with pytest.raises(ValueError):prompts.load(bad)

def test_make_answer_records_prompt_version(monkeypatch):
    async def fake(*args):return '{"answer":"Cards are explained in the guide.","source_ids":["cards"]}'
    monkeypatch.setattr(answers.llm,'answer',fake)
    fixed=asyncio.run(answers.make_answer(ChatRequest(messages=[{'role':'user','content':'I lost my card'}])))
    assert fixed['prompt_version'] is None
    monkeypatch.setattr(settings,'prompt_version','v1')
    llm_result=asyncio.run(answers.make_answer(ChatRequest(messages=[{'role':'user','content':'What credit card fees exist?'}])))
    assert llm_result['prompt_version']==prompts.load('v1')['id']
