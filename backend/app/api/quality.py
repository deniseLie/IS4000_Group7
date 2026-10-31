"""Team-only Quality page: LangSmith evaluation experiments and recent /chat traces (services/quality.py)."""
from uuid import UUID
from fastapi import APIRouter,Depends,Query
from starlette.concurrency import run_in_threadpool
from app.core import auth
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.contracts import RequestContext
from app.schemas.quality import Case,Experiment,Span,Trace
from app.services import quality

router=APIRouter(prefix='/quality')


async def team(ctx: RequestContext=Depends(get_ctx)) -> RequestContext:
    if ctx.principal.kind!='customer' or not ctx.principal.jwt:raise ApiError(401,'auth_required','Please sign in.')
    claims=await run_in_threadpool(auth.verify_token,ctx.principal.jwt.get_secret_value())
    if not quality.is_team(claims):raise ApiError(403,'team_only','This page is for the DemoBank team.')
    return ctx


async def read(load,*args):
    try:return await run_in_threadpool(load,*args)
    except Exception as error:
        log_event('quality_unavailable',error_type=type(error).__name__)
        raise ApiError(503,'quality_unavailable','LangSmith cannot be read right now. Please retry.') from error


@router.get('/experiments',response_model=list[Experiment])
async def experiments(limit: int=Query(20,ge=1,le=50),ctx: RequestContext=Depends(team)) -> list[Experiment]:
    return await read(quality.experiments,limit)


@router.get('/experiments/{name}',response_model=list[Case])
async def experiment(name: str,ctx: RequestContext=Depends(team)) -> list[Case]:
    if not name.startswith(quality.DATASETS):raise ApiError(404,'not_found','No such experiment.')
    return await read(quality.experiment,name)


@router.get('/traces',response_model=list[Trace])
async def traces(limit: int=Query(30,ge=1,le=100),ctx: RequestContext=Depends(team)) -> list[Trace]:
    return await read(quality.traces,limit)


@router.get('/traces/{run_id}',response_model=list[Span])
async def trace(run_id: UUID,ctx: RequestContext=Depends(team)) -> list[Span]:
    return await read(quality.trace,str(run_id))
