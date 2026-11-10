"""v4 vs v5 graph cases (architecture §14.12) and the planner-only dataset: ids, failure variants and names."""
import json
import re
from pathlib import Path
import pytest
from evaluation import langsmith_sync as sync

BACKEND=Path(__file__).parents[1]
GRAPH=sync.load('demobank-graph')[0]
PLANNER=sync.load('demobank-planner')[0]
PATTERNS={'conditional','input','fan_out','handoff','confirmation_chain'}

def test_ids_and_variants():
    base=[c for c in GRAPH if 'variant_of' not in c]
    assert [c['id'] for c in base]==['C28','R21','R23']  # C23-C27 removed 4 Nov; R22 on 11 Nov (it asks which of two meetings on hosted)
    ids={c['id'] for c in base}
    for c in GRAPH:
        assert set(c['pattern'])<=PATTERNS and c['turns'] and c['check'] and c['must_not'] and c['trajectory'] and c['trajectory_v5']
        if 'variant_of' in c:assert c['variant_of'] in ids and c['id'].startswith(c['variant_of']) and c['fault'] in sync.FAULTS
    assert {c['fault'] for c in GRAPH if 'fault' in c}<=set(sync.FAULTS)

def test_every_pattern_has_a_failure_variant():
    faulted={p for c in GRAPH if 'fault' in c for p in c['pattern']}
    assert {'conditional'}<=faulted

def test_names_exist_in_both_graphs():
    for c in GRAPH:
        sync.check_trajectory(c['id'],c,sync.V4_NODES,sync.V4_AGENTS);sync.check_v5(c['id'],c)
    with pytest.raises(ValueError,match='v5 node plan'):sync.check_v5('x',{'trajectory_v5':{'nodes':['plan'],'idle':[],'agents':{}}})
    with pytest.raises(ValueError,match='v5 agent accounts_cards'):sync.check_v5('x',{'trajectory_v5':{'nodes':[],'idle':[],'agents':{'accounts_cards':1}}})
    with pytest.raises(ValueError,match='v5 agent retail'):sync.check_v5('x',{'trajectory_v5':{'nodes':[],'idle':[],'agents':{'retail':1}}})

def test_references_exist():
    for c in GRAPH:
        for pattern in c.get('reply_has',[])+c.get('reply_lacks',[]):re.compile(pattern)
        for path in c['covered_by']:assert (BACKEND/path).is_file(),(c['id'],path)

def test_planner_cases():
    assert [c['id'] for c in PLANNER]==[f'P{i:02}' for i in range(1,17) if i!=14]  # P14 removed 11 Nov (links its two tasks in about half the runs)
    for c in PLANNER:
        ids={t['id'] for t in c['plan']['tasks'] if 'id' in t}
        assert all(t['depends_on'] in ids for t in c['plan']['tasks'] if 'depends_on' in t),c['id']
    assert {c['persona'] for c in PLANNER}=={'guest','michelle','sophie'}

def test_examples_carry_fault_and_both_trajectories():
    built={e['metadata']['case_id']:e for e in sync.examples('demobank-graph',GRAPH)}
    assert built['R23a']['inputs']['fault']=='consent_revoked' and 'fault' not in built['R23']['inputs']
    assert built['R23a']['metadata']['variant_of']=='R23'
    planned={e['metadata']['case_id']:e for e in sync.examples('demobank-planner',PLANNER)}
    assert planned['P10']['inputs']['open_offers']==1 and planned['P10']['outputs']['plan']['route']=='act'
    assert json.loads(json.dumps(built))==built
