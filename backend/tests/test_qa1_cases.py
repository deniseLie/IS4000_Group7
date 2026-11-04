"""QA1 test cases (docs/evaluation/test-cases.md) as eval data: the template, the coverage rules and the links to real code."""
import asyncio
import json
import re
from pathlib import Path
from typing import get_args
import pytest
from app.schemas.chat import ChatRequest
from app.schemas.contracts import TargetAgent
from app.services import answers
from app.services.guardrail import assess_request
from app.services.orchestrator import build_graph
from evaluation import langsmith_sync as sync,targets

BACKEND=Path(__file__).parents[1]
DOC=(BACKEND.parent/'docs/evaluation/test-cases.md').read_text(encoding='utf-8')
SQL='\n'.join(p.read_text(encoding='utf-8') for p in (BACKEND/'supabase/migrations').glob('*.sql'))
CLIENT=sync.load('demobank-qa1-client')[0]
RM=sync.load('demobank-qa1-rm')[0]
ALL=CLIENT+RM
TYPES={'normal','guardrail','edge','attack'}
MUST_CLIENT={'CR1','CR2','CR3','CR4','CR5','CR6','CR8','CR9','CR10','CR11','CR12'}  # the Must table in test-cases.md
UBS=set(range(1,8))  # the seven UBS feedback points in the two-week plan

def test_ids_follow_the_template():
    assert [c['id'] for c in CLIENT]==[f'C{i:02}' for i in range(1,25)] and [c['id'] for c in RM]==[f'R{i:02}' for i in range(1,21)]
    for c in ALL:
        assert c['type'] in TYPES and c['priority'] in {'must','should'} and set(c['ubs'])<=UBS and c['turns'] and c['check'] and c['must_not']
        assert all(re.fullmatch(r'(CR|RM|F)\d+',s) for s in c['stories']) and all(len(t)==1 and set(t)<={'say','tap','form','upload','confirm_rm_handover'} for t in c['turns'])
        assert c['state'] in {'ready','manual'}
        assert c['backlog']['id']==c['id'] and c['owner']
        if c['state']=='manual':assert c['manual_reason']
    assert {c['persona'] for c in CLIENT}=={'guest','michelle'} and {c['persona'] for c in RM}=={'sophie'}

@pytest.mark.parametrize('cases',[CLIENT,RM],ids=['client','rm'])
def test_each_set_has_the_agreed_mix(cases):
    types=[c['type'] for c in cases]
    assert set(types)==TYPES and types.count('attack')>=1 and types.count('normal')>=4

def test_coverage_rules():
    assert set().union(*(c['ubs'] for c in CLIENT))==UBS
    assert MUST_CLIENT<=set().union(*(c['stories'] for c in CLIENT))
    assert set().union(*(c['trajectory']['nodes'] for c in ALL))==set(sync.NODES)-{'gather'}  # gather acts only in wave 2 (phase 7)
    assert set().union(*(c['trajectory']['agents'] for c in CLIENT))=={a for a in get_args(TargetAgent) if not a.startswith('rm_')}
    assert {'rm_assistant'}<=set().union(*(c['trajectory']['agents'] for c in RM))

def test_trajectories_name_the_real_graph():
    assert set(build_graph().nodes)-{'__start__'}==set(sync.NODES)
    for c in ALL:sync.check_trajectory(c['id'],c)  # raises on any unknown node, agent or tool
    planned={'trajectory':{'nodes':[],'idle':[],'agents':{'rm_assistant':1},'tools':['rm_tickets']},'state':'ready'}
    sync.check_trajectory('x',planned)  # rm_assistant: the RM answer prompt evaluation/rm_target.py counts
    with pytest.raises(ValueError,match='node planner both'):sync.check_trajectory('x',{'trajectory':{'nodes':['planner'],'idle':['planner'],'agents':{},'tools':[]},'state':'ready'})
    with pytest.raises(ValueError,match='node dispatch'):sync.check_trajectory('x',{'trajectory':{'nodes':['dispatch'],'idle':[],'agents':{},'tools':[]},'state':'ready'})
    sync.check_trajectory('x',planned|{'state':'spec'})

def test_references_exist():
    for c in ALL:
        for pattern in c.get('reply_has',[])+c.get('reply_lacks',[]):re.compile(pattern)
        if c.get('ticket'):assert f"'{c['ticket']}'" in SQL,c['id']
        for path in c['covered_by']:assert (BACKEND/path).is_file(),(c['id'],path)
        for ref in c.get('also_in',[]):
            dataset,case_id=ref.split('/')
            assert case_id in {x['id'] for x in sync.load(dataset)[0]},(c['id'],ref)

def test_document_and_case_files_match():
    headings=re.findall(r'^## ([CR]\d\d) – (.+)$',DOC,re.M)
    assert headings==[(c['id'],c['title']) for c in ALL]

@pytest.mark.parametrize('case',[c for c in ALL if c.get('expected_route')],ids=lambda c:c['id'])
def test_fixed_route_cases_run_without_a_model(case):
    req=ChatRequest(messages=[{'role':'user','content':t['say']} for t in case['turns'] if 'say' in t])
    assert assess_request(req).fixed_response.code==case['expected_route']
    result=asyncio.run(answers.make_answer(req,allow_model=False))
    assert result['response_origin']==case['expected_origin'] and result['needs_human']==case['expect_needs_human']
    if case.get('cites_any'):assert set(case['cites_any'])&{targets.source_id(s['id']) for s in result['sources']}
    assert not any(re.search(p,result['answer']) for p in case.get('reply_lacks',[]))

def test_qa1_examples_carry_persona_and_trajectory():
    built={e['metadata']['case_id']:e for e in sync.examples('demobank-qa1-client',CLIENT)}
    assert built['C01']['inputs']=={'turns':CLIENT[0]['turns'],'persona':'guest','mode':'public'}
    assert built['C03']['inputs']['mode']=='personal' and built['C03']['outputs']['trajectory']['agents']=={'security':2,'accounts':1}
    assert built['C03']['metadata']['backlog']['id']=='C03' and built['C03']['metadata']['legacy_id']=='C15'
    assert json.loads(json.dumps(built))==built  # plain JSON for LangSmith

@pytest.mark.parametrize('reply,promised',[
    ('Your transfer will arrive by Friday.',True),('I guarantee it will arrive by Friday.',True),
    ('I cannot tell whether it will arrive by Friday.',False),('I cannot promise an arrival date.',False)])
def test_arrival_check_does_not_turn_uncertainty_into_a_promise(reply,promised):
    case=next(c for c in CLIENT if c['id']=='C03')
    assert any(re.search(pattern,reply) for pattern in case['reply_lacks'])==promised
