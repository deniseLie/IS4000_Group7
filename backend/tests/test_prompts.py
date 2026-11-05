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
RELEASED[('fraud','v4')]='v4+a6b2bd0c'
RELEASED[('retail','v4')]='v4+3fa65f65'
RELEASED[('fraud','v5')]='v5+3ef7cab6'
RELEASED[('fraud','v6')]='v6+72e1baf2'
RELEASED[('fraud','v7')]='v7+264be4b4'
RELEASED[('retail','v5')]='v5+378f6d71'
RELEASED[('document','v2')]='v2+d6e35152'
RELEASED[('document','v3')]='v3+c2b4a08b'
RELEASED[('document_check','v1')]='v1+8d9e2c2c'
RELEASED[('document_check','v2')]='v2+2858097b'
RELEASED[('document_check','v3')]='v3+53ed74b1'
RELEASED[('notice_requirements','v1')]='v1+5d973f56'
RELEASED[('notice_requirements','v2')]='v2+c556c910'
RELEASED[('document_evidence','v1')]='v1+a6d0d1ed'
RELEASED[('document_evidence','v2')]='v2+4d5d0060'
RELEASED[('answer','v3')]='v3+7ebdfbfd'
RELEASED[('compliance','v3')]='v3+30458b0b'
RELEASED[('compliance','v4')]='v4+7bad1a5e'
RELEASED[('compliance','v5')]='v5+9fcb70cc'
RELEASED[('retail','v6')]='v6+c4aaa7a7'
RELEASED[('retail','v7')]='v7+bf9e632d'
RELEASED[('document','v4')]='v4+f7e7c0f1'
RELEASED[('document','v5')]='v5+b7244f35'
RELEASED[('relationship','v4')]='v4+2c8aec82'
RELEASED[('relationship','v5')]='v5+82c591e9'
RELEASED[('rm_planner','v1')]='v1+b780c1e3'
RELEASED[('rm_assistant','v1')]='v1+162b341f'
RELEASED[('rm_planner','v2')]='v2+baed2fc1'
RELEASED[('rm_planner','v3')]='v3+2998b199'
RELEASED[('rm_planner','v4')]='v4+6a9deeba'
RELEASED[('rm_assistant','v3')]='v3+830de048'
RELEASED[('rm_assistant','v4')]='v4+83cc3e8b'
RELEASED[('rm_assistant','v2')]='v2+bdcb8a3f'
RELEASED[('rm_planner','v5')]='v5+dd5370bc'  # build plan 3b: the today intent
RELEASED[('rm_assistant','v5')]='v5+6738eb5a'  # build plan 3b: today.md, ranked priorities.md
RELEASED[('accounts','v1')]='v1+872a384c'
RELEASED[('runner_common','v1')]='v1+0c95684c'
RELEASED[('guide','v1')]='v1+3948ad35'
RELEASED[('security','v1')]='v1+fb828ecf'
RELEASED[('guide','v2')]='v2+20d159f2'  # build plan 2b: login, OTP and phishing move to Guide
RELEASED[('planner','v9')]='v9+a4b3c8fd'  # 2a meeting_request line (from v8) + build plan 2b: four client agents, no Security intents
RELEASED[('planner','v6')]='v6+e8bc7007'
RELEASED[('planner','v7')]='v7+0590901b'
RELEASED[('planner','v8')]='v8+2710b2ab'
RELEASED[('relationship','v6')]='v6+eb7307b9'
RELEASED[('relationship','v7')]='v7+e690200b'  # build plan 6e (consent v2): Data sharing explained, changes offered by rules
RELEASED[('talking_points','v1')]='v1+254803bd'  # build plan 4 (0108): lane 1 classifier

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


def test_prompts_in_use_are_released_versions():
    """The admin dashboard's "live" version per family: every one exists on disk, and the v5 runner agents are included."""
    used=prompts.in_use()
    assert set(used.items())<=set(prompts.released())
    assert {'planner','guide','accounts','runner_common','rm_planner','rm_assistant'}<=set(used)
    assert not {'security','fraud'}&set(used)  # build plan 2b: no Security agent, and the fraud v7 review no longer runs
