"""Run a LangSmith dataset through the real app and score it (docs/evaluation/README.md §6 step 4).

Usage: python -m evaluation.langsmith_run --dataset demobank-qa1-client [--case C01] [--judge] [--pipeline v2]
Real provider, real database: every playable turn calls the configured model and writes to the project the .env points
at (saved chats, audit, and tickets or handoffs where a case asks for them). Signed-in cases need the persona fixture
(TEST_PERSONA_FIXTURE_FILE, as for tests/test_rls_live.py). Spec cases (state 'spec') are skipped. Cases run one at a
time so ticket before/after reads stay attributable. Exits 1 when a deterministic check fails or a case errors."""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path

STATUSES=('pass','fail','skipped','error')


def status(row) -> str:
    if row['run'].error:return 'error'
    scores=[r.score for r in row['evaluation_results']['results'] if not r.key.startswith('judge')]
    if any(s==0 for s in scores):return 'fail'
    return 'pass' if any(s is not None for s in scores) else 'skipped'


async def run(args) -> dict:
    from langsmith import aevaluate
    from app.core import langsmith_client
    from app.core.config import settings
    from app.main import app
    from app.services import prompts
    from evaluation import evaluators,targets
    from evaluation.langsmith_sync import DATASETS
    if settings.conversation_store!='database':raise SystemExit('Set CONVERSATION_STORE=database: the runner plays saved chats like the browser')
    settings.pipeline=args.pipeline
    client=langsmith_client.client()
    examples=[e for e in client.list_examples(dataset_name=args.dataset)
        if (e.metadata or {}).get('state')!='spec' and (not args.case or (e.metadata or {}).get('case_id') in args.case)]
    if not examples:raise SystemExit('No runnable examples (spec cases are skipped)')
    personas={e.inputs.get('persona') for e in examples}-{None,'guest'}
    tokens={}
    if personas:
        if not args.personas:raise SystemExit('Signed-in cases need --personas or TEST_PERSONA_FIXTURE_FILE')
        fixture=json.loads(Path(args.personas).read_text(encoding='utf-8'))
        tokens={p:targets.sign_in(p,fixture,os.environ['SUPABASE_URL'],os.environ['SUPABASE_ANON_KEY']) for p in sorted(personas)}
    async def target(inputs: dict) -> dict:
        return await targets.play(inputs,app,tokens.get(inputs.get('persona')))
    prompt=prompts.current()['id']
    results=await aevaluate(target,data=examples,evaluators=[evaluators.deterministic,*([evaluators.judge] if args.judge else [])],
        experiment_prefix=f'{args.dataset}-{args.pipeline}',max_concurrency=1,client=client,
        metadata={'dataset':args.dataset,'pipeline':args.pipeline,'answer_prompt':prompt,'prompt_versions':settings.prompt_versions,
            'model':os.getenv('LLM_MODEL'),'transport':os.getenv('CHAT_TRANSPORT','api'),'judge_model':os.getenv('JUDGE_MODEL') if args.judge else None,
            'dataset_file':DATASETS[args.dataset]})
    rows=[]
    async for row in results:
        out=row['run'].outputs or {}
        rows.append({'id':(row['example'].metadata or {}).get('case_id'),'status':status(row),
            'question':' / '.join(t.get('say') or json.dumps(t) for t in targets.turns_of(row['example'].inputs)),
            'answer':out.get('answer',''),'turns':[{k:r.get(k) for k in ('say','origin','agents','model_calls','sources')} for r in out.get('replies',[])],'checks':{r.key:{'score':r.score,'comment':r.comment} for r in row['evaluation_results']['results']},
            'error':(row['run'].error or '')[:300] or None})
    rows.sort(key=lambda r:r['id'] or '')
    return {'mode':'langsmith_experiment','experiment':results.experiment_name,'dataset':args.dataset,'pipeline':args.pipeline,
        'created_at':datetime.now(timezone.utc).isoformat(),'summary':{s:sum(r['status']==s for r in rows) for s in STATUSES},
        'limits':['Real model and real database: results vary between runs and models.',
            'Deterministic checks test wording patterns, sources, tickets and trajectory, not full meaning; judge scores are model-graded.',
            'Form, upload and button steps other than chat replies are not played; checks that depend on them are skipped.'],
        'results':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dataset',required=True,help='LangSmith dataset, e.g. demobank-qa1-client')
    parser.add_argument('--case',action='append',help='Only these case ids (repeatable)')
    parser.add_argument('--pipeline',choices=('v1','v2'),default='v2')
    parser.add_argument('--judge',action='store_true',help='Also grade check/must_not with JUDGE_MODEL (must differ from LLM_MODEL)')
    parser.add_argument('--personas',default=os.getenv('TEST_PERSONA_FIXTURE_FILE'),help='JSON list of {email, password} persona logins')
    parser.add_argument('--env-file',type=Path,help='Load this .env first (e.g. the main checkout\'s from a worktree)')
    parser.add_argument('--output',default='langsmith-results.json')
    args=parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    if not os.getenv('LANGSMITH_API_KEY'):parser.error('Set LANGSMITH_API_KEY')
    report=asyncio.run(run(args))
    from evaluation.run import write_report
    write_report(report,args.output)
    print(json.dumps({'experiment':report['experiment'],**report['summary']}))
    for row in report['results']:
        failed=[f"{k}: {v['comment']}" for k,v in row['checks'].items() if v['score']==0]
        print(f"{row['id']}: {row['status']}"+(' - '+'; '.join(failed) if failed else '')+(f" ({row['error']})" if row['error'] else ''),file=sys.stderr)
    return 1 if report['summary']['fail'] or report['summary']['error'] else 0


if __name__=='__main__':
    raise SystemExit(main())
