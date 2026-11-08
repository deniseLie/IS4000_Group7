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
RELEASED[('rm_assistant','v6')]='v6+77a77a52'  # RM answer speed: code writes the client reports' record sections (the live runs' v6+251d64ae and v6+6a16d22f were drafts before the actions.md changes)
RELEASED[('rm_assistant','v7')]='v7+9edeb2a0'  # help is a plain chat reply; follow_up explains the previous answer
RELEASED[('accounts','v1')]='v1+872a384c'
RELEASED[('runner_common','v1')]='v1+0c95684c'
RELEASED[('runner_common','v2')]='v2+659e0d0c'  # batch 2 (C12): never restate a figure from her own message
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
RELEASED[('talking_points','v2')]='v2+63a30afe'  # lane 1 v2 (0143): state-aware, carry-over, kind, on hold
RELEASED[('planner','v10')]='v10+67067378'  # batch 3: a question needing another agent's data is two tasks; imports; threshold
RELEASED[('compliance','v7')]='v7+51624f5a'  # batch 3 (P05): her account list from Accounts is citable

RELEASED[('minutes_writer','v3')]='v3+94a92997'
RELEASED[('minutes_writer','v4')]='v4+5dec283d'
RELEASED[('minutes_overview','v1')]='v1+6138bb95'
RELEASED[('minutes_overview','v2')]='v2+69bbc872'
RELEASED[('brief_writer','v1')]='v1+25e786cb'
RELEASED[('brief_writer','v2')]='v2+74f98536'
RELEASED[('brief_writer','v3')]='v3+8ff73d94'
RELEASED[('brief_writer','v4')]='v4+03eb16f4'  # storyline: recorded dates, funding-options question (0140)

# 8 Nov 2026: the product is named UBS Connect; each family below is its previous version with "DemoBank" renamed, nothing else.
RELEASED[('answer','v4')]='v4+7c757bc5'
RELEASED[('planner','v11')]='v11+e5506594'
RELEASED[('guide','v3')]='v3+3eec8ebc'
RELEASED[('accounts','v2')]='v2+50c6b0ab'
RELEASED[('runner_common','v3')]='v3+9cc8694c'
RELEASED[('retail','v12')]='v12+e2b249d0'
RELEASED[('compliance','v8')]='v8+4c71d46e'
RELEASED[('relationship','v8')]='v8+3ac41170'
RELEASED[('document','v6')]='v6+d4f5ed2b'
RELEASED[('rm_planner','v6')]='v6+08f9a7ff'
RELEASED[('rm_assistant','v8')]='v8+ea931216'
# 8 Nov 2026: the public holiday and FX tools are removed; each is its previous version without their lines.
RELEASED[('accounts','v3')]='v3+ccdade61'
RELEASED[('guide','v4')]='v4+eb11931a'
RELEASED[('planner','v12')]='v12+77ebe779'

def test_released_prompt_ids_never_change():
    for (agent,version),pid in RELEASED.items():assert prompts.load(agent,version)['id']==pid

def test_every_prompt_version_has_a_prompt_log_entry():
    log=Path(__file__).resolve().parents[2]/'docs'/'prompt-log'
    assert set(RELEASED)<=set(prompts.released())
    for agent,version in prompts.released():assert (log/agent/f'{version}.md').is_file(),(agent,version)

def test_every_version_is_chosen_in_one_table():
    """No setting picks a prompt; prompts.VERSIONS is the one table, and in_use adds only the meeting side's own pins."""
    from app.services import llm
    assert not {'prompt_version','prompt_versions'}&set(type(settings).model_fields)
    assert llm.answer_prompt()==prompts.current('answer')==prompts.load('answer',prompts.VERSIONS['answer'])
    used=prompts.in_use()
    assert {k:used[k] for k in prompts.VERSIONS}==prompts.VERSIONS
    assert set(used)-set(prompts.VERSIONS)=={'minutes_writer','minutes_overview','brief_writer','proposal_drafter'}
    with pytest.raises(ValueError):prompts.current('fraud')  # retired (build plan 2b): not in the table


def test_prompts_in_use_are_released_versions():
    """The admin dashboard's "live" version per family: every one exists on disk, and the v5 runner agents are included."""
    used=prompts.in_use()
    assert set(used.items())<=set(prompts.released())
    assert {'planner','guide','accounts','runner_common','rm_planner','rm_assistant'}<=set(used)
    assert not {'security','fraud'}&set(used)  # build plan 2b: no Security agent, and the fraud v7 review no longer runs
