"""Admin dashboard (LangSmith read-back): evaluation experiments and recent client/RM traces.

The backend reads LangSmith with its own server key; the browser never sees it. Only signed-in users whose
administrator-owned app_metadata.demobank_role is 'admin' may read it (user_metadata is never trusted). Results are
cached briefly so the page does not hammer the LangSmith API. Text shown here already passed the redacting client."""
import json
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from itertools import islice
from typing import Any
from langsmith import schemas as ls_schemas
from langsmith.utils import LangSmithRateLimitError
from app.core import langsmith_client
from app.services import insights
from app.schemas.quality import Check,Span,Trace

ADMIN_ROLE='admin'
DATASET_PREFIX='demobank-'  # every evaluation dataset langsmith_sync uploads (evaluation/langsmith_sync.DATASETS)
CACHE_S=60
_cache: dict[tuple,tuple[float,Any]]={}
_locks: dict[tuple,threading.Lock]=defaultdict(threading.Lock)  # one LangSmith read per key, even when pages load together
_client=None
WINDOWS={'24h':1,'7d':7,'30d':30,'90d':90}  # days; source 'live' is the app's tracing project, 'eval' the requests inside experiments
REQUEST_NAMES=('chat','rm_assistant')
REQUESTS='or(eq(name, "chat"), eq(name, "rm_assistant"))'
STEPS=('llm','agent','tool_call','kb.search','planner','gather','act','compose','embed','loop_step')
STEP_FILTER='or('+', '.join(f'eq(name, "{n}")' for n in STEPS)+')'
EVAL_FILTER='or('+', '.join(f'eq(name, "{n}")' for n in REQUEST_NAMES+STEPS)+')'
MAX_REQUESTS,MAX_STEPS,MAX_TRACE=2000,20000,500  # bounded reads; the page says "sampled" when a cap is reached
POOL,RUN_QUERIES,BACKOFF_S=6,4,(2,5,10)  # parallel project reads, of which at most RUN_QUERIES query LangSmith at once (it rate-limits /runs/query)
FINISHED_MIN,FINISHED_S=60,6*3600  # an experiment older than an hour has finished, so its runs are cached for hours
EVAL_PROJECTS,PAGE=8,100  # requests and steps are read from the latest experiments only (one query each); cases from every experiment
_gate=threading.BoundedSemaphore(RUN_QUERIES)
REQUEST_FIELDS=['id','name','trace_id','session_id','parent_run_id','start_time','end_time','status','error','extra','inputs','outputs','total_tokens']
STEP_FIELDS=['id','name','run_type','trace_id','session_id','parent_run_id','start_time','end_time','status','error','extra','total_tokens']
# Step metadata the dashboard reads (names, statuses, counts and ms; never text).
STEP_KEYS=('agent','intent','path','status','outcome','tool_calls','steps','refused','evidence_count','exports','wave','prompt_version','model','transport',
    'input_tokens','output_tokens','retries','cause','error_type','tool','mode','count','embedding_error','embed_ms','sql_ms','route','tasks','dependent','side',
    'sends','skipped','rerouted','decision','offers','chained','failed','langgraph_node',
    # build plan 3a: the loop's stop and limit, plan source and refusals, tool outcomes (allowed, reason, consent), cards and exports
    'stop','limit','step','reads_left','source','refusal','allowed','reason','shared','category','purpose','rows','cards','withheld','export_names',
    'unlinked','fan_out','passed','merged','fixed_lines')


def is_admin(claims: dict[str,Any]) -> bool:
    return (claims.get('app_metadata') or {}).get('demobank_role')==ADMIN_ROLE


def client():
    global _client
    if _client is None:_client=langsmith_client.client()
    return _client


def cached(key: tuple,load,ttl: int=CACHE_S):
    hit=_cache.get(key)
    if hit and hit[0]>time.monotonic():return hit[1]
    with _locks[key]:
        hit=_cache.get(key)
        if hit and hit[0]>time.monotonic():return hit[1]
        value=load()
        _cache[key]=(time.monotonic()+ttl,value)
        return value


def checks_of(stats: dict[str,Any] | None) -> list[Check]:
    """One check per feedback key from the run's feedback stats (avg is the 0/0.5/1 score of a single run)."""
    return [Check(key=k,score=v.get('avg'),comment=next(iter(v.get('comments') or []),'') or '') for k,v in sorted((stats or {}).items())]


def status(checks: list[Check],error: str | None) -> str:
    if error:return 'error'
    scores=[c.score for c in checks if not c.key.startswith('judge')]
    if any(s==0 for s in scores):return 'fail'
    return 'pass' if any(s is not None for s in scores) else 'skipped'


def datasets() -> list[dict[str,Any]]:
    """The synced evaluation datasets, read from LangSmith so a new one shows without a code change."""
    def load():
        return sorted(({'name':d.name,'id':str(d.id),'examples':d.example_count} for d in client().list_datasets(dataset_name_contains=DATASET_PREFIX)
            if d.name.startswith(DATASET_PREFIX)),key=lambda d:d['name'])
    return cached(('datasets',),load,600)


def project() -> str:
    return os.getenv('LANGSMITH_PROJECT') or 'default'


def traces(limit: int=30) -> list[Trace]:
    def load():
        runs=islice(client().list_runs(project_name=project(),is_root=True,filter='or(eq(name, "chat"), eq(name, "rm_assistant"))',limit=limit),limit)
        return [Trace(run_id=str(r.id),started_at=r.start_time,latency_s=r.latency,status=r.status,tokens=r.total_tokens,
            mode='rm' if r.name=='rm_assistant' else (r.extra or {}).get('metadata',{}).get('mode'),error=(r.error or '')[:300] or None) for r in runs]
    return cached(('traces',limit),load)


def trace(run_id: str) -> list[Span]:
    def load():
        root=client().read_run(run_id);top=root.dotted_order or ''
        kids=sorted((r for r in islice(client().list_runs(trace_id=root.trace_id),MAX_TRACE) if (r.dotted_order or '').startswith(top)),key=lambda r:(r.dotted_order or ''))
        return [Span(name=r.name,run_type=r.run_type,depth=(r.dotted_order or '').count('.')-top.count('.'),latency_s=r.latency,status=r.status,
            start_s=round((r.start_time-root.start_time).total_seconds(),3) if r.start_time and root.start_time else None,
            tokens=r.total_tokens,agent=(r.extra or {}).get('metadata',{}).get('agent'),tool=(r.extra or {}).get('metadata',{}).get('tool'),
            error=(r.error or '')[:300] or None) for r in kids]
    return cached(('trace',run_id),load)


# ---- Bulk reads for the dashboard pages (plain dicts; services/insights.py does the arithmetic) ----

def now() -> datetime:
    return datetime.now(timezone.utc)


def meta(run) -> dict[str,Any]:
    return (run.extra or {}).get('metadata') or {}


def short(text: str | None,limit: int=300) -> str | None:
    return (text or '')[:limit] or None


def question(inputs: dict[str,Any] | None) -> str:
    """The last user message of a request (already redacted by the tracing client)."""
    inputs=inputs or {}
    if isinstance(inputs.get('message'),str):return inputs['message'][:300]
    users=[m.get('content') for m in inputs.get('messages') or [] if isinstance(m,dict) and m.get('role')=='user' and isinstance(m.get('content'),str)]
    return users[-1][:300] if users else ''


def as_request(run,source: str) -> dict[str,Any]:
    m,out=meta(run),run.outputs or {}
    rm=run.name=='rm_assistant'
    return {'run_id':str(run.id),'trace_id':str(run.trace_id),'source':source,'name':run.name,'started_at':run.start_time,'latency_s':run.latency,
        'surface':'rm' if rm else 'personal' if m.get('mode')=='personal' else 'public','status':run.status,'error':short(run.error),
        'tokens':run.total_tokens or m.get('llm_tokens') or None,'route':m.get('route'),'plan_route':m.get('plan_route'),'outcome':m.get('outcome'),
        'origin':out.get('response_origin'),'source_check':m.get('source_check') or out.get('source_check'),'needs_human':out.get('needs_human'),
        'reject_reason':m.get('reject_reason'),'prompt_version':m.get('prompt_version') or out.get('prompt_version'),'pipeline':m.get('pipeline'),
        'model_calls':m.get('model_calls'),'agents':m.get('agents'),'agent_status':m.get('agent_status'),'prompt_ids':m.get('prompt_ids'),'model':m.get('model'),
        'first_section_ms':m.get('first_section_ms'),'app_version':m.get('app_version'),'error_type':m.get('error_type'),
        'offered':m.get('offered'),'llm_ms':m.get('llm_ms'),
        'side':m.get('side') or ('rm' if rm else 'client'),'role':m.get('role'),'tool_calls':m.get('tool_calls'),'loop_stops':m.get('loop_stops') or {},
        'refusals':m.get('refusals'),'step_refusals':m.get('step_refusals'),'refused_tools':m.get('refused_tools') or [],'consent_gaps':m.get('consent_gaps'),
        'question':question(run.inputs),'answer':next((out[k][:600] for k in ('answer','summary') if isinstance(out.get(k),str) and out[k]),''),  # RM replies carry a summary
        'sources':[s['id'] for s in out.get('sources') or [] if isinstance(s,dict) and isinstance(s.get('id'),str)][:20]}


def as_step(run) -> dict[str,Any]:
    m=meta(run)
    return {'run_id':str(run.id),'trace_id':str(run.trace_id),'parent_id':str(run.parent_run_id) if run.parent_run_id else None,'name':run.name,
        'run_type':run.run_type,'started_at':run.start_time,'latency_s':run.latency,'run_status':run.status,'error':short(run.error),'tokens':run.total_tokens,
        'meta':{k:m[k] for k in STEP_KEYS if m.get(k) is not None}}


def asked(inputs: dict[str,Any] | None) -> list[str]:
    """An evaluation case's synthetic dataset inputs: the turns it typed, or the planner-only message."""
    given=inputs or {}
    said=[t['say'] for t in given.get('turns') or [] if isinstance(t,dict) and isinstance(t.get('say'),str)]+([given['say']] if isinstance(given.get('say'),str) else [])
    return [s[:300] for s in said]


def finished(started_at: datetime) -> bool:
    return now()-started_at>timedelta(minutes=FINISHED_MIN)


def gated(read):
    """At most RUN_QUERIES run queries at once across every page; a rate-limited read waits and tries again."""
    for wait in (*BACKOFF_S,None):
        try:
            with _gate:return read()
        except LangSmithRateLimitError:
            if wait is None:raise
            time.sleep(wait)


def per_project(kind: str,projects: list,read) -> list:
    """read(project) for each experiment project in parallel, cached on its own: for hours once its experiment has
    finished. LangSmith answers a run query over many projects for only some of them, so each project is read alone."""
    def one(p):return cached((kind,str(p.id)),lambda:gated(lambda:read(p)),FINISHED_S if finished(p.start_time) else CACHE_S)
    if not projects:return []
    with ThreadPoolExecutor(min(POOL,len(projects))) as pool:return list(pool.map(one,projects))


def experiment_projects() -> list[tuple[dict,Any]]:
    """(dataset, project) for every experiment of every synced dataset."""
    return cached(('experiment_projects',),lambda:[(d,p) for d in datasets() for p in client().list_projects(reference_dataset_id=d['id'])],120)


def dataset_runs(dataset_id: str,sessions: list[str]) -> list:
    """Every case run of those experiments with its example (LangSmith's experiment comparison read): a few calls per
    dataset instead of one rate-limited run query per experiment."""
    found,offset=[],0
    while True:
        body={'session_ids':sessions,'offset':offset,'limit':PAGE,'preview':False}
        batch=gated(lambda:client().request_with_retries('POST',f'/datasets/{dataset_id}/runs',request_kwargs={'data':json.dumps(body)}).json())
        found.extend(ls_schemas.ExampleWithRuns(**example) for example in batch)
        if len(batch)<PAGE:return found
        offset+=len(batch)


def as_case(run,example) -> dict[str,Any]:
    """One evaluation case run: status from its rule checks, what it was expected to do (dataset outputs) and what it did."""
    checks=checks_of(run.feedback_stats);out=run.outputs or {};expected=example.outputs or {}
    strings=lambda key:[str(x)[:120] for x in out.get(key) or [] if isinstance(x,(str,int))][:30] if isinstance(out.get(key),list) else []
    return {'case_id':(example.metadata or {}).get('case_id'),'run_id':str(run.id),'status':status(checks,run.error),
        'checks':{c.key:{'score':c.score,'comment':c.comment[:300]} for c in checks},
        'expected':{k:expected[k] for k in insights.EXPECTED_SHOWN if k in expected},
        'answer':out['answer'][:1500] if isinstance(out.get('answer'),str) else '','tools':strings('tools'),'service_reads':strings('service_reads'),
        'model_calls':out.get('model_calls') if isinstance(out.get('model_calls'),int) else None,
        'failed':[c.key for c in checks if c.score==0 and not c.key.startswith('judge')],
        'judge':next((c.score for c in checks if c.key=='judge_rubric'),None),'latency_s':run.latency,'tokens':run.total_tokens or None,
        'error':short(run.error),'agents':out.get('agents') if isinstance(out.get('agents'),dict) else {},
        'prompt_ids':out.get('prompt_ids') if isinstance(out.get('prompt_ids'),list) else None,'started_at':run.start_time,
        'asked':asked(run.inputs or example.inputs)}


def experiment_runs() -> list[dict[str,Any]]:
    """Every experiment of every synced dataset with its cases' statuses (oldest first)."""
    def load():
        projects=experiment_projects()
        sessions=defaultdict(list)
        for d,p in projects:sessions[d['id']].append(str(p.id))
        with ThreadPoolExecutor(min(POOL,len(sessions) or 1)) as pool:
            read=dict(zip(sessions,pool.map(lambda d:dataset_runs(d,sessions[d]),sessions)))
        cases=defaultdict(list)
        for examples in read.values():
            for example in examples:
                for run in example.runs:cases[str(run.session_id)].append(as_case(run,example))
        found=[]
        for d,p in projects:
            m=(p.extra or {}).get('metadata') or {};git=m.get('git') or {}
            found.append({'id':str(p.id),'name':p.name,'dataset':d['name'],'started_at':p.start_time,'url':p.url,'model':m.get('model'),
                'pipeline':m.get('pipeline'),'transport':m.get('transport'),'judge_model':m.get('judge_model'),'answer_prompt':m.get('answer_prompt'),
                'prompts':m.get('prompts_in_use'),'commit':(git.get('commit') or m.get('app_version') or '')[:7] or None,'branch':git.get('branch'),
                'partial':m.get('partial'),'cases':sorted(cases[str(p.id)],key=lambda c:c['case_id'] or '')})
        return sorted(found,key=lambda e:e['started_at'])
    return cached(('experiment_runs',),load,120)


def recent_projects(since: datetime) -> list:
    """The latest EVAL_PROJECTS experiment projects that started since `since`, oldest first: their requests and steps are
    read run by run (one query each), so older experiments count through their cases only."""
    return sorted((p for _,p in experiment_projects() if p.start_time>=since),key=lambda p:p.start_time)[-EVAL_PROJECTS:]


def eval_runs(since: datetime) -> list[tuple[list[dict],list[dict]]]:
    """(requests, steps) of each recent experiment, both from one run query per experiment."""
    def read(p):
        runs=list(islice(client().list_runs(project_id=p.id,filter=EVAL_FILTER,select=REQUEST_FIELDS),MAX_STEPS))
        return [as_request(r,'eval') for r in runs if r.name in REQUEST_NAMES],[dict(as_step(r),source='eval') for r in runs if r.name in STEPS]
    return per_project('eval_runs',recent_projects(since),read)


def requests(source: str,days: int) -> list[dict[str,Any]]:
    """Client and RM requests that started in the last `days` days. Eval requests carry their case (dataset, case id, status)."""
    def load():
        since=now()-timedelta(days=days);found=[]
        if source in ('live','all'):
            runs=client().list_runs(project_name=project(),is_root=True,start_time=since,filter=REQUESTS,select=REQUEST_FIELDS)
            found.extend(as_request(r,'live') for r in islice(runs,MAX_REQUESTS))
        if source in ('eval','all'):
            case={c['run_id']:(e,c) for e in experiment_runs() for c in e['cases']}
            for row in islice((r for rows,_ in eval_runs(since) for r in rows if r['started_at']>=since),MAX_REQUESTS):
                row=dict(row)
                if hit:=case.get(row['trace_id']):row.update(dataset=hit[0]['dataset'],experiment=hit[0]['name'],case_id=hit[1]['case_id'],case_status=hit[1]['status'])
                found.append(row)
        return sorted(found,key=lambda r:r['started_at'])
    return cached(('requests',source,days),load)


def steps(source: str,days: int) -> list[dict[str,Any]]:
    """The steps inside those requests: model calls, agents, tools, knowledge search and graph nodes."""
    def load():
        since=now()-timedelta(days=days);found=[]
        if source in ('live','all'):
            runs=client().list_runs(project_name=project(),start_time=since,filter=STEP_FILTER,trace_filter=REQUESTS,select=STEP_FIELDS)
            found.extend(dict(as_step(r),source='live') for r in islice(runs,MAX_STEPS))
        if source in ('eval','all'):
            found.extend(islice((s for _,rows in eval_runs(since) for s in rows if s['started_at']>=since),MAX_STEPS-len(found)))
        return found
    return cached(('steps',source,days),load)


def trace_runs(run_id: str) -> dict[str,Any]:
    """One request (live) or one evaluation case (eval) with every run in its trace, for the failure card."""
    def load():
        first=client().read_run(run_id)
        runs=sorted(islice(client().list_runs(trace_id=first.trace_id),MAX_TRACE),key=lambda r:r.dotted_order or '')
        top=next((r for r in runs if r.parent_run_id is None),first)
        exp=next((e for e in experiment_runs() if e['id']==str(top.session_id)),None)
        try:url=client().get_run_url(run=top,project_id=top.session_id)
        except Exception:url=None
        rows=[]
        for r in runs:
            row=as_step(r);row['depth']=(r.dotted_order or '').count('.')
            if r.name in ('chat','rm_assistant'):row['request']=as_request(r,'eval' if exp else 'live')
            rows.append(row)
        case=next((c for c in exp['cases'] if c['run_id']==str(top.id)),None) if exp else None
        return {'root_id':str(top.id),'url':url,'case':case,'asked':asked(top.inputs) if exp else None,'expected':case.get('expected') if case else None,'runs':rows,'capped':len(runs)>=MAX_TRACE,
            'experiment':{k:exp[k] for k in ('name','dataset','model','pipeline','transport','commit','url')} if exp else None}
    return cached(('trace_runs',run_id),load)
