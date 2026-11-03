"""Run a LangSmith dataset through the real app and score it (docs/evaluation/README.md §6 step 4).

Usage: python -m evaluation.langsmith_run --dataset demobank-qa1-client [--case C01] [--judge] [--pipeline v2]
Real provider, real database: every playable turn calls the configured model and writes to the project the .env points
at (saved chats, audit, and tickets or handoffs where a case asks for them). Signed-in cases need the persona fixture
(TEST_PERSONA_FIXTURE_FILE, as for tests/test_rls_live.py). Spec cases (state 'spec') and cases that need a setup the
runner cannot make (state 'manual', e.g. a failing model) are skipped. Cases run one at a
time so ticket before/after reads stay attributable. Exits 1 when a deterministic check fails or a case errors."""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

STATUSES=('pass','fail','skipped','error')


def status(row) -> str:
    if row['run'].error:return 'error'
    scores=[r.score for r in row['evaluation_results']['results'] if not r.key.startswith('judge')]
    if any(s==0 for s in scores):return 'fail'
    return 'pass' if any(s is not None for s in scores) else 'skipped'


async def into_project(project: str,label: str,meta: dict,examples: list,target,graders: list,client) -> list[dict]:
    """--project: each case is one trace in an existing tracing project, beside the app's own traces, with every check
    attached as feedback. LangSmith keeps experiments only in dataset-linked projects, so this mode has no experiment
    comparison view; filter the project by the `eval` tag or the run label instead."""
    import langsmith
    session=client.read_project(project_name=project).id  # must exist: a typo would otherwise create a new project
    rows=[]
    for example in examples:
        case=(example.metadata or {}).get('case_id')
        out,error=None,None
        with langsmith.tracing_context(enabled=True),langsmith.trace(f'eval {case}','chain',project_name=project,inputs=example.inputs,client=client,
                tags=['eval',meta['dataset'],label],metadata={'eval_run':label,'case_id':case,'example_id':str(example.id),**meta}) as tree:
            try:out=await target(example.inputs)
            except Exception as failure:error=f'{type(failure).__name__}: {failure}'
            tree.end(outputs=out,error=error)
        run=SimpleNamespace(outputs=out,error=error)
        results=[]
        for grade in graders if error is None else []:
            graded=await grade(run,example) if asyncio.iscoroutinefunction(grade) else grade(run,example)
            results+=graded.get('results',[graded])
        for r in results:client.create_feedback(tree.id,key=r['key'],score=r['score'],comment=r['comment'] or None,trace_id=tree.trace_id,session_id=session)
        rows.append({'run':run,'example':example,'evaluation_results':{'results':[SimpleNamespace(**r) for r in results]}})
    client.flush()
    return rows


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
        if (e.metadata or {}).get('state') not in ('spec','manual') and (not args.case or (e.metadata or {}).get('case_id') in args.case)]
    if not examples:raise SystemExit('No runnable examples (spec and manual cases are skipped)')
    personas={e.inputs.get('persona') for e in examples if 'turns' in e.inputs}-{None,'guest'}  # the planner-only dataset signs in nobody
    tokens={}
    if personas:
        if not args.personas:raise SystemExit('Signed-in cases need --personas or TEST_PERSONA_FIXTURE_FILE')
        fixture=json.loads(Path(args.personas).read_text(encoding='utf-8'))
        tokens={p:targets.sign_in(p,fixture,os.environ['SUPABASE_URL'],os.environ['SUPABASE_ANON_KEY']) for p in sorted(personas)}
    prompt=prompts.current()['id']
    meta={'dataset':args.dataset,'pipeline':args.pipeline,'answer_prompt':prompt,'prompt_versions':settings.prompt_versions,
        'model':os.getenv('LLM_MODEL'),'transport':os.getenv('CHAT_TRANSPORT','api'),'judge_model':os.getenv('JUDGE_MODEL') if args.judge else None,
        'dataset_file':DATASETS[args.dataset]}
    label=f"{args.dataset}-{args.pipeline}-{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    async def target(inputs: dict) -> dict:
        if 'say' in inputs:return await targets.plan_only(inputs)
        return await targets.play(inputs,app,tokens.get(inputs.get('persona')))
    graders=[evaluators.deterministic,*([evaluators.judge] if args.judge else [])]
    if args.project:
        found=await into_project(args.project,label,meta,examples,target,graders,client);name=args.project
    else:
        results=await aevaluate(target,data=examples,evaluators=graders,experiment_prefix=f'{args.dataset}-{args.pipeline}',
            max_concurrency=1,client=client,metadata=meta)
        found=[row async for row in results];name=results.experiment_name
    rows=[]
    for row in found:
        out=row['run'].outputs or {}
        rows.append({'id':(row['example'].metadata or {}).get('case_id'),'status':status(row),
            'question':' / '.join(t.get('say') or json.dumps(t) for t in targets.turns_of(row['example'].inputs)),
            'answer':out.get('answer',''),'turns':[{k:r.get(k) for k in ('say','origin','agents','model_calls','sources')} for r in out.get('replies',[])],'checks':{r.key:{'score':r.score,'comment':r.comment} for r in row['evaluation_results']['results']},
            'error':(row['run'].error or '')[:300] or None})
    rows.sort(key=lambda r:r['id'] or '')
    return {'mode':'langsmith_project' if args.project else 'langsmith_experiment','experiment':name,'eval_run':label if args.project else None,'dataset':args.dataset,'pipeline':args.pipeline,
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
    parser.add_argument('--project',help='Trace each case into this existing tracing project (e.g. demobank-dev) with its checks '
        'as feedback, instead of a dataset experiment; runs are tagged eval and the run label')
    parser.add_argument('--output',default='langsmith-results.json')
    args=parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    import app.core.config  # noqa: F401  loads the same .env files as the app
    if not os.getenv('LANGSMITH_API_KEY'):parser.error('Set LANGSMITH_API_KEY')
    report=asyncio.run(run(args))
    from evaluation.run import write_report
    write_report(report,args.output)
    print(json.dumps({'experiment':report['experiment'],'eval_run':report['eval_run'],**report['summary']}))
    for row in report['results']:
        failed=[f"{k}: {v['comment']}" for k,v in row['checks'].items() if v['score']==0]
        print(f"{row['id']}: {row['status']}"+(' - '+'; '.join(failed) if failed else '')+(f" ({row['error']})" if row['error'] else ''),file=sys.stderr)
    return 1 if report['summary']['fail'] or report['summary']['error'] else 0


if __name__=='__main__':
    raise SystemExit(main())
