"""Local tool layer (F21): each tool is one audited service call with the caller's own RequestContext.

Agents reach these tools directly (TOOL_TRANSPORT=direct, the default) or through the Model Context Protocol
(TOOL_TRANSPORT=mcp): app/mcp_server.py exposes the same functions as an MCP server, used in-process by the
orchestrator and over stdio by external MCP clients (python -m scripts.mcp_server). Either way, policy
(require_permission), JWT + RLS and audit are applied inside the tool, never by the caller or the model.
"""
from collections.abc import Awaitable,Callable
from contextvars import ContextVar
from typing import Any
from app.core import data_api
from app.core.config import settings
from app.core.observability import span
from app.schemas.contracts import RequestContext
from app.schemas.knowledge import KnowledgeQuery
from app.services import customer_data,kb

TOOL_CTX: ContextVar[RequestContext | None]=ContextVar('tool_ctx',default=None)


class ToolContextMissing(PermissionError):
    """A tool ran without a backend-assigned request context."""


def _ctx() -> RequestContext:
    ctx=TOOL_CTX.get()
    if ctx is None:raise ToolContextMissing('Tools run only inside a request context set by the backend')
    return ctx


async def knowledge_search(query: str,k: int=4) -> dict[str,Any]:
    """Search approved DemoBank knowledge (public scope). Returns cited sections."""
    ctx=_ctx()
    with data_api.connect(ctx) as db:sections=await kb.search(KnowledgeQuery(query=query,scope='public',k=k),ctx,db)
    return {'items':[item.model_dump(mode='json') for item in sections]}


async def request_categories() -> dict[str,Any]:
    """List the request categories, what each needs and which team decides."""
    return {'items':[item.model_dump(mode='json') for item in await customer_data.get_categories(_ctx())]}


async def my_tickets() -> dict[str,Any]:
    """The signed-in customer's own requests with plain-language status (no internal notes)."""
    result=await customer_data.get_tickets(_ctx())
    return {'items':result.data,'call_id':str(result.call_id)}


async def my_cards() -> dict[str,Any]:
    """The signed-in customer's own cards, masked to the last four digits."""
    result=await customer_data.get_credit_cards(_ctx())
    return {'items':result.data,'call_id':str(result.call_id)}


async def my_transactions(status: str | None=None) -> dict[str,Any]:
    """The signed-in customer's recent transactions, optionally only 'pending' or 'posted' ones."""
    result=await customer_data.get_transactions(_ctx(),None,None,{'status':status} if status else None)
    return {'items':result.data,'call_id':str(result.call_id)}


TOOLS: dict[str,Callable[...,Awaitable[dict[str,Any]]]]={'knowledge_search':knowledge_search,'request_categories':request_categories,
    'my_tickets':my_tickets,'my_cards':my_cards,'my_transactions':my_transactions}


async def call(name: str,args: dict[str,Any],ctx: RequestContext) -> dict[str,Any]:
    """The one way agents call a tool. The transport is a setting; the tool, policy and audit are the same."""
    if name not in TOOLS:raise KeyError(f'Unknown tool {name}')
    token=TOOL_CTX.set(ctx)
    try:
        with span('tool_call','tool',tool=name,transport=settings.tool_transport):
            if settings.tool_transport=='mcp':
                from app.mcp_server import call_in_process
                return await call_in_process(name,args)
            return await TOOLS[name](**args)
    finally:TOOL_CTX.reset(token)
