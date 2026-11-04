"""Admin dashboard analytics (docs/planning/admin-dashboard.md Part 2): pure functions over the plain run dicts that
services/quality.py reads from LangSmith. Nothing here calls LangSmith, so every number is unit-tested on sample rows.

Definitions: a request is a `chat` or `rm_assistant` run; it failed when the run errored or its outcome is 'error'; it was
held back when the answer checks withheld it (origin 'validation' or source_check 'rejected'); it was handed to a person
when needs_human is true. A case passes when no rule check scored 0 (judge scores are model-graded and never decide)."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from typing import Any

FIXED_ROUTES=frozenset({'secrets','lost_card','human_support','guest_balance','other_customer_data','instruction_override'})  # api/chat.FIXED_ROUTES
BAD_AGENT=('withheld','timeout','error')  # needs_human and needs_clarification are intended outcomes, not failures
STATUSES=('pass','fail','error','skipped')
NODES=('planner','agent','gather','act','compose')
# Starting thresholds for "needs attention"; the team tunes them.
HELD_BACK_MAX,ERROR_RATE_MAX,P95_MAX_S,STALE_DAYS,MIN_RUNS=0.15,0.02,30.0,7,3
LEVELS=('high','medium','info')
EXPECTED_SHOWN=('check','must_not','reply_has','reply_lacks','cites_any','expected_origin','expected_route','expect_needs_human','ticket',
    'ticket_categories','trajectory','trajectory_v5','plan','source_prefix')


# ---- small helpers ----

def pct(values,q: float) -> float | None:
    xs=sorted(v for v in values if v is not None)
    if not xs:return None
    k=(len(xs)-1)*q;lo=int(k);hi=min(lo+1,len(xs)-1)
    return round(xs[lo]+(xs[hi]-xs[lo])*(k-lo),3)


def ratio(n: float,d: float) -> float | None:
    return None if not d else round(n/d,4)


def mean(values) -> float | None:
    xs=[v for v in values if v is not None]
    return round(sum(xs)/len(xs),3) if xs else None


def failed(r: dict) -> bool:
    return bool(r.get('error')) or r.get('status')=='error' or r.get('outcome')=='error'


def held_back(r: dict) -> bool:
    return r.get('origin')=='validation' or r.get('source_check')=='rejected'


def version(prompt_id: str | None) -> str | None:
    return (prompt_id or '').split('+')[0] or None


def error_type(text: str | None) -> str | None:
    """'LLMError('Model …')' -> 'LLMError'; free text -> its first 60 characters."""
    if not text:return None
    head=text.split('(',1)[0].split(':',1)[0].strip()
    return head[:60] if head.isidentifier() else text[:60]


def agent_runs(steps: list[dict]) -> list[dict]:
    """Agent spans (not the LangGraph node also named 'agent'): they name an agent and an intent."""
    return [s for s in steps if s['name']=='agent' and s['meta'].get('agent') and s['meta'].get('intent')]


def model_calls(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s['name']=='llm' and s['meta'].get('prompt_version')]


def agent_status(s: dict) -> str:
    m=s['meta']
    if m.get('outcome')=='timeout':return 'timeout'
    return m.get('status') or ('error' if s.get('error') else 'ok')


def calls_by_trace(steps: list[dict]) -> Counter:
    return Counter(s['trace_id'] for s in model_calls(steps))


# ---- Overview ----

def kpis(rows: list[dict],calls: Counter | None=None) -> dict[str,Any]:
    n,calls=len(rows),calls or Counter()
    per_trace=Counter(r['trace_id'] for r in rows)
    bad=sum(map(failed,rows))
    used=[r['model_calls'] if r.get('model_calls') is not None else calls.get(r['trace_id'],0)/per_trace[r['trace_id']] for r in rows]
    lat=[r['latency_s'] for r in rows]
    return {'requests':n,'client':sum(r['surface']!='rm' for r in rows),'rm':sum(r['surface']=='rm' for r in rows),'failed':bad,
        'success_rate':ratio(n-bad,n),'held_back_rate':ratio(sum(map(held_back,rows)),n),'handoff_rate':ratio(sum(bool(r.get('needs_human')) for r in rows),n),
        'p50_s':pct(lat,.5),'p95_s':pct(lat,.95),'tokens_per_request':mean(r.get('tokens') for r in rows),'model_calls_per_request':mean(used)}


def series(rows: list[dict],end: datetime,days: int) -> list[dict[str,Any]]:
    """Buckets for the sparklines: hourly for 24 h, daily up to 30 days, 30 buckets beyond."""
    n=24 if days==1 else min(days,30);size=timedelta(days=days)/n;start=end-timedelta(days=days)
    out=[{'start':start+size*i,'requests':0,'failed':0,'held_back':0,'p95_s':None} for i in range(n)];lat=defaultdict(list)
    for r in rows:
        i=int((r['started_at']-start)/size)
        if 0<=i<n:
            b=out[i];b['requests']+=1;b['failed']+=failed(r);b['held_back']+=held_back(r);lat[i].append(r['latency_s'])
    for i,values in lat.items():out[i]['p95_s']=pct(values,.95)
    return out


def counts(cases: list[dict]) -> dict[str,int]:
    found=Counter(c['status'] for c in cases)
    return {s:found.get(s,0) for s in STATUSES}


def pass_rate(cases: list[dict]) -> float | None:
    c=counts(cases)
    return ratio(c['pass'],c['pass']+c['fail']+c['error'])


def by_dataset(experiments: list[dict]) -> dict[str,list[dict]]:
    found=defaultdict(list)
    for e in sorted(experiments,key=lambda e:e['started_at']):found[e['dataset']].append(e)
    return found


def subset(e: dict,largest: int) -> bool:
    """A run of only some cases (--case): recorded since the admin dashboard; older runs are judged by their size."""
    return bool(e['partial']) if e.get('partial') is not None else len(e['cases'])<0.75*largest


def full_runs(exps: list[dict]) -> list[dict]:
    largest=max((len(e['cases']) for e in exps),default=0)
    return [e for e in exps if e['cases'] and not subset(e,largest)]


def latest_evals(experiments: list[dict],now: datetime) -> list[dict[str,Any]]:
    rows=[]
    for name,exps in sorted(by_dataset(experiments).items()):
        full=full_runs(exps);ref=full[-1] if full else exps[-1];prev=full[-2] if len(full)>1 else None
        rows.append({'dataset':name,'experiment':ref['name'],'started_at':ref['started_at'],'url':ref['url'],'model':ref['model'],
            'pass_rate':pass_rate(ref['cases']),'previous_pass_rate':pass_rate(prev['cases']) if prev else None,'counts':counts(ref['cases']),
            'cases':len(ref['cases']),'last_run':exps[-1]['started_at'],'stale':now-exps[-1]['started_at']>timedelta(days=STALE_DAYS)})
    return rows


def case_history(exps: list[dict]) -> dict[str,list[tuple[dict,dict]]]:
    """case id -> [(experiment, case)] oldest first, over every run that included the case."""
    found=defaultdict(list)
    for e in exps:
        for c in e['cases']:
            if c['case_id']:found[c['case_id']].append((e,c))
    return found


def regressions(experiments: list[dict]) -> list[dict[str,Any]]:
    """Cases whose latest run fails although the run before it passed (the latest run of that case, in any experiment)."""
    found=[]
    for name,exps in by_dataset(experiments).items():
        for case_id,runs in case_history(exps).items():
            if len(runs)<2:continue
            (pe,pc),(le,lc)=runs[-2],runs[-1]
            if lc['status'] in ('fail','error') and pc['status']=='pass':
                found.append({'dataset':name,'case_id':case_id,'experiment':le['name'],'previous_experiment':pe['name'],'started_at':le['started_at'],
                    'status':lc['status'],'failed':lc['failed'],'comments':{k:lc['checks'][k]['comment'] for k in lc['failed'] if lc['checks'].get(k,{}).get('comment')},
                    'run_id':lc['run_id'],'error':lc['error']})
    return sorted(found,key=lambda r:r['started_at'],reverse=True)


# ---- Failures ----

def events(rows: list[dict],steps: list[dict],experiments: list[dict],since: datetime,with_eval: bool) -> list[dict[str,Any]]:
    """Every failure in the window, one dict each, before grouping. Agent, model and tool failures name the step; a held-back
    answer counts at request level only when no agent in that trace already reported it."""
    first=defaultdict(dict)
    for r in rows:first[r['trace_id']]=first[r['trace_id']] or r
    def base(kind: str,source: str,trace_id: str,started_at,**kw) -> dict[str,Any]:
        r=first.get(trace_id) or {}
        return {'kind':kind,'source':source,'trace_id':trace_id,'started_at':started_at,'surface':r.get('surface'),'question':r.get('question') or '',
            'dataset':r.get('dataset'),'case_id':r.get('case_id'),'experiment':r.get('experiment'),'agent':None,'tool':None,'prompt':None,'error':None,'detail':None,
            'step_id':None,**kw}
    found=[];covered=set()
    for s in steps:
        if s['started_at']<since:continue
        m=s['meta']
        if s['name']=='agent' and m.get('agent') and m.get('intent'):
            status=agent_status(s)
            if status in BAD_AGENT:
                found.append(base('agent_'+status,s['source'],s['trace_id'],s['started_at'],agent=m['agent'],error=m.get('error_type') or status,
                    detail=f"intent {m['intent']}"+(f", path {m['path']}" if m.get('path') else ''),step_id=s['run_id']));covered.add(s['trace_id'])
        elif s['name']=='llm' and m.get('prompt_version') and m.get('outcome') not in (None,'ok'):
            found.append(base('model_call',s['source'],s['trace_id'],s['started_at'],agent=m.get('agent'),prompt=f"{m.get('agent')} {version(m['prompt_version'])}",
                error=m['outcome'],detail=', '.join(filter(None,[m.get('cause'),f"{m['retries']} retries" if m.get('retries') else None,m.get('model')])) or None,step_id=s['run_id']))
            covered.add(s['trace_id'])
        elif s['name']=='tool_call' and s['error']:
            found.append(base('tool_error',s['source'],s['trace_id'],s['started_at'],tool=m.get('tool'),error=m.get('error_type') or error_type(s['error']),
                detail=s['error'],step_id=s['run_id']));covered.add(s['trace_id'])
    stepped={s['trace_id'] for s in agent_runs(steps)}
    for r in rows:
        if r['started_at']<since:continue
        if r['trace_id'] not in stepped and isinstance(r.get('agent_status'),dict):  # steps not read (older experiments): the request's own summary
            for agent,status in sorted(r['agent_status'].items()):
                if status in BAD_AGENT:
                    found.append(base('agent_'+status,r['source'],r['trace_id'],r['started_at'],agent=agent,error=status,detail='from the request summary'))
                    covered.add(r['trace_id'])
        if failed(r):
            found.append(base('request_error',r['source'],r['trace_id'],r['started_at'],error=r.get('error_type') or error_type(r['error']) or 'error',detail=r['error']))
        elif held_back(r) and r['trace_id'] not in covered:
            found.append(base('held_back',r['source'],r['trace_id'],r['started_at'],error=r.get('reject_reason') or 'answer checks',detail=r.get('answer') or None))
    if with_eval:
        for e in experiments:
            if e['started_at']<since:continue
            for c in e['cases']:
                if c['status'] in ('fail','error'):
                    said=(first.get(c['run_id']) or {}).get('question') or next(iter(c.get('asked') or []),'')
                    found.append(base('eval_case','eval',c['run_id'],c['started_at'] or e['started_at'],dataset=e['dataset'],case_id=c['case_id'],experiment=e['name'],question=said,
                        error=', '.join(c['failed']) or error_type(c['error']) or c['status'],
                        detail='; '.join(f"{k}: {c['checks'][k]['comment']}" for k in c['failed'] if c['checks'].get(k,{}).get('comment')) or c['error']))
    return found


def signature(e: dict) -> str:
    if e['kind']=='eval_case':return f"eval_case|{e['dataset']}|{e['case_id']}"
    return '|'.join([e['kind'],e['source'],e.get('agent') or e.get('tool') or '',e.get('error') or '',e.get('prompt') or ''])


def title(e: dict) -> str:
    who=e.get('agent') or e.get('tool') or ''
    return {'request_error':f"Request failed: {e['error']}",'held_back':f"Answer held back by the checks ({e['error']})",
        'agent_withheld':f"{who} withheld its answer",'agent_timeout':f"{who} timed out",'agent_error':f"{who} failed: {e['error']}",
        'model_call':f"Model call {e['error']} in {e.get('prompt')}",'tool_error':f"Tool {who} failed: {e['error']}",
        'eval_case':f"{e['case_id']} failing in {e['dataset']}"}.get(e['kind'],e['kind'])


def groups(found: list[dict],since: datetime,experiments: list[dict] | None=None,per_group: int=20) -> list[dict[str,Any]]:
    """Failures grouped by signature, newest first. `new`: first seen inside the window (since) and not before it."""
    out={}
    history={(n,c):[x['status'] for _,x in runs] for n,exps in by_dataset(experiments or []).items() for c,runs in case_history(exps).items()}
    for e in sorted(found,key=lambda e:e['started_at'],reverse=True):
        key=signature(e)
        g=out.get(key)
        if g is None:
            g=out[key]={'signature':key,'kind':e['kind'],'source':e['source'],'title':title(e),'agent':e.get('agent'),'tool':e.get('tool'),'prompt':e.get('prompt'),
                'error':e.get('error'),'dataset':e.get('dataset'),'case_id':e.get('case_id'),'count':0,'in_window':0,'first_seen':e['started_at'],
                'last_seen':e['started_at'],'surfaces':Counter(),'items':[]}
            if e['kind']=='eval_case':g['history']=history.get((e['dataset'],e['case_id']),[])[-10:]
        g['count']+=1;g['in_window']+=e['started_at']>=since;g['first_seen']=min(g['first_seen'],e['started_at'])
        if e.get('surface'):g['surfaces'][e['surface']]+=1
        if len(g['items'])<per_group:g['items'].append(e)
    for g in out.values():g['new']=g['first_seen']>=since;g['surfaces']=dict(g['surfaces'])
    return sorted((g for g in out.values() if g['in_window']),key=lambda g:g['last_seen'],reverse=True)


def failures(rows: list[dict],steps: list[dict],experiments: list[dict],now: datetime,days: int,source: str) -> dict[str,Any]:
    since=now-timedelta(days=days)
    found=events(rows,steps,experiments,since-timedelta(days=days),with_eval=source in ('eval','all'))
    found=[e for e in found if source=='all' or e['source']==source]
    grouped=groups(found,since,experiments)
    refused=sum(1 for s in steps if s['name']=='planner' and s['meta'].get('route')=='refused' and s['started_at']>=since)
    return {'window_days':days,'source':source,'groups':grouped,'refusals':refused,
        'counts':dict(Counter(g['source'] for g in grouped for _ in range(g['in_window'])))}


# ---- "Needs attention" ----

def attention(rows: list[dict],steps: list[dict],experiments: list[dict],grouped: list[dict],now: datetime,untested: list[str] | None=None) -> list[dict[str,Any]]:
    items=[]
    def add(level,kind,text,detail,page,**filters):items.append({'level':level,'kind':kind,'title':text,'detail':detail,'page':page,'filter':filters})
    for r in regressions(experiments)[:6]:
        add('high','regression',f"{r['case_id']} regressed in {r['dataset']}",
            f"Passed in {r['previous_experiment']}, {r['status']} in {r['experiment']}"+(f": {'; '.join(f'{k}: {v}' for k,v in r['comments'].items())}" if r['comments'] else
            f" ({', '.join(r['failed'])})" if r['failed'] else ''),'evaluations',dataset=r['dataset'],case=r['case_id'])
    for g in grouped:
        if g['new'] and g['kind']!='eval_case':
            add('high' if g['kind'] in ('request_error','agent_error','tool_error') else 'medium','new_failure',g['title'],
                f"{g['in_window']}× in this window, first seen {g['first_seen']:%d %b %H:%M} UTC; not seen in the window before",'failures',signature=g['signature'])
    n,bad=len(rows),sum(map(failed,rows))
    if n>=MIN_RUNS and bad/n>ERROR_RATE_MAX:add('high','error_rate',f"{bad/n:.0%} of requests failed",f"{bad} of {n} requests (limit {ERROR_RATE_MAX:.0%})",'failures')
    if n>=MIN_RUNS and (p95:=pct([r['latency_s'] for r in rows],.95)) and p95>P95_MAX_S:
        add('medium','latency',f"p95 latency {p95:.1f} s",f"Above the {P95_MAX_S:.0f} s target over {n} requests",'latency')
    per_agent=defaultdict(Counter)
    for s in agent_runs(steps):per_agent[s['meta']['agent']][agent_status(s)]+=1
    for agent,c in sorted(per_agent.items()):
        total,withheld=sum(c.values()),c['withheld']+c['timeout']+c['error']
        if total>=MIN_RUNS and withheld/total>HELD_BACK_MAX:
            add('medium','held_back',f"{agent} withheld or failed {withheld/total:.0%} of its runs",f"{withheld} of {total} runs ({dict(c)})",'agents',agent=agent)
    fixed=[r for r in rows if r.get('route') in FIXED_ROUTES and (r.get('model_calls') or 0)>0]
    if fixed:add('high','fixed_route_model',f"{len(fixed)} fixed safety replies called the model",
        'Lost card, secrets and human-support replies must never call the model: '+', '.join(sorted({r['route'] for r in fixed})),'failures')
    searches=[s for s in steps if s['name']=='kb.search']
    broken=[s for s in searches if s['meta'].get('mode')=='keyword_only' and s['meta'].get('embedding_error') not in (None,'not_configured')]
    if broken:add('medium','keyword_fallback',f"Knowledge search fell back to keyword-only {len(broken)}×",
        'The embedding provider failed: '+', '.join(sorted({str(s['meta']['embedding_error']) for s in broken})),'agents')
    elif any(s['meta'].get('embedding_error')=='not_configured' for s in searches):
        add('info','keyword_fallback','Knowledge search runs keyword-only','EMBEDDING_API_KEY is not set on this server, so hybrid search is off','agents')
    if untested:add('info','untested_prompt',f"{len(untested)} configured prompts have no evaluation run in 30 days",', '.join(untested),'prompts')
    for row in latest_evals(experiments,now):
        if row['stale']:add('info','stale_dataset',f"{row['dataset']} last ran {row['last_run']:%d %b}",f"Older than {STALE_DAYS} days",'evaluations',dataset=row['dataset'])
    return sorted(items,key=lambda i:LEVELS.index(i['level']))


def overview(rows: list[dict],steps: list[dict],experiments: list[dict],now: datetime,days: int,untested: list[str] | None=None) -> dict[str,Any]:
    """rows and steps cover two windows (this one and the one before), so tiles can show the change."""
    since=now-timedelta(days=days);before=since-timedelta(days=days)
    cur=[r for r in rows if r['started_at']>=since];prev=[r for r in rows if before<=r['started_at']<since]
    calls=calls_by_trace(steps)
    grouped=groups([e for e in events(rows,steps,experiments,before,with_eval=False)],since)
    return {'window_days':days,'live':{'now':kpis(cur,calls),'previous':kpis(prev,calls),'series':series(cur,now,days)},
        'evals':latest_evals(experiments,now),'attention':attention(cur,[s for s in steps if s['started_at']>=since],experiments,grouped,now,untested)}


def untested_prompts(in_use: dict[str,str],steps: list[dict],experiments: list[dict]) -> list[str]:
    """Configured prompt versions that no evaluation case called (llm runs in experiments, or the cases' recorded prompt ids).
    Empty when nothing recorded which prompts ran (experiments from before the cases carried prompt ids): unknown, not untested."""
    seen={(s['meta'].get('agent'),version(s['meta']['prompt_version'])) for s in model_calls(steps)}
    seen|={tuple(p.split('/',1)[0:1])+(version(p.split('/',1)[1]),) for e in experiments for c in e['cases'] for p in c.get('prompt_ids') or [] if '/' in p}
    return [f'{family} {v}' for family,v in sorted(in_use.items()) if v and (family,v) not in seen] if seen else []


# ---- Evaluations ----

ANSWER_CHECKS=frozenset({'reply_has','reply_lacks','cites','record_sources','origin','fixed_route','needs_human','completed','played'})
PLANNER_DATASET='demobank-planner'
CHECK_GROUPS=('answer','trajectory','planner','judge')
HEAT_RUNS=10


def check_group(key: str,dataset: str) -> str:
    if key.startswith('judge'):return 'judge'
    if dataset==PLANNER_DATASET:return 'planner'
    return 'answer' if key in ANSWER_CHECKS or key.startswith('ticket') else 'trajectory'


def changes(before: dict | None,after: dict) -> list[str]:
    """What changed between two runs of a dataset: model, pipeline, transport and prompt versions (when both recorded them)."""
    if before is None:return []
    found=[f'{k} {before[k]} → {after[k]}' for k in ('model','pipeline','transport') if before.get(k) and after.get(k) and before[k]!=after[k]]
    a,b=before.get('prompts') or {},after.get('prompts') or {}
    if a and b:found+=[f'{f} {a.get(f) or "—"} → {b.get(f) or "—"}' for f in sorted(set(a)|set(b)) if a.get(f)!=b.get(f)]
    elif before.get('answer_prompt') and after.get('answer_prompt') and before['answer_prompt']!=after['answer_prompt']:
        found.append(f"answer {before['answer_prompt']} → {after['answer_prompt']}")
    return found


def timeline(exps: list[dict]) -> list[dict[str,Any]]:
    """One point per experiment (oldest first). Subset runs are flagged and compared with nothing; full runs with the full run before."""
    largest=max((len(e['cases']) for e in exps),default=0);points=[];previous=None
    for e in exps:
        part=not e['cases'] or subset(e,largest)
        points.append({'experiment':e['name'],'started_at':e['started_at'],'url':e['url'],'pass_rate':pass_rate(e['cases']),'counts':counts(e['cases']),
            'cases':len(e['cases']),'subset':part,'model':e.get('model'),'transport':e.get('transport'),'judge_model':e.get('judge_model'),
            'pipeline':e.get('pipeline'),'commit':e.get('commit'),'judge':mean(c.get('judge') for c in e['cases']),
            'changes':[] if part else changes(previous,e)})
        if not part:previous=e
    return points


def check_trends(exps: list[dict],dataset: str) -> list[dict[str,Any]]:
    """Each check's pass rate (mean score) per experiment, so a drop shows whether wording, citations, routing or tools broke."""
    keys=sorted({k for e in exps for c in e['cases'] for k in c['checks']})
    rows=[]
    for key in keys:
        points=[]
        for e in exps:
            scores=[c['checks'][key]['score'] for c in e['cases'] if c['checks'].get(key,{}).get('score') is not None]
            points.append({'experiment':e['name'],'rate':mean(scores),'n':len(scores)})
        rows.append({'key':key,'group':check_group(key,dataset),'points':points,'latest':next((p['rate'] for p in reversed(points) if p['n']),None)})
    return sorted(rows,key=lambda r:(CHECK_GROUPS.index(r['group']),r['key']))


def case_label(statuses: list[str]) -> str:
    """regressed / fixed (the last two runs), always_failing, flaky (pass and fail swapped twice or more), else failing or
    passing by the latest run."""
    seen=[s for s in statuses if s in ('pass','fail','error')]
    if not seen:return 'not_scored'
    bad=[s!='pass' for s in seen]
    if len(seen)>=2 and bad[-1] and not bad[-2]:return 'regressed'
    if len(seen)>=2 and not bad[-1] and bad[-2]:return 'fixed'
    if all(bad):return 'always_failing'
    if sum(a!=b for a,b in zip(bad,bad[1:]))>=2:return 'flaky'
    return 'failing' if bad[-1] else 'passing'


def heatmap(exps: list[dict],runs: int=HEAT_RUNS) -> dict[str,Any]:
    """Cases × the last `runs` experiments; each case labelled from its whole history."""
    last=exps[-runs:];history=case_history(exps)
    ids=sorted({c['case_id'] for e in last for c in e['cases'] if c['case_id']})
    rows=[]
    for case_id in ids:
        cells={e['name']:c['status'] for e in last for c in e['cases'] if c['case_id']==case_id}
        rows.append({'case_id':case_id,'statuses':[cells.get(e['name']) for e in last],'label':case_label([c['status'] for _,c in history[case_id]])})
    return {'experiments':[{'name':e['name'],'started_at':e['started_at']} for e in last],'rows':rows,
        'labels':dict(Counter(r['label'] for r in rows))}


def evaluations(experiments: list[dict],datasets: list[dict],now: datetime) -> list[dict[str,Any]]:
    grouped=by_dataset(experiments)
    out=[]
    for d in datasets:
        exps=grouped.get(d['name'],[])
        full=full_runs(exps)
        out.append({'dataset':d['name'],'examples':d.get('examples'),'runs':len(exps),'full_runs':len(full),
            'last_run':exps[-1]['started_at'] if exps else None,'stale':bool(exps) and now-exps[-1]['started_at']>timedelta(days=STALE_DAYS),
            'cases_last_full':len(full[-1]['cases']) if full else 0,'pass_rate':pass_rate(full[-1]['cases']) if full else None,
            'timeline':timeline(exps),'checks':check_trends(exps,d['name']),'heatmap':heatmap(exps)})
    return out


def compare(a: dict,b: dict) -> dict[str,Any]:
    """Case by case from run A (before) to run B (after)."""
    ca={c['case_id']:c for c in a['cases'] if c['case_id']};cb={c['case_id']:c for c in b['cases'] if c['case_id']}
    rows=[]
    for case_id in sorted(set(ca)|set(cb)):
        x,y=ca.get(case_id),cb.get(case_id)
        if x is None or y is None:kind='only_b' if x is None else 'only_a'
        else:
            bx,by=x['status'] in ('fail','error'),y['status'] in ('fail','error')
            kind='skipped' if 'skipped' in (x['status'],y['status']) else 'regressed' if by and not bx else 'fixed' if bx and not by else 'still_failing' if bx else 'still_passing'
        side=lambda c:None if c is None else {'status':c['status'],'failed':c['failed'],'comments':{k:c['checks'][k]['comment'] for k in c['failed'] if c['checks'].get(k,{}).get('comment')},
            'judge':c.get('judge'),'run_id':c['run_id'],'error':c.get('error')}
        rows.append({'case_id':case_id,'kind':kind,'a':side(x),'b':side(y)})
    order=('regressed','fixed','still_failing','only_a','only_b','skipped','still_passing')
    return {'a':{k:a.get(k) for k in ('name','dataset','started_at','model','pipeline','commit')},'b':{k:b.get(k) for k in ('name','dataset','started_at','model','pipeline','commit')},
        'changes':changes(a,b),'counts':{k:sum(r['kind']==k for r in rows) for k in order},'rows':sorted(rows,key=lambda r:(order.index(r['kind']),r['case_id']))}


def experiment_detail(e: dict) -> dict[str,Any]:
    return {**{k:e.get(k) for k in ('name','dataset','started_at','url','model','pipeline','transport','judge_model','commit','branch','partial','prompts')},
        'counts':counts(e['cases']),'pass_rate':pass_rate(e['cases']),
        'cases':[{**c,'repro':f"python -m evaluation.langsmith_run --dataset {e['dataset']} --case {c['case_id']}" if c['case_id'] else None} for c in e['cases']]}


# ---- Prompts ----

def prompt_versions(steps: list[dict]) -> dict[tuple,list[dict]]:
    """(family, version) -> its llm runs."""
    found=defaultdict(list)
    for s in model_calls(steps):found[(s['meta'].get('agent'),version(s['meta']['prompt_version']))].append(s)
    return found


def call_metrics(calls: list[dict]) -> dict[str,Any] | None:
    if not calls:return None
    outcomes=Counter(c['meta'].get('outcome') or ('error' if c['error'] else 'ok') for c in calls)
    return {'calls':len(calls),'ok_rate':ratio(outcomes.get('ok',0),len(calls)),'outcomes':dict(outcomes),
        'retries':sum(c['meta'].get('retries') or 0 for c in calls),'p50_s':pct([c['latency_s'] for c in calls],.5),'p95_s':pct([c['latency_s'] for c in calls],.95),
        'input_tokens':mean(c['meta'].get('input_tokens') for c in calls),'output_tokens':mean(c['meta'].get('output_tokens') for c in calls)}


def cases_by_prompt(experiments: list[dict],eval_steps: list[dict]) -> dict[tuple,list[tuple[dict,dict]]]:
    """(family, version) -> the eval cases it ran in: from the case's recorded prompt ids, or its trace's llm runs."""
    by_run={c['run_id']:(e,c) for e in experiments for c in e['cases']}
    found=defaultdict(dict)
    for e,c in by_run.values():
        for p in c.get('prompt_ids') or []:
            if '/' in p:found[(p.split('/',1)[0],version(p.split('/',1)[1]))][c['run_id']]=(e,c)
    for (family,v),calls in prompt_versions(eval_steps).items():
        for s in calls:
            if hit:=by_run.get(s['trace_id']):found[(family,v)][s['trace_id']]=hit
    return {k:list(v.values()) for k,v in found.items()}


def eval_metrics(runs: list[tuple[dict,dict]]) -> dict[str,Any] | None:
    if not runs:return None
    cases=[c for _,c in runs]
    per=defaultdict(list)
    for e,c in runs:per[e['dataset']].append(c)
    return {'cases':len(cases),'pass_rate':pass_rate(cases),'counts':counts(cases),'judge':mean(c.get('judge') for c in cases),
        'datasets':{d:{'cases':len(cs),'pass_rate':pass_rate(cs)} for d,cs in sorted(per.items())},
        'last_run':max(e['started_at'] for e,_ in runs)}


def vnum(v: str) -> int:
    return int(v[1:]) if v[1:].isdigit() else 0


def prompt_catalogue(released: list[tuple[str,str]],in_use: dict[str,str],notes: dict[tuple,dict],live_steps: list[dict],eval_steps: list[dict],
        experiments: list[dict]) -> list[dict[str,Any]]:
    """Every prompt family: its versions on disk (newest first), the live one, the one the latest eval ran, the prompt log, and
    live (llm runs) and eval (cases it ran in) metrics per version."""
    live=prompt_versions(live_steps);evals=prompt_versions(eval_steps);cases=cases_by_prompt(experiments,eval_steps)
    families=sorted({f for f,_ in released}|set(in_use)|{f for f,_ in live if f}|{f for f,_ in cases if f})
    out=[]
    for family in families:
        versions=sorted({v for f,v in released if f==family}|{v for f,v in [*live,*cases] if f==family and v}|({in_use[family]} if family in in_use else set()),key=vnum,reverse=True)
        latest_eval=max(((m['last_run'],v) for v in versions if (m:=eval_metrics(cases.get((family,v),[])))),default=(None,None))[1]
        out.append({'family':family,'live':in_use.get(family),'latest_eval':latest_eval,
            'untested':bool(in_use.get(family)) and latest_eval is not None and latest_eval!=in_use.get(family),
            'versions':[{'version':v,'on_disk':(family,v) in set(released),'live':in_use.get(family)==v,'log':notes.get((family,v)),
                'live_metrics':call_metrics(live.get((family,v),[])),'eval_calls':call_metrics(evals.get((family,v),[])),
                'eval_metrics':eval_metrics(cases.get((family,v),[]))} for v in versions]})
    return out


# ---- Failure card ----

def bad_step(run: dict) -> bool:
    m=run['meta']
    if run['name']=='llm':return m.get('outcome') not in (None,'ok')
    if run['name']=='agent' and m.get('intent'):return agent_status(run) in BAD_AGENT
    return bool(run['error'])


def card(detail: dict) -> dict[str,Any]:
    """The debug card for one request or evaluation case (services/quality.trace_runs): what was asked, the plan, where it
    failed, what the user saw, the configuration, and for a case the expected result and the repro command."""
    runs=detail['runs'];requests=[r['request'] for r in runs if 'request' in r]
    failing=[r for r in runs if bad_step(r)]
    origin=max(failing,key=lambda r:r['depth']) if failing else None
    keep={'chat','rm_assistant','route','validate','retrieve',*NODES,'tool_call','kb.search','embed','customer_context'}
    names={r['run_id']:r['name'] for r in runs}
    wrappers={r['parent_id'] for r in runs if names.get(r['parent_id'])==r['name']}  # a LangGraph node around our span of the same name
    def repeat(r):return r['run_id'] in wrappers or (r['run_type']=='tool' and names.get(r['parent_id'])=='tool_call' and not r['error'])  # the MCP run in a tool_call
    shown=[r for r in runs if (r['name'] in keep or r['run_type'] in ('llm','tool') or bad_step(r)) and not repeat(r)][:200]
    llm=model_calls(runs)
    case,exp,expected=detail.get('case'),detail.get('experiment'),detail.get('expected') or {}
    last=requests[-1] if requests else {}
    planner=next((r['meta'] for r in runs if r['name']=='planner' and r['meta'].get('route')),{})
    return {'run_id':detail['root_id'],'url':detail.get('url'),'source':'eval' if exp else 'live','capped':detail.get('capped',False),
        'started_at':runs[0]['started_at'] if runs else None,
        'turns':[{k:r.get(k) for k in ('started_at','surface','question','answer','origin','needs_human','source_check','reject_reason','error','latency_s','route')} for r in requests],
        'asked':detail.get('asked') or [r['question'] for r in requests if r.get('question')],
        'plan':{'route':planner.get('route'),'tasks':planner.get('tasks'),'agents':[{'agent':r['meta']['agent'],'intent':r['meta'].get('intent'),'status':agent_status(r),
            'path':r['meta'].get('path'),'wave':r['meta'].get('wave'),'tool_calls':r['meta'].get('tool_calls'),'latency_s':r['latency_s']} for r in agent_runs(runs)]},
        'steps':[{'id':r['run_id'],'name':r['name'],'kind':r['run_type'],'depth':r['depth'],'status':r['meta'].get('status') or r['meta'].get('outcome') or r['run_status'],
            'error':r['error'],'latency_s':r['latency_s'],'agent':r['meta'].get('agent'),'tool':r['meta'].get('tool'),
            'prompt':f"{r['meta'].get('agent')} {version(r['meta']['prompt_version'])}" if r['name']=='llm' and r['meta'].get('prompt_version') else None,
            'failing':bad_step(r),'origin':origin is not None and r['run_id']==origin['run_id']} for r in shown],
        'failure':{'step':origin['name'],'agent':origin['meta'].get('agent'),'tool':origin['meta'].get('tool'),'error':origin['error'],
            'status':origin['meta'].get('status'),'outcome':origin['meta'].get('outcome'),'cause':origin['meta'].get('cause'),'error_type':origin['meta'].get('error_type'),
            'retries':origin['meta'].get('retries'),'reject_reason':last.get('reject_reason')} if origin else
            ({'step':'answer checks','reject_reason':last.get('reject_reason'),'error':None} if last and held_back(last) else None),
        'saw':{k:last.get(k) for k in ('answer','origin','needs_human','source_check','sources','error')} if last else None,
        'config':{'models':sorted({r['meta']['model'] for r in llm if r['meta'].get('model')}),'transports':sorted({r['meta']['transport'] for r in llm if r['meta'].get('transport')}),
            'prompts':sorted({f"{r['meta'].get('agent')} {r['meta']['prompt_version']}" for r in llm}),'pipeline':last.get('pipeline'),'app_version':last.get('app_version')},
        'eval':{'dataset':exp['dataset'],'experiment':exp['name'],'experiment_url':exp.get('url'),'case_id':case and case['case_id'],'status':case and case['status'],
            'checks':[{'key':k,**v} for k,v in sorted((case or {}).get('checks',{}).items())],
            'expected':{k:expected[k] for k in EXPECTED_SHOWN if k in expected},
            'repro':f"python -m evaluation.langsmith_run --dataset {exp['dataset']} --case {case['case_id']}" if case and case.get('case_id') else None} if exp else None,
        'repro':None if exp else (last.get('question') or None)}
