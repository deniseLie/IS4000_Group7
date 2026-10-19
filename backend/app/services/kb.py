"""Read-only versioned knowledge RPCs. Empty results are explicit; database failures propagate.

Callers supply the public or user-JWT client and flush their audit batch before releasing data.
The approved in-memory v1 library remains independent of this database service.
"""
from postgrest import SyncPostgrestClient
from pydantic import TypeAdapter
from app.core.data_api import execute
from app.schemas.contracts import RequestContext
from app.schemas.knowledge import FactId,KnowledgeFact,KnowledgeQuery,KnowledgeSection,SectionKey,StoredSection
from app.services import audit
from app.services.authorization import require_permission

STUB=False


class KnowledgeResponseError(ValueError):
    """A knowledge RPC returned inconsistent or ambiguous records."""


async def search(query: KnowledgeQuery,ctx: RequestContext,db: SyncPostgrestClient) -> list[KnowledgeSection]:
    require_permission(ctx.principal,'kb.search',None,ctx)
    response=await execute(lambda:db.rpc('kb_search',{'p_query':query.query,
        'p_categories':[] if query.scope=='public' else [query.scope],'p_k':query.k}).execute(),'kb.search')
    sections=TypeAdapter(list[KnowledgeSection]).validate_python(response.data)
    if len(sections)>query.k or len({item.cite for item in sections})!=len(sections):
        raise KnowledgeResponseError('kb_search returned too many results or duplicate citations')
    if any(item.score<=0 or not item.current for item in sections):
        raise KnowledgeResponseError('kb_search returned an unranked or out-of-review section')
    audit.add('data','data.read',resource_type='kb.sections',details={'tool':'kb.search','result_count':len(sections)})
    return sections


async def get_fact(fact_id: str,ctx: RequestContext,db: SyncPostgrestClient) -> KnowledgeFact | None:
    require_permission(ctx.principal,'kb.get_fact',None,ctx)
    identifier=TypeAdapter(FactId).validate_python(fact_id)
    response=await execute(lambda:db.rpc('kb_get_fact',{'p_fact_id':identifier}).execute(),'kb.get_fact')
    facts=TypeAdapter(list[KnowledgeFact]).validate_python(response.data)
    if len(facts)>1:raise KnowledgeResponseError('kb_get_fact returned multiple records for one fact id')
    audit.add('data','data.read',resource_type='kb.facts',details={'tool':'kb.get_fact','result_count':len(facts)})
    return facts[0] if facts else None


async def get_section(key: SectionKey,ctx: RequestContext,db: SyncPostgrestClient) -> StoredSection | None:
    require_permission(ctx.principal,'kb.search',None,ctx)
    response=await execute(lambda:db.rpc('kb_get_section',{'p_article_id':key.article_id,
        'p_version':key.version,'p_anchor':key.anchor}).execute(),'kb.search')
    sections=TypeAdapter(list[StoredSection]).validate_python(response.data)
    if len(sections)>1 or any(item.key!=key for item in sections):
        raise KnowledgeResponseError('kb_get_section returned an ambiguous or different section')
    audit.add('data','data.read',resource_type='kb.sections',details={'tool':'kb.search','result_count':len(sections)})
    return sections[0] if sections else None
