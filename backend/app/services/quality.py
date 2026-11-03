"""Team-only Quality page (LangSmith read-back): evaluation experiments and recent /chat traces.

The backend reads LangSmith with its own server key; the browser never sees it. Only signed-in staff whose
administrator-owned app_metadata.demobank_role is a team role may read it (user_metadata is never trusted). Results are
cached briefly so the page does not hammer the LangSmith API. Text shown here already passed the redacting client."""
import os
import time
from itertools import islice
from typing import Any
from app.core import langsmith_client
from app.schemas.quality import Case,Check,Experiment,Span,Trace

TEAM_ROLES={'rm','support'}
DATASETS=('demobank-qa1-client','demobank-qa1-rm','demobank-focused','demobank-standard','demobank-safety')
CACHE_S=60
_cache: dict[tuple,tuple[float,Any]]={}
_client=None


def is_team(claims: dict[str,Any]) -> bool:
    return (claims.get('app_metadata') or {}).get('demobank_role') in TEAM_ROLES


def client():
    global _client
    if _client is None:_client=langsmith_client.client()
    return _client


def cached(key: tuple,load):
    hit=_cache.get(key)
    if hit and hit[0]>time.monotonic():return hit[1]
    value=load()
    _cache[key]=(time.monotonic()+CACHE_S,value)
    return value


def checks_of(stats: dict[str,Any] | None) -> list[Check]:
    """One check per feedback key from the run's feedback stats (avg is the 0/0.5/1 score of a single run)."""
    return [Check(key=k,score=v.get('avg'),comment=next(iter(v.get('comments') or []),'') or '') for k,v in sorted((stats or {}).items())]


def status(checks: list[Check],error: str | None) -> str:
    if error:return 'error'
    scores=[c.score for c in checks if not c.key.startswith('judge')]
    if any(s==0 for s in scores):return 'fail'
    return 'pass' if any(s is not None for s in scores) else 'skipped'


def experiments(limit: int=20) -> list[Experiment]:
    def load():
        found=[]
        for dataset in DATASETS:
            try:projects=list(islice(client().list_projects(reference_dataset_name=dataset),limit))
            except Exception as error:
                if 'not found' in str(error).lower():continue  # a dataset that was never synced
                raise
            for p in projects:
                meta=(p.extra or {}).get('metadata',{})
                found.append(Experiment(name=p.name,dataset=dataset,started_at=p.start_time,pipeline=meta.get('pipeline'),
                    model=meta.get('model'),answer_prompt=meta.get('answer_prompt'),judge_model=meta.get('judge_model'),url=p.url))
        return sorted(found,key=lambda e:e.started_at,reverse=True)[:limit]
    return cached(('experiments',limit),load)


def experiment(name: str) -> list[Case]:
    def load():
        project=client().read_project(project_name=name)
        dataset=client().read_dataset(dataset_id=project.reference_dataset_id)
        ids={e.id:(e.metadata or {}).get('case_id') for e in client().list_examples(dataset_id=dataset.id)}
        cases=[]
        for run in client().list_runs(project_name=name,is_root=True):
            out=run.outputs or {}
            checks=checks_of(run.feedback_stats)
            cases.append(Case(case_id=ids.get(run.reference_example_id),status=status(checks,run.error),checks=checks,
                answer=(out.get('answer') or '')[:2000],agents=out.get('agents') or {},tools=out.get('tools') or [],
                model_calls=out.get('model_calls'),latency_s=run.latency,tokens=run.total_tokens,error=(run.error or '')[:300] or None,
                run_id=str(run.id)))
        return sorted(cases,key=lambda c:c.case_id or '')
    return cached(('experiment',name),load)


def project() -> str:
    return os.getenv('LANGSMITH_PROJECT') or 'default'


def traces(limit: int=30) -> list[Trace]:
    def load():
        runs=islice(client().list_runs(project_name=project(),is_root=True,filter='eq(name, "chat")',limit=limit),limit)
        return [Trace(run_id=str(r.id),started_at=r.start_time,latency_s=r.latency,status=r.status,tokens=r.total_tokens,
            mode=(r.extra or {}).get('metadata',{}).get('mode'),error=(r.error or '')[:300] or None) for r in runs]
    return cached(('traces',limit),load)


def trace(run_id: str) -> list[Span]:
    def load():
        root=client().read_run(run_id)
        kids=sorted(islice(client().list_runs(trace_id=root.trace_id),500),key=lambda r:(r.dotted_order or ''))
        return [Span(name=r.name,run_type=r.run_type,depth=(r.dotted_order or '').count('.'),latency_s=r.latency,status=r.status,
            tokens=r.total_tokens,agent=(r.extra or {}).get('metadata',{}).get('agent'),tool=(r.extra or {}).get('metadata',{}).get('tool'),
            error=(r.error or '')[:300] or None) for r in kids]
    return cached(('trace',run_id),load)
