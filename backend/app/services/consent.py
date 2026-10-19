"""JWT/RLS-scoped consent lookup. Never substitutes a missing registry or failed read with a denial.

The Action Service must recheck inside its transaction before sharing, using the categories of
the actual selected records. Model-supplied labels cannot establish what a summary contains.
"""
from postgrest import SyncPostgrestClient
from pydantic import JsonValue,TypeAdapter
from app.core.data_api import execute
from app.core.observability import span
from app.schemas.consent import ConsentQuery,ConsentResult
from app.schemas.contracts import RequestContext
from app.services import audit
from app.services.authorization import require_permission

class ConsentResponseError(ValueError):
    """The registry returned a response that cannot establish consent."""


async def has_consent(query: ConsentQuery,ctx: RequestContext,db: SyncPostgrestClient) -> ConsentResult:
    require_permission(ctx.principal,'consent.check',{'customer_id':ctx.principal.customer_id},ctx)
    params: dict[str,JsonValue]={'p_customer_id':ctx.principal.customer_id,'p_purpose':query.purpose,
        'p_recipient':str(query.recipient),'p_categories':list(query.categories)}
    with span('customer_context','tool',tool='consent.check',category_count=len(query.categories)):
        response=await execute(lambda:db.rpc('consent_check',params).execute(),'consent.check')
        results=TypeAdapter(list[ConsentResult]).validate_python(response.data)
        if len(results)!=1:raise ConsentResponseError(f'consent_check returned {len(results)} rows; expected one scoped result')
    result=results[0]
    audit.add('data','data.read',resource_type='consent.grants',details={'tool':'consent.check','category_count':len(query.categories)})
    audit.add('consent','consent.checked',resource_type='consent.grants',resource_id=str(result.grant_id) if result.grant_id else None,
        details={'granted':result.granted,'category_count':len(query.categories)})
    return result
