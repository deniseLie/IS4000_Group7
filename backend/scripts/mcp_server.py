"""Run the DemoBank tools as an MCP server over stdio (F21), for an external MCP client such as the MCP Inspector.

  python -m scripts.mcp_server                       # guest: knowledge_search and request_categories only
  DEMOBANK_ACCESS_TOKEN=<customer JWT> python -m scripts.mcp_server   # also my_tickets, my_cards, my_transactions

Needs SUPABASE_URL and SUPABASE_ANON_KEY (root .env) and the optional `mcp` package. The token is a normal
signed-in customer's Supabase access token; it is verified exactly like the API gateway does, RLS applies and
each tool call writes its own audit batch. Nothing here can change banking records.
"""
import functools
import os
from uuid import uuid4
from app.core import auth
from app.core.gateway import _customer_id
from app.schemas.contracts import Principal,RequestContext
from app.services import audit
from app.services.tools import TOOL_CTX,TOOLS


def context() -> tuple[RequestContext,object]:
    token=os.getenv('DEMOBANK_ACCESS_TOKEN','').strip()
    if not token:return RequestContext(session_id=uuid4(),principal=Principal(kind='guest')),None
    claims=auth.verify_token(token)
    db=auth.user_db(token)
    principal=Principal(kind='customer',user_id=claims['sub'],customer_id=_customer_id(db,claims['sub']),auth_session_id=claims['session_id'],
        aal='aal2' if claims.get('aal')=='aal2' else 'aal1',jwt=token)
    return RequestContext(session_id=claims['session_id'],principal=principal),db


def audited(function,ctx: RequestContext,db: object):
    @functools.wraps(function)
    async def run(*args,**kwargs):
        token=TOOL_CTX.set(ctx.model_copy(update={'request_id':uuid4()}))
        batch=audit.start_batch(db,session_id=ctx.session_id)
        try:return await function(*args,**kwargs)
        finally:
            TOOL_CTX.reset(token)
            await batch.flush()
    return run


def main() -> None:
    from mcp.server.mcpserver import MCPServer
    ctx,db=context()
    server=MCPServer('demobank-tools',instructions='DemoBank coursework tools: read-only, permission-checked and audited.')
    for name,function in TOOLS.items():server.tool(name=name,description=(function.__doc__ or name).strip())(audited(function,ctx,db))
    server.run('stdio')


if __name__=='__main__':main()
