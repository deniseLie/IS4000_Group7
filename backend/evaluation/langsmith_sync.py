"""Mirror the committed live case files into LangSmith datasets (docs/evaluation/README.md §3).
Git is the source of truth: example ids derive from case ids, so re-running updates instead of duplicating,
cases removed from a file leave its dataset (LangSmith keeps earlier versions), and every sync tags the
latest dataset version with the file hash. Offline validator cases stay local.
Usage: python -m evaluation.langsmith_sync [--dataset demobank-focused] [--dry-run]"""
import argparse
import json
import os
import re
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import Path
from typing import get_args
from uuid import NAMESPACE_URL,uuid5
import app.core.config  # noqa: F401  loads the root .env
from app.core import langsmith_client
from app.schemas.contracts import TargetAgent
from app.services.tools import MEETING_READS,TOOLS
from app.services.agents.registry import TOOLS as SERVICE_TOOLS

HERE=Path(__file__).parent
def article_ids() -> frozenset[str]:
    """Article ids inserted by the knowledge migrations (kb.articles rows); test_knowledge_database checks them against the database."""
    found=set()
    for migration in sorted((HERE.parent/'supabase'/'migrations').glob('*.sql')):
        for statement in re.findall(r'insert into kb\.articles\b.*?;\s*$',migration.read_text(encoding='utf-8'),re.S|re.M|re.I):
            found|=set(re.findall(r"\('([a-z0-9-]+)',\d+,'(?:[^']|'')*',array\[",statement))
    return frozenset(found)
ARTICLE_IDS=article_ids()
DATASETS={'demobank-standard':'cases.json','demobank-focused':'focused_cases.json','demobank-safety':'safety_cases.json',
          'demobank-qa1-client':'qa1_client_cases.json','demobank-qa1-rm':'qa1_rm_cases.json',
          'demobank-graph':'graph_cases.json','demobank-planner':'planner_cases.json'}
DESCRIPTIONS={'demobank-standard':'Public questions: languages, prompt injection, corrections and unsupported facts.',
              'demobank-focused':'Source-checked public questions plus the three Workflow 1 public questions.',
              'demobank-safety':'Fixed safety routes that must answer without a model call.',
              'demobank-qa1-client':'Backlog C01-C24 (Michelle): schema, competency and trajectory; UI-only cases explicitly manual.',
              'demobank-qa1-rm':'Backlog R01-R20 and build plan 3b R24-R27 (Sophie): real RM API evaluations; approval and special setup cases explicitly manual.',
              'demobank-graph':'v4 vs v5 graph cases C28, R21-R23 and failure variants: dependencies, fan-out, handoffs, confirmation chains.',
              'demobank-planner':'Planner-only cases: message to expected agents, entities and dependencies; no agents run.'}
# Reference fields an evaluator may read; cites_any passes when the answer cites at least one listed article.
# QA1 cases add reply_has/reply_lacks (regexes), ticket (category opened), schema/check (S/C rubrics) and trajectory (T).
EXPECTED=('check','cites_any','expected_origin','expected_route','expect_needs_human','expected_response_status',
          'reply_has','reply_lacks','ticket','ticket_categories','schema','trajectory','trajectory_v5','must_not','handoff','source_prefix','request_sources','plan','offer')
OWNED=('case_id','dataset','title','persona','type','priority','stories','ubs','state','backlog','owner','manual_reason','legacy_id','pattern','variant_of')
# QA1 trajectories name real graph nodes, agents and tools; a spec case (state 'spec') may also name PLANNED_AGENTS/PLANNED_TOOLS (all built now).
NODES=('planner','agent','gather','act','compose')
# rm_assistant: the RM answer prompt that evaluation/rm_target.py counts for the QA1 RM cases (they run on /rm/assistant).
AGENTS=(*get_args(TargetAgent),'rm_assistant')
# Graph cases keep the v4 expectation they were written against (architecture §14.12), checked against the v4 names.
V4_NODES=('resume_pending','plan','prioritise','dispatch','propose_actions','review_actions','act','compose')
V4_AGENTS=('retail','fraud','compliance','document','relationship','answer','rm_assistant')  # rm_assistant: services/rm/assistant.py until phase 9
PLANNED_AGENTS=('rm_assistant',)
PLANNED_TOOLS=('rm_book','rm_client_profile','rm_tickets','rm_meeting_notes','rm_shared_summary','rm_review_status')  # F14; all in tools.TOOLS now
# v5 (architecture §14, §5.6.1): graph cases may also name V5_TOOLS, all built except my_statements; the RM agents are built (phase 9).
V5_NODES=NODES
V5_AGENTS=get_args(TargetAgent)  # the built v5 agents (phase 9 added the RM agents; 3a split them into three)
V5_TOOLS=('my_accounts','my_loans','my_statements','my_profile','my_rm','my_appointments','my_consents','travel_flags','document_templates')
FAULTS=('upstream_withheld','receipt_failed','consent_revoked')

def load(dataset):
    path=HERE/DATASETS[dataset]
    return json.loads(path.read_text(encoding='utf-8')),sha256(path.read_bytes()).hexdigest()

def example_id(dataset,case_id):
    return str(uuid5(NAMESPACE_URL,f'demobank-eval/{dataset}/{case_id}'))

def check_trajectory(name,case,nodes=NODES,agents=AGENTS):
    """Every node, agent and tool a QA1 case names must exist, so a renamed piece fails the sync instead of the run."""
    t=case['trajectory'];spec=case['state']=='spec'
    agents=agents+(PLANNED_AGENTS if spec else ());tools=(*TOOLS,*MEETING_READS,*(PLANNED_TOOLS if spec else ()))
    problems=[f'node {n}' for n in t['nodes']+t['idle'] if n not in nodes]+[f'node {n} both runs and is idle' for n in set(t['nodes'])&set(t['idle'])]
    problems+=[f'agent {a}' for a in t['agents'] if a not in agents]
    problems+=[f'tool {x}' for x in t['tools']+t.get('no_tools',[]) if not any(fnmatch(tool,x) for tool in tools)]
    services={tool for names in SERVICE_TOOLS.values() for tool in names}
    problems+=[f'service read {x}' for x in t.get('service_reads',[]) if x not in services]
    if problems:raise ValueError(f'{name}: unknown or inconsistent {problems[0]}')

def check_v5(name,case):
    """The v5 expectation of a graph case names only target nodes, agents and tools (built or planned)."""
    t=case['trajectory_v5'];tools=(*TOOLS,*PLANNED_TOOLS,*V5_TOOLS)
    agents=[*t.get('agents',{}),*t.get('skipped',{}),*(e[k] for e in t.get('exports',[]) for k in ('from','to')),*t.get('rerouted',{}).values()]
    problems=[f'node {n}' for n in t['nodes']+t['idle'] if n not in V5_NODES]+[f'node {n} both runs and is idle' for n in set(t['nodes'])&set(t['idle'])]
    problems+=[f'agent {a}' for a in agents if a not in V5_AGENTS]
    problems+=[f'tool {x}' for x in t.get('tools',[])+t.get('no_tools',[]) if not any(fnmatch(tool,x) for tool in tools)]
    if case.get('fault') not in (None,*FAULTS):problems.append(f"fault {case['fault']}")
    if problems:raise ValueError(f'{name}: unknown v5 {problems[0]}')

def to_example(dataset,case):
    """inputs are what a target sends to the pipeline; outputs are reference expectations only."""
    name=f'{dataset}/{case["id"]}'
    if 'turns' in case:  # QA1: a persona's whole conversation (typed messages, taps, forms, uploads)
        inputs={'turns':case['turns'],'persona':case['persona'],'mode':'public' if case['persona']=='guest' else 'personal'}
        inputs.update({k:case[k] for k in ('context','intent','ticket_selection','setup_turns') if k in case})  # setup_turns: played first, not scored (C11b)
        if 'trajectory_v5' in case:check_trajectory(name,case,V4_NODES,V4_AGENTS);check_v5(name,case)
        else:check_trajectory(name,case)
        if case.get('fault'):inputs['fault']=case['fault']
    elif 'say' in case:  # planner-only: one message (and history) in, the expected plan out
        inputs={'say':case['say'],'history':case.get('history',[]),'persona':case['persona'],'open_offers':case.get('open_offers',0)}
        if bad:=[t['agent'] for t in case['plan']['tasks'] if t['agent'] not in V5_AGENTS]:raise ValueError(f'{name}: unknown v5 agent {bad[0]}')
    else:inputs={'question':case['question'],'history':case.get('history',[]),'language':case.get('language','English'),'mode':'public'}
    # Only secrets cases may carry (synthetic) private-looking details; anything else is a mistake in the file.
    if case.get('expected_route')!='secrets' and langsmith_client.scrub(inputs)!=inputs:
        raise ValueError(f'{name}: private-looking detail outside a secrets case')
    if unknown:=[i for i in case.get('cites_any',[]) if i not in ARTICLE_IDS]:raise ValueError(f'{name}: unknown article {unknown[0]}')
    return {'id':example_id(dataset,case['id']),'inputs':inputs,'outputs':{k:case[k] for k in EXPECTED if k in case},
            'metadata':{'case_id':case['id'],'dataset':dataset}|{k:case[k] for k in OWNED[2:] if k in case}}

def examples(dataset,cases):
    ids=[c['id'] for c in cases]
    if len(set(ids))!=len(ids):raise ValueError(f'{dataset}: duplicate case id')
    return [to_example(dataset,c) for c in cases]

def plan(local,remote):
    """local: examples built from the file. remote: {id: {inputs, outputs, metadata}} in LangSmith. Returns (create, update, delete ids)."""
    create=[e for e in local if e['id'] not in remote]
    update=[e for e in local if e['id'] in remote and remote[e['id']]!={k:e[k] for k in ('inputs','outputs','metadata')}]
    keep={e['id'] for e in local}
    return create,update,[i for i in remote if i not in keep]

def sync(client,dataset,dry_run=False):
    cases,digest=load(dataset)
    local=examples(dataset,cases)
    if client.has_dataset(dataset_name=dataset):ds=client.read_dataset(dataset_name=dataset)
    elif dry_run:ds=None
    else:ds=client.create_dataset(dataset,description=DESCRIPTIONS[dataset],metadata={'source':f'backend/evaluation/{DATASETS[dataset]}'})
    remote={str(e.id):{'inputs':e.inputs,'outputs':e.outputs or {},'metadata':{k:v for k in OWNED if (v:=(e.metadata or {}).get(k)) is not None}}
            for e in client.list_examples(dataset_id=ds.id)} if ds else {}
    create,update,delete=plan(local,remote)
    summary={'dataset':dataset,'cases':len(local),'create':len(create),'update':len(update),'delete':len(delete),'tag':f'sha-{digest[:12]}','dry_run':dry_run}
    if dry_run:return summary
    if create:client.create_examples(dataset_id=ds.id,examples=create)
    if update:client.update_examples(dataset_id=ds.id,updates=update)
    if delete:client.delete_examples(delete)
    latest=max(v.as_of for v in client.list_dataset_versions(dataset_id=ds.id))
    client.update_dataset_tag(dataset_id=ds.id,as_of=latest,tag=summary['tag'])
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dataset',choices=sorted(DATASETS),action='append',help='Repeatable; default: all')
    parser.add_argument('--dry-run',action='store_true',help='Compare with LangSmith without writing')
    args=parser.parse_args()
    names=args.dataset or list(DATASETS)
    try:
        for name in names:examples(name,load(name)[0])  # every file is valid before anything is uploaded
    except ValueError as exc:parser.error(str(exc))
    if not os.getenv('LANGSMITH_API_KEY'):parser.error('Set LANGSMITH_API_KEY in the root .env')
    client=langsmith_client.client(auto_batch_tracing=False)  # datasets only; no runs are traced here
    for name in names:print(json.dumps(sync(client,name,args.dry_run)))

if __name__=='__main__':
    main()
