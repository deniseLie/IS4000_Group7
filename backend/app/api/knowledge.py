"""Immutable database source details used by the existing chat source dialog."""
import httpx
from typing import Annotated
from fastapi import APIRouter,Depends,Path,Request
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.api.audited import flush as flush_audit
from app.core import knowledge_db
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.contracts import RequestContext
from app.schemas.knowledge import Anchor,ArticleId,KnowledgeDetails,SectionKey
from app.services import audit,kb
from app.services.authorization import PolicyDenied

router=APIRouter()


@router.get('/knowledge/sections/{article_id}/{version}/{anchor}',response_model=KnowledgeDetails)
async def section(article_id: ArticleId,version: Annotated[int,Path(gt=0,le=2147483647)],anchor: Anchor,request: Request,
    ctx: RequestContext=Depends(get_ctx)) -> KnowledgeDetails:
    key=SectionKey(article_id=article_id,version=version,anchor=anchor)
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:
        with knowledge_db.connect(ctx) as db:record=await kb.get_section(key,ctx,db)
    except PolicyDenied as error:
        await flush_audit(batch)
        raise ApiError(403,'knowledge_forbidden','This session cannot read the requested source.') from error
    except (APIError,httpx.TransportError,ValidationError,kb.KnowledgeResponseError) as error:
        batch.add('data','data.read','failed',resource_type='kb.sections',details={'tool':'kb.search','error_type':type(error).__name__})
        await flush_audit(batch)
        log_event('knowledge_read_failed',error_type=type(error).__name__)
        raise ApiError(503,'knowledge_unavailable','The knowledge source cannot be loaded. Please retry.') from error
    await flush_audit(batch)
    if record is None:raise ApiError(404,'knowledge_not_found','The requested knowledge section was not found.')
    return KnowledgeDetails(**record.source().model_dump(),content=record.body,notice=record.scope)
