"""Public-key Data API connector; signed-in callers retain their JWT and RLS role."""
import os
from postgrest import SyncPostgrestClient
from app.schemas.contracts import RequestContext


def connect(ctx: RequestContext) -> SyncPostgrestClient:
    db=SyncPostgrestClient(os.environ['SUPABASE_URL'].rstrip('/')+'/rest/v1',
        headers={'apikey':os.environ['SUPABASE_ANON_KEY']})
    if ctx.principal.jwt is not None:db.auth(ctx.principal.jwt.get_secret_value())
    return db
