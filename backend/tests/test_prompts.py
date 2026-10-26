import asyncio
from pathlib import Path
import pytest
from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.services import answers, prompts

def test_load_known_and_unknown_versions():
    pv=prompts.load('answer','v1')
    assert pv['id'].startswith('v1+') and len(pv['id'])==11 and pv['agent']=='answer'
    assert 'DemoBank' in pv['system'] and 'PUBLIC FAQ TEXT ONLY' in pv['public_only']
    for agent,bad in (('answer','v999'),('answer','../x'),('answer','latest'),('../answer','v1'),('nobody','v1')):
        with pytest.raises(ValueError):prompts.load(agent,bad)

def test_v2_is_separate_and_fixed_guidance_has_no_prompt():
    assert prompts.load('answer','v2')['id']!=prompts.load('answer','v1')['id']
    fixed=asyncio.run(answers.make_answer(ChatRequest(messages=[{'role':'user','content':'I lost my card'}])))
    assert fixed['prompt_version'] is None and fixed['response_origin']=='safety'

# Released prompts are immutable: a changed id means a released file was edited.
RELEASED={('answer','v1'):'v1+855ee8ff',('answer','v2'):'v2+88f886d0'}
RELEASED.update({('domain_common','v1'):'v1+18c3cd30',('planner','v1'):'v1+5ef4e2b9',('retail','v1'):'v1+6bfcff22',
    ('retail','v2'):'v2+224affe7',('fraud','v1'):'v1+26e57492',('fraud','v2'):'v2+f78df743',('compliance','v1'):'v1+92455942',('compliance','v2'):'v2+88098141',
    ('document','v1'):'v1+754f2c43',('planner','v2'):'v2+6389a2e1',('retail','v3'):'v3+1b376761',('relationship','v1'):'v1+6c756adf',('relationship','v2'):'v2+6ed24a45',('relationship','v3'):'v3+536e3f12'})
RELEASED[('fraud','v3')]='v3+75382468'
RELEASED[('document','v2')]='v2+d6e35152'
RELEASED[('document','v3')]='v3+c2b4a08b'
RELEASED[('document_check','v1')]='v1+8d9e2c2c'
RELEASED[('document_check','v2')]='v2+2858097b'
RELEASED[('document_check','v3')]='v3+53ed74b1'
RELEASED[('notice_requirements','v1')]='v1+5d973f56'
RELEASED[('notice_requirements','v2')]='v2+c556c910'
RELEASED[('document_evidence','v1')]='v1+a6d0d1ed'
RELEASED[('document_evidence','v2')]='v2+4d5d0060'

def test_released_prompt_ids_never_change():
    for (agent,version),pid in RELEASED.items():assert prompts.load(agent,version)['id']==pid

def test_every_prompt_version_has_a_prompt_log_entry():
    log=Path(__file__).resolve().parents[2]/'docs'/'prompt-log'
    assert set(RELEASED)<=set(prompts.released())
    for agent,version in prompts.released():assert (log/agent/f'{version}.md').is_file(),(agent,version)

def test_agent_versions_are_configured_not_guessed(monkeypatch):
    monkeypatch.setattr(settings,'prompt_version','v1')
    assert prompts.current()['id']==RELEASED[('answer','v1')]
    monkeypatch.setattr(settings,'prompt_versions',{})
    with pytest.raises(ValueError):prompts.current('planner')
