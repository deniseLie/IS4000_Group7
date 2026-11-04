"""Admin dashboard (AI engineering view): LangSmith evaluation experiments, prompts, failures and recent /chat and /rm/assistant traces.
Every route needs a verified JWT whose administrator-owned app_metadata.demobank_role is 'admin' (services/quality.py)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any
from uuid import UUID
from fastapi import APIRouter,Depends,Query
from starlette.concurrency import run_in_threadpool
from app.core import auth
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.contracts import RequestContext
from app.schemas.quality import Span,Trace
from app.services import insights,prompts,quality

WINDOW=Query('7d',pattern='^(24h|7d|30d|90d)$')
NAME=r'^demobank-[A-Za-z0-9_.-]{1,120}$'  # an experiment of a synced dataset (quality.DATASET_PREFIX)

router=APIRouter(prefix='/admin')


async def admin(ctx: RequestContext=Depends(get_ctx)) -> RequestContext:
    if ctx.principal.kind!='customer' or not ctx.principal.jwt:raise ApiError(401,'auth_required','Please sign in.')
    claims=await run_in_threadpool(auth.verify_token,ctx.principal.jwt.get_secret_value())
    if not quality.is_admin(claims):raise ApiError(403,'admin_only','This page is for DemoBank administrators.')
    return ctx


async def read(load,*args):
    try:return await run_in_threadpool(load,*args)
    except Exception as error:
        log_event('admin_unavailable',error_type=type(error).__name__)
        raise ApiError(503,'admin_unavailable','LangSmith cannot be read right now. Please retry.') from error


@router.get('/traces',response_model=list[Trace])
async def traces(limit: int=Query(30,ge=1,le=100),ctx: RequestContext=Depends(admin)) -> list[Trace]:
    return await read(quality.traces,limit)


@router.get('/traces/{run_id}',response_model=list[Span])
async def trace(run_id: UUID,ctx: RequestContext=Depends(admin)) -> list[Span]:
    return await read(quality.trace,str(run_id))


# ---- Dashboard pages: LangSmith reads (services/quality.py) and pure arithmetic (services/insights.py) ----

def capped(rows: list,steps: list) -> bool:
    return len(rows)>=quality.MAX_REQUESTS or len(steps)>=quality.MAX_STEPS


def together(*loads):
    """Independent LangSmith reads at the same time (each is cached on its own)."""
    with ThreadPoolExecutor(len(loads)) as pool:return [f.result() for f in [pool.submit(*load) for load in loads]]


def overview_of(days: int) -> dict[str,Any]:
    rows,steps,exps=together((quality.requests,'live',2*days),(quality.steps,'live',2*days),(quality.experiment_runs,))
    month=[e for e in exps if e['started_at']>=quality.now()-timedelta(days=30)]
    untested=insights.untested_prompts(prompts.in_use(),[],month)
    return {**insights.overview(rows,steps,exps,quality.now(),days,untested),'capped':capped(rows,steps)}


def failures_of(days: int,source: str) -> dict[str,Any]:
    rows,steps,exps=together((quality.requests,source,2*days),(quality.steps,source,2*days),(quality.experiment_runs,))
    return {**insights.failures(rows,steps,exps,quality.now(),days,source),'capped':capped(rows,steps)}


@router.get('/overview')
async def overview(window: str=WINDOW,ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    return await read(overview_of,quality.WINDOWS[window])


@router.get('/failures')
async def failures(window: str=WINDOW,source: str=Query('all',pattern='^(live|eval|all)$'),ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    return await read(failures_of,quality.WINDOWS[window],source)


@router.get('/failures/{run_id}')
async def failure(run_id: UUID,ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    return await read(lambda:insights.card(quality.trace_runs(str(run_id))))


async def named(name: str) -> dict[str,Any]:
    found=next((e for e in await read(quality.experiment_runs) if e['name']==name),None)
    if found is None:raise ApiError(404,'not_found','No such experiment.')
    return found


@router.get('/evaluations')
async def evaluations(ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    exps,sets=await read(lambda:together((quality.experiment_runs,),(quality.datasets,)))
    return {'datasets':insights.evaluations(exps,sets,quality.now())}


@router.get('/evaluations/compare')
async def compare(a: str=Query(...,pattern=NAME),b: str=Query(...,pattern=NAME),ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    first,second=await named(a),await named(b)
    if first['dataset']!=second['dataset']:raise ApiError(422,'different_datasets','Compare two runs of the same dataset.')
    return insights.compare(first,second)


@router.get('/evaluations/{name}')
async def evaluation(name: str,ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    if not name.startswith(quality.DATASET_PREFIX):raise ApiError(404,'not_found','No such experiment.')
    return insights.experiment_detail(await named(name))


def prompts_of(days: int) -> dict[str,Any]:
    live,evals,exps=together((quality.steps,'live',days),(quality.steps,'eval',days),(quality.experiment_runs,))
    since=quality.now()-timedelta(days=days)
    return {'window_days':days,'families':insights.prompt_catalogue(prompts.released(),prompts.in_use(),prompts.notes(),live,evals,
        [e for e in exps if e['started_at']>=since]),'capped':len(live)>=quality.MAX_STEPS}


@router.get('/prompts')
async def prompt_page(window: str=WINDOW,ctx: RequestContext=Depends(admin)) -> dict[str,Any]:
    return await read(prompts_of,quality.WINDOWS[window])
