"""Read-only versioned knowledge RPCs. Empty results are explicit; database failures propagate.

Callers supply the public or user-JWT client and flush their audit batch before releasing data.
Every pipeline reads approved knowledge only from here; there is no in-memory copy.
"""
import logging
import time
from postgrest import SyncPostgrestClient
from pydantic import TypeAdapter
from app.core.config import settings
from app.core.data_api import execute
from app.core.observability import log_event,span
from app.schemas.contracts import RequestContext
from app.schemas.knowledge import ArticleId,KnowledgeQuery,KnowledgeSection,SectionKey,StoredSection
from app.services import audit,embeddings
from app.services.authorization import require_permission
from app.services.typos import for_search

STUB=False


class KnowledgeResponseError(ValueError):
    """A knowledge RPC returned inconsistent or ambiguous records."""


async def search(query: KnowledgeQuery,ctx: RequestContext,db: SyncPostgrestClient) -> list[KnowledgeSection]:
    """Fuses keyword and embedding ranks (0099). Without an embedding key, or if the provider fails, the keyword search
    still runs and the fallback is recorded (mode keyword_only, with the reason) in the audit event, log and LangSmith run."""
    require_permission(ctx.principal,'kb.search',None,ctx)
    text=for_search(query.query)
    args={'p_query':text,'p_categories':[] if query.scope=='public' else [query.scope],'p_k':query.k}
    timing,failure={},None
    with span('kb.search','tool',respell=settings.knowledge_respell) as attrs:
        try:
            embedded=await embeddings.embed_query(text)
            args|={'p_embedding':embedded.vectors[0],'p_model':embedded.model,'p_min_similarity':settings.embedding_min_similarity}
            timing['embed_ms']=embedded.ms
        except embeddings.EmbeddingError as error:failure=error.reason
        start=time.perf_counter()
        response=await execute(lambda:db.rpc('kb_search' if failure else 'kb_hybrid_search',args).execute(),'kb.search')
        timing['sql_ms']=round((time.perf_counter()-start)*1000)
        mode='keyword_only' if failure else 'hybrid'
        attrs.update(mode=mode,count=len(response.data or []),embedding_error=failure,**timing)
    # A missing key is a set-up choice; a provider failure is worth a warning.
    log_event('knowledge_search',logging.WARNING if failure and failure!='not_configured' else logging.INFO,mode=mode,
        embedding_error=failure,result_count=len(response.data or []),**timing)
    sections=TypeAdapter(list[KnowledgeSection]).validate_python(response.data)
    if len(sections)>query.k or len({item.cite for item in sections})!=len(sections):
        raise KnowledgeResponseError('kb_search returned too many results or duplicate citations')
    if any(item.score<=0 or not item.current for item in sections):
        raise KnowledgeResponseError('kb_search returned an unranked or out-of-review section')
    audit.add('data','data.read',resource_type='kb.sections',details={'tool':'kb.search','result_count':len(sections),
        'mode':mode,**({'embedding_error':failure} if failure else {})})
    return sections


async def get_article(article_id: str,ctx: RequestContext,db: SyncPostgrestClient) -> list[KnowledgeSection]:
    """The published, in-review sections of one article in anchor order (0100); empty when it is retired or unknown."""
    require_permission(ctx.principal,'kb.search',None,ctx)
    identifier=TypeAdapter(ArticleId).validate_python(article_id)
    response=await execute(lambda:db.rpc('kb_get_article',{'p_article_id':identifier}).execute(),'kb.search')
    sections=TypeAdapter(list[KnowledgeSection]).validate_python(response.data)
    if any(item.article_id!=identifier or not item.current for item in sections) or len({item.cite for item in sections})!=len(sections):
        raise KnowledgeResponseError('kb_get_article returned another article, an out-of-review section or duplicates')
    audit.add('data','data.read',resource_type='kb.sections',details={'tool':'kb.search','result_count':len(sections)})
    return sections


async def get_section(key: SectionKey,ctx: RequestContext,db: SyncPostgrestClient) -> StoredSection | None:
    require_permission(ctx.principal,'kb.search',None,ctx)
    response=await execute(lambda:db.rpc('kb_get_section',{'p_article_id':key.article_id,
        'p_version':key.version,'p_anchor':key.anchor}).execute(),'kb.search')
    sections=TypeAdapter(list[StoredSection]).validate_python(response.data)
    if len(sections)>1 or any(item.key!=key for item in sections):
        raise KnowledgeResponseError('kb_get_section returned an ambiguous or different section')
    audit.add('data','data.read',resource_type='kb.sections',details={'tool':'kb.search','result_count':len(sections)})
    return sections[0] if sections else None
