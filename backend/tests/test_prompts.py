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

def test_v2_is_separate_and_fixed_guidance_has_no_prompt():
    assert prompts.load('v2')['id']!=prompts.load('v1')['id']
    fixed=asyncio.run(answers.make_answer(ChatRequest(messages=[{'role':'user','content':'I lost my card'}])))
    assert fixed['prompt_version'] is None and fixed['response_origin']=='safety'
