"""Mirror the committed live case files into LangSmith datasets (docs/evaluation/langsmith-evals-plan.md §3).
Git is the source of truth: example ids derive from case ids, so re-running updates instead of duplicating,
cases removed from a file leave its dataset (LangSmith keeps earlier versions), and every sync tags the
latest dataset version with the file hash. Offline validator cases stay local.
Usage: python -m evaluation.langsmith_sync [--dataset demobank-focused] [--dry-run]"""
import argparse
import json
import os
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL,uuid5
import app.core.config  # noqa: F401  loads the root .env
from app.core import langsmith_client
from app.services.knowledge import INDEX

HERE=Path(__file__).parent
DATASETS={'demobank-standard':'cases.json','demobank-focused':'focused_cases.json','demobank-safety':'safety_cases.json'}
DESCRIPTIONS={'demobank-standard':'Public questions: languages, prompt injection, corrections and unsupported facts.',
              'demobank-focused':'Source-checked public questions plus the three Workflow 1 public questions.',
              'demobank-safety':'Fixed safety routes that must answer without a model call.'}
# Reference fields an evaluator may read; cites_any passes when the answer cites at least one listed article.
EXPECTED=('check','cites_any','expected_origin','expected_route','expect_needs_human')
OWNED=('case_id','dataset')

def load(dataset):
    path=HERE/DATASETS[dataset]
    return json.loads(path.read_text(encoding='utf-8')),sha256(path.read_bytes()).hexdigest()

def example_id(dataset,case_id):
    return str(uuid5(NAMESPACE_URL,f'demobank-eval/{dataset}/{case_id}'))

def to_example(dataset,case):
    """inputs are what a target sends to the pipeline; outputs are reference expectations only."""
    inputs={'question':case['question'],'history':case.get('history',[]),'language':case.get('language','English'),'mode':'public'}
    # Only secrets cases may carry (synthetic) private-looking details; anything else is a mistake in the file.
    if case.get('expected_route')!='secrets' and langsmith_client.scrub(inputs)!=inputs:
        raise ValueError(f'{dataset}/{case["id"]}: private-looking detail outside a secrets case')
    if unknown:=[i for i in case.get('cites_any',[]) if i not in INDEX]:raise ValueError(f'{dataset}/{case["id"]}: unknown article {unknown[0]}')
    return {'id':example_id(dataset,case['id']),'inputs':inputs,'outputs':{k:case[k] for k in EXPECTED if k in case},
            'metadata':{'case_id':case['id'],'dataset':dataset}}

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
    remote={str(e.id):{'inputs':e.inputs,'outputs':e.outputs or {},'metadata':{k:(e.metadata or {}).get(k) for k in OWNED}}
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
