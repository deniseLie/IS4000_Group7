"""Publish a local evaluation's results to LangSmith as an experiment on a `demobank-` dataset, so the admin dashboard shows
it beside the langsmith_run datasets (quality.datasets lists every `demobank-` dataset; a case fails when any non-judge
check scores 0, quality.status).

The dataset mirrors the whole case file: examples keyed by stable case ids (langsmith_sync.example_id) are created,
updated or deleted to match it, and its version is tagged sha-<file hash>. One publication is one experiment (metadata:
the file tag, case count, partial, model, prompts, app version) with one run per case: its outputs, its measured time and
one feedback score per check (1 pass, 0 fail, between for a graded check; None is not sent); a refused run (e.g. a 429
usage limit) fails the publication, and an experiment with no runs recorded is deleted again. Run start times are laid end
to end from the publication time; the durations are the measured ones. Synthetic cases only; inputs and outputs pass the
redacting scrub. Writes to LangSmith only, never to the app's database.

Used by retrieval.py, talking_points.py and meeting_boundaries.py (--publish)."""
import hashlib
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.langsmith_client import scrub
from evaluation.langsmith_sync import example_id,plan


@dataclass
class Case:
    """One example: what the case gives (`say` or `turns` is what the dashboard shows as asked) and its reference
    (`check` is the line the dashboard shows as expected)."""
    id: str
    inputs: dict[str,Any]
    expected: dict[str,Any]
    metadata: dict[str,Any]=field(default_factory=dict)


@dataclass
class Result:
    """One case run: what it did (`answer` is the line the dashboard shows) and {check: (score, comment)}."""
    id: str
    outputs: dict[str,Any]
    checks: dict[str,tuple[float | None,str]]
    ms: float | None=None
    error: str | None=None


def tag(source: Path) -> str:
    return 'sha-'+hashlib.sha256(source.read_bytes()).hexdigest()[:12]


def examples(dataset: str,cases: list[Case]) -> list[dict]:
    ids=[c.id for c in cases]
    if len(set(ids))!=len(ids):raise ValueError(f'{dataset}: duplicate case id')
    return [{'id':example_id(dataset,c.id),'inputs':scrub(c.inputs),'outputs':scrub(c.expected),'metadata':{'case_id':c.id,'dataset':dataset}|c.metadata}
        for c in cases]


def sync(client,dataset: str,description: str,source: Path,built: list[dict]):
    """The dataset equal to the case file; returns (dataset, {create, update, delete})."""
    ds=client.read_dataset(dataset_name=dataset) if client.has_dataset(dataset_name=dataset) else \
        client.create_dataset(dataset,description=description,metadata={'source':f'backend/evaluation/{source.name}'})
    keys={k for e in built for k in e['metadata']}
    remote={str(e.id):{'inputs':e.inputs,'outputs':e.outputs or {},'metadata':{k:v for k,v in (e.metadata or {}).items() if k in keys}}
        for e in client.list_examples(dataset_id=ds.id)}
    create,update,delete=plan(built,remote)
    if create:client.create_examples(dataset_id=ds.id,examples=create)
    if update:client.update_examples(dataset_id=ds.id,updates=update)
    if delete:client.delete_examples(delete)
    latest=max(v.as_of for v in client.list_dataset_versions(dataset_id=ds.id))
    client.update_dataset_tag(dataset_id=ds.id,as_of=latest,tag=tag(source))
    return ds,{'create':len(create),'update':len(update),'delete':len(delete)}


def publish(dataset: str,description: str,source: Path,cases: list[Case],results: list[Result],label: str,meta: dict[str,Any],client=None) -> dict:
    """Sync the dataset, then record `results` (a subset of `cases` is a partial run) as one experiment."""
    from app.core.observability import app_version
    if client is None:  # unbatched: a refused run (e.g. a 429 usage limit) raises here instead of only being logged by the queue
        from app.core.langsmith_client import client as make
        client=make(auto_batch_tracing=False)
    known={c.id:c for c in cases}
    if missing:=[r.id for r in results if r.id not in known]:raise ValueError(f'{dataset}: results for unknown cases {missing[:3]}')
    if not results:raise ValueError(f'{dataset}: no results to publish')
    ds,changes=sync(client,dataset,description,source,examples(dataset,cases))
    stamp=datetime.now(timezone.utc)
    name=f'{dataset}-{label}-{stamp:%Y%m%d-%H%M%S}'
    experiment=client.create_project(name,reference_dataset_id=ds.id,description=description,metadata={'dataset':dataset,'dataset_file':source.name,
        'dataset_file_tag':tag(source),'case_count':len(results),'partial':len(results)<len(cases),'app_version':app_version(),'publisher':'evaluation.publish',**meta})
    clock,sent=stamp,0
    try:
        for r in results:
            run_id=uuid4();start=clock;end=start+timedelta(milliseconds=r.ms or 0);clock=end+timedelta(microseconds=1)
            client.create_run(r.id,scrub(known[r.id].inputs),'chain',id=run_id,trace_id=run_id,dotted_order=f'{start:%Y%m%dT%H%M%S%fZ}{run_id}',
                project_name=name,reference_example_id=example_id(dataset,r.id),outputs=scrub(r.outputs),error=r.error,start_time=start,end_time=end,
                tags=['eval',dataset],extra={'metadata':{'case_id':r.id,'eval_run':name}})
            for key,(score,comment) in r.checks.items():
                if score is not None:client.create_feedback(run_id,key,score=score,comment=comment or None,session_id=experiment.id)
            sent+=1
        client.flush()
    except Exception as error:
        kept='kept'
        if not sent:  # nothing recorded: remove the empty experiment so the dashboard does not list it
            try:client.delete_project(project_id=experiment.id);kept='removed'
            except Exception:pass
        raise RuntimeError(f'LangSmith refused the runs of {name} after {sent} of {len(results)} cases (experiment {kept}): '
            f'{type(error).__name__}: {str(error)[:300]}') from error
    return {'dataset':dataset,'experiment':name,'url':experiment.url,'cases':len(results),**changes}


def scored(ok: bool,comment: str='') -> tuple[float,str]:
    """A pass/fail check: 1 or 0, with the comment kept only on a failure."""
    return (1.0,'') if ok else (0.0,comment)
