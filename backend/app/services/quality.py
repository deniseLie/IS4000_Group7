"""Admin dashboard (LangSmith read-back): evaluation experiments and recent client/RM traces.

The backend reads LangSmith with its own server key; the browser never sees it. Only signed-in users whose
administrator-owned app_metadata.demobank_role is 'admin' may read it (user_metadata is never trusted). Results are
cached briefly so the page does not hammer the LangSmith API. Text shown here already passed the redacting client.

Requests and steps are read through LangSmith's v2 run query (`query`): up to 1000 runs a page, and not throttled the way
/runs/query (list_runs) is, which answers 429 after about three 100-run pages and makes the SDK wait up to 9 s each time."""
import json
import os
import threading
import logging
import time
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from itertools import islice
from types import SimpleNamespace
from typing import Any
from langsmith import schemas as ls_schemas
from langsmith.utils import LangSmithRateLimitError
from app.core import langsmith_client
from app.services import insights
from app.schemas.quality import Check,Span,Trace
from typing import get_args
from app.schemas.contracts import TargetAgent

log=logging.getLogger(__name__)

ADMIN_ROLE='admin'
DATASET_PREFIX='demobank-'  # every evaluation dataset langsmith_sync uploads (evaluation/langsmith_sync.DATASETS)
CACHE_S=300  # live reads are cached 5 minutes server-side, so a Refresh inside that window returns the same numbers
_cache: dict[tuple,tuple[float,Any]]={}
_locks: dict[tuple,threading.Lock]=defaultdict(threading.Lock)  # one LangSmith read per key, even when pages load together
_client=None
WINDOWS={'24h':1,'7d':7,'30d':30,'90d':90}  # days; source 'live' is the app's tracing project, 'eval' the requests inside experiments
REQUESTS='or(eq(name, "chat"), eq(name, "rm_assistant"))'
STEPS=('llm','agent','tool_call','kb.search','planner','gather','act','compose','embed','loop_step')
MEETING_STEPS=('meeting.agent','meeting.tool_call')
# Steps plus the model client's own runs (run_type llm), whose tokens add up to a request's total as /runs/query summed it.
AGENT_ROWS=get_args(TargetAgent)  # since 10 Nov an agent's row is named after the agent (observability.span), read back as 'agent'
STEPS_AND_MODELS='or('+', '.join(f'eq(name, "{n}")' for n in (*STEPS,*AGENT_ROWS))+', eq(run_type, "llm"))'
MAX_REQUESTS,MAX_STEPS,MAX_TRACE=2000,20000,500  # bounded reads; the page says "sampled" when a cap is reached
POOL,RUN_QUERIES,BACKOFF_S=6,4,(2,5,10)  # parallel project reads, of which at most RUN_QUERIES query LangSmith at once (with backoff on 429)
FINISHED_MIN,FINISHED_S=60,6*3600  # an experiment older than an hour has finished, so its runs are cached for hours
EVAL_PROJECTS,PAGE,V2_PAGE=8,100,1000  # requests and steps are read from the latest experiments only; cases from every experiment
LIVE_DAYS=sorted({*WINDOWS.values(),*(2*d for d in WINDOWS.values())})  # windows the pages read (Overview and Failures read two)
_gate=threading.BoundedSemaphore(RUN_QUERIES)
RUN_SELECTS=['ID','NAME','RUN_TYPE','TRACE_ID','PARENT_RUN_IDS','PROJECT_ID','START_TIME','LATENCY_SECONDS','STATUS','ERROR','METADATA']
REQUEST_SELECTS=RUN_SELECTS+['INPUTS','OUTPUTS']
STEP_SELECTS=RUN_SELECTS+['TOTAL_TOKENS']
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


TRACE_UNIT=0.005  # LangSmith bills "Base Traces" in standard units: one trace is 0.005 units


def trace_budget() -> dict[str,Any] | None:
    """This month's LangSmith traces against the workspace's monthly limit (read-only billing reads), or None if unreadable."""
    def load():
        c,now_=client(),now()
        start=now_.replace(day=1,hour=0,minute=0,second=0,microsecond=0);resets=(start+timedelta(days=32)).replace(day=1)
        fmt='%Y-%m-%dT%H:%M:%SZ'
        limits=c.request_with_retries('GET','/api/v1/usage-limits').json()
        usage=c.request_with_retries('GET',f'/api/v1/orgs/current/billing/usage?starting_on={start.strftime(fmt)}&ending_before={now_.strftime(fmt)}').json()
        limit=next((l['limit_value'] for l in limits if l.get('limit_type')=='monthly_traces'),None)
        units=next((m.get('value') or 0 for m in usage if (m.get('billable_metric_name') or '').endswith('Base Traces')),None)
        return None if units is None else {'used':round(units/TRACE_UNIT),'limit':limit,'resets':resets}
    try:return cached(('trace_budget',),load)
    except Exception as failure:  # a missing billing read leaves the tile empty; the rest of the Overview still loads
        log.warning('admin trace budget unavailable',extra={'error_type':type(failure).__name__});return None


def fresh(key: tuple):
    hit=_cache.get(key)
    return hit[1] if hit and hit[0]>time.monotonic() else None


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
    name='agent' if m.get('step_name')=='agent' and run.name in AGENT_ROWS else run.name
    return {'run_id':str(run.id),'trace_id':str(run.trace_id),'parent_id':str(run.parent_run_id) if run.parent_run_id else None,'name':name,
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


def when(text: str | None) -> datetime | None:
    if not text:return None
    found=datetime.fromisoformat(text)
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def unquoted(text: str | None) -> str | None:
    """v2 returns some errors as a JSON string literal ('"LLMError(...)\\n..."'); list_runs gave the text itself."""
    if isinstance(text,str) and text.startswith('"'):
        try:
            found=json.loads(text)
            if isinstance(found,str):return found
        except ValueError:pass
    return text


def as_run(item: dict[str,Any]) -> SimpleNamespace:
    """A v2 run with the attributes list_runs gives (what as_request and as_step read)."""
    parents=item.get('parent_run_ids') or []  # root first, so the last one is the direct parent
    latency=item.get('latency_seconds')
    return SimpleNamespace(id=item['id'],name=item.get('name'),run_type=item.get('run_type'),trace_id=item.get('trace_id'),parent_run_ids=parents,
        parent_run_id=parents[-1] if parents else None,session_id=item.get('project_id'),start_time=when(item.get('start_time')),
        latency=round(latency,6) if isinstance(latency,(int,float)) else None,status=item.get('status'),error=unquoted(item.get('error')),
        extra={'metadata':item.get('metadata') or {}},inputs=item.get('inputs'),outputs=item.get('outputs'),total_tokens=item.get('total_tokens'))


def query(body: dict[str,Any],cap: int) -> list[SimpleNamespace]:
    """LangSmith's v2 run query, page by page, up to `cap` runs (newest first). Set min_start_time: it defaults to a day ago."""
    found,cursor=[],None
    while len(found)<cap:
        data=json.dumps({**body,'page_size':min(V2_PAGE,cap-len(found)),**({'cursor':cursor} if cursor else {})})
        page=gated(lambda:client().request_with_retries('POST','/api/v2/runs/query',request_kwargs={'data':data}).json())
        found.extend(page.get('items') or [])
        if not (cursor:=page.get('next_cursor')):break
    return [as_run(item) for item in found[:cap]]


def with_tokens(asked: list,runs: list) -> list:
    """Each request's total_tokens: the tokens of the model runs (run_type llm) under it, which is what /runs/query reported."""
    ids={str(r.id) for r in asked};total=Counter()
    for r in runs:
        if r.run_type=='llm':
            for parent in r.parent_run_ids:
                if parent in ids:total[parent]+=r.total_tokens or 0
    for r in asked:r.total_tokens=total.get(str(r.id))
    return asked


def read_runs(base: dict[str,Any],source: str,root: bool | None=None) -> tuple[list[dict],list[dict]]:
    """(requests, steps) of one project: two queries at once, requests with their text, steps with metadata only."""
    with ThreadPoolExecutor(2) as pool:
        asked=pool.submit(query,{**base,'filter':REQUESTS,'selects':REQUEST_SELECTS,**({'is_root':root} if root is not None else {})},MAX_REQUESTS)
        inner=pool.submit(query,{**base,'filter':STEPS_AND_MODELS,'selects':STEP_SELECTS,**({'trace_filter':REQUESTS} if root else {})},MAX_STEPS)
        runs=inner.result();found=with_tokens(asked.result(),runs)
    return [as_request(r,source) for r in found],[s for r in runs if (s:=dict(as_step(r),source=source))['name'] in STEPS]


def per_project(kind: str,projects: list,read) -> list:
    """read(project) for each experiment project in parallel, cached on its own: for hours once its experiment has
    finished. LangSmith answers a run query over many projects for only some of them, so each project is read alone."""
    def one(p):return cached((kind,str(p.id)),lambda:read(p),FINISHED_S if finished(p.start_time) else CACHE_S)
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
                'pipeline':m.get('pipeline'),'transport':m.get('transport'),'judge_model':m.get('judge_model'),
                'judge_rubric_version':m.get('judge_rubric_version'),'answer_prompt':m.get('answer_prompt'),
                'prompts':m.get('prompts_in_use'),'commit':(git.get('commit') or m.get('app_version') or '')[:7] or None,'branch':git.get('branch'),
                'partial':m.get('partial'),'cases':sorted(cases[str(p.id)],key=lambda c:c['case_id'] or '')})
        return sorted(found,key=lambda e:e['started_at'])
    return cached(('experiment_runs',),load,120)


def recent_projects(since: datetime) -> list:
    """The latest EVAL_PROJECTS experiment projects that started since `since`, oldest first: their requests and steps are
    read run by run (one query each), so older experiments count through their cases only."""
    return sorted((p for _,p in experiment_projects() if p.start_time>=since),key=lambda p:p.start_time)[-EVAL_PROJECTS:]


def eval_runs(since: datetime) -> list[tuple[list[dict],list[dict]]]:
    """(requests, steps) of each recent experiment."""
    return per_project('eval_runs',recent_projects(since),
        lambda p:read_runs({'project_ids':[str(p.id)],'min_start_time':(p.start_time-timedelta(days=1)).isoformat()},'eval'))


def live_project() -> str:
    return cached(('live_project',project()),lambda:str(client().read_project(project_name=project()).id),3600)


def live_runs(days: int) -> tuple[list[dict],list[dict]]:
    """(requests, steps) of the app's own project in the last `days` days. Every page and source shares this read, and a
    shorter window is cut from a wider one still cached (Overview reads 14 days for 7, so the 7-day pages need no read)."""
    since=now()-timedelta(days=days)
    for wider in (d for d in LIVE_DAYS if d>days):
        if hit:=fresh(('live_runs',wider)):return [r for r in hit[0] if r['started_at']>=since],[s for s in hit[1] if s['started_at']>=since]
    return cached(('live_runs',days),lambda:read_runs({'project_ids':[live_project()],'min_start_time':since.isoformat()},'live',root=True))


def meeting_steps(days: int) -> list[dict[str,Any]]:
    """Live workflow spans, including standalone worker runs and preparation outside chat requests."""
    def load() -> list[dict[str,Any]]:
        since=now()-timedelta(days=days)
        run_filter='or('+', '.join(f'eq(name, "{name}")' for name in MEETING_STEPS)+')'
        runs=query({'project_ids':[live_project()],'min_start_time':since.isoformat(),'filter':run_filter,'selects':STEP_SELECTS},MAX_REQUESTS)
        return [dict(as_step(run),name=run.name.removeprefix('meeting.'),source='live') for run in runs]
    return cached(('meeting_steps',days),load)


def requests(source: str,days: int) -> list[dict[str,Any]]:
    """Client and RM requests that started in the last `days` days. Eval requests carry their case (dataset, case id, status)."""
    since=now()-timedelta(days=days);found=[]
    if source in ('live','all'):found.extend(live_runs(days)[0])
    if source in ('eval','all'):
        case={c['run_id']:(e,c) for e in experiment_runs() for c in e['cases']}
        for row in islice((r for rows,_ in eval_runs(since) for r in rows if r['started_at']>=since),MAX_REQUESTS):
            row=dict(row)
            if hit:=case.get(row['trace_id']):row.update(dataset=hit[0]['dataset'],experiment=hit[0]['name'],case_id=hit[1]['case_id'],case_status=hit[1]['status'])
            found.append(row)
    return sorted(found,key=lambda r:r['started_at'])


def steps(source: str,days: int) -> list[dict[str,Any]]:
    """The steps inside those requests: model calls, agents, tools, knowledge search and graph nodes."""
    since=now()-timedelta(days=days);found=[]
    if source in ('live','all'):found.extend(live_runs(days)[1])
    if source in ('eval','all'):found.extend(islice((s for _,rows in eval_runs(since) for s in rows if s['started_at']>=since),MAX_STEPS-len(found)))
    return found


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
