"""Local tool layer (F25): each tool is one audited service call with the caller's own RequestContext.

Agents read knowledge and the customer's records only through call(). With TOOL_TRANSPORT=mcp (the default) every
call is an MCP tools/call to app/mcp_server.py, in-process; TOOL_TRANSPORT=direct runs the same function without MCP.
External MCP clients use the same server over stdio (python -m scripts.mcp_server). Either way, policy
(require_permission, including the calling agent's allow-list), JWT + RLS and audit are applied inside the tool,
never by the caller or the model. Customer tools return the reader's ToolResult (data, call_id, as_of).
Action and upload paths (handovers, tickets, document storage) keep their own service calls; they are not agent reads.
RM tools wait for the RM read functions (F14).
"""
from collections.abc import Awaitable,Callable
from contextvars import ContextVar
from datetime import date
from typing import Any
from app.core import data_api
from app.core.config import settings
from app.core.observability import span
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.knowledge import KnowledgeQuery
from app.schemas.relationship import SlotsQuery
from app.services import customer_data,kb,prechecks

TOOL_CTX: ContextVar[RequestContext | None]=ContextVar('tool_ctx',default=None)


class ToolContextMissing(PermissionError):
    """A tool ran without a backend-assigned request context."""


def _ctx() -> RequestContext:
    ctx=TOOL_CTX.get()
    if ctx is None:raise ToolContextMissing('Tools run only inside a request context set by the backend')
    return ctx


def _out(result: ToolResult) -> dict[str,Any]:
    return result.model_dump(mode='json')


async def knowledge_search(query: str,k: int=4) -> dict[str,Any]:
    """Search approved DemoBank knowledge (public scope). Returns cited sections."""
    ctx=_ctx()
    with data_api.connect(ctx) as db:sections=await kb.search(KnowledgeQuery(query=query,scope='public',k=k),ctx,db)
    return {'items':[item.model_dump(mode='json') for item in sections]}


async def request_categories() -> dict[str,Any]:
    """List the request categories, what each needs and which team decides."""
    return {'items':[item.model_dump(mode='json') for item in await customer_data.get_categories(_ctx())]}


async def my_profile() -> dict[str,Any]:
    """The signed-in customer's own profile summary."""
    return _out(await customer_data.get_profile(_ctx()))


async def my_accounts() -> dict[str,Any]:
    """The signed-in customer's own accounts and balances, with masked account numbers."""
    return _out(await customer_data.get_accounts(_ctx()))


async def my_cards() -> dict[str,Any]:
    """The signed-in customer's own cards, masked to the last four digits."""
    return _out(await customer_data.get_credit_cards(_ctx()))


async def my_loans() -> dict[str,Any]:
    """The signed-in customer's own existing loans and their recorded status."""
    return _out(await customer_data.get_loans(_ctx()))


async def my_transactions(from_date: str | None=None,to_date: str | None=None,status: str | None=None) -> dict[str,Any]:
    """The signed-in customer's own transactions between two ISO dates, optionally only 'pending' or 'posted' ones."""
    return _out(await customer_data.get_transactions(_ctx(),date.fromisoformat(from_date) if from_date else None,
        date.fromisoformat(to_date) if to_date else None,{'status':status} if status else None))


async def my_tickets() -> dict[str,Any]:
    """The signed-in customer's own requests with plain-language status (no internal notes)."""
    return _out(await customer_data.get_tickets(_ctx()))


async def my_compliance_case() -> dict[str,Any]:
    """The signed-in customer's latest client-visible compliance review, checklist and bank requests."""
    return _out(await customer_data.get_compliance_case(_ctx()))


async def my_documents() -> dict[str,Any]:
    """Metadata of the signed-in customer's own registered documents (no contents or storage paths)."""
    return _out(await customer_data.get_documents(_ctx()))


async def my_document_checks() -> dict[str,Any]:
    """The signed-in customer's review workspace: requested items, current uploads and recorded preliminary checks."""
    return _out(await prechecks.workspace(_ctx()))


async def my_relationship_manager() -> dict[str,Any]:
    """The signed-in customer's current and most recent previous relationship manager."""
    return _out(await customer_data.get_rm_assignment(_ctx()))


async def my_rm_introduction() -> dict[str,Any]:
    """The current relationship manager and the approved wording for a recorded change."""
    return _out(await customer_data.get_introduction(_ctx()))


async def my_requests() -> dict[str,Any]:
    """The signed-in customer's own requests to their relationship manager."""
    return _out(await customer_data.get_requests(_ctx()))


async def my_appointments() -> dict[str,Any]:
    """The signed-in customer's own appointments with their relationship manager."""
    return _out(await customer_data.get_appointments(_ctx()))


async def free_slots(from_date: str,to_date: str,limit: int=20) -> dict[str,Any]:
    """Free times in the current relationship manager's calendar between two ISO dates (suggestions, not holds)."""
    query=SlotsQuery(from_date=date.fromisoformat(from_date),to_date=date.fromisoformat(to_date),limit=limit)
    return _out(await customer_data.get_free_slots(query,_ctx()))


TOOLS: dict[str,Callable[...,Awaitable[dict[str,Any]]]]={'knowledge_search':knowledge_search,'request_categories':request_categories,
    'my_profile':my_profile,'my_accounts':my_accounts,'my_cards':my_cards,'my_loans':my_loans,'my_transactions':my_transactions,
    'my_tickets':my_tickets,'my_compliance_case':my_compliance_case,'my_documents':my_documents,'my_document_checks':my_document_checks,
    'my_relationship_manager':my_relationship_manager,'my_rm_introduction':my_rm_introduction,'my_requests':my_requests,
    'my_appointments':my_appointments,'free_slots':free_slots}


async def call(name: str,args: dict[str,Any],ctx: RequestContext) -> dict[str,Any]:
    """The one way agents call a tool. The transport is a setting; the tool, policy and audit are the same."""
    if name not in TOOLS:raise KeyError(f'Unknown tool {name}')
    token=TOOL_CTX.set(ctx)
    try:
        with span('tool_call','tool',tool=name,transport=settings.tool_transport,request_id=str(ctx.request_id),agent=ctx.agent):
            if settings.tool_transport=='mcp':
                from app.mcp_server import call_in_process
                return await call_in_process(name,args)
            return await TOOLS[name](**args)
    finally:TOOL_CTX.reset(token)


async def read(name: str,args: dict[str,Any],ctx: RequestContext) -> ToolResult:
    """A customer tool's result as the reader returned it."""
    return ToolResult.model_validate(await call(name,args,ctx))
