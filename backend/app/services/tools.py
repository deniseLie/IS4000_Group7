"""Local tool layer (F21): each tool is one audited service call with the caller's own RequestContext.

Agents reach these tools directly (TOOL_TRANSPORT=direct, the default) or through the Model Context Protocol
(TOOL_TRANSPORT=mcp): app/mcp_server.py exposes the same functions as an MCP server, used in-process by the
orchestrator and over stdio by external MCP clients (python -m scripts.mcp_server). Either way, policy
(require_permission), JWT + RLS and audit are applied inside the tool, never by the caller or the model.
"""
from collections.abc import Awaitable,Callable
from datetime import date
from contextvars import ContextVar
from typing import Any
from app.core import data_api
from app.core.config import settings
from app.core.observability import span
from app.schemas.contracts import RequestContext
from app.schemas.knowledge import KnowledgeQuery
from app.services import customer_data,kb
from app.services.rm import records as rm_records

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


# RM tools (F14/F25, Persona 2): the signed-in relationship manager's own book only. Each runs one audited SQL function
# that checks the RM claim, the staff login link and the client book; a client outside the book raises RmDenied.
async def rm_book(query: str | None=None) -> dict[str,Any]:
    """Clients in the signed-in relationship manager's current book, with open-ticket counts and next meeting."""
    return await rm_records.book(_ctx(),query)


async def rm_match_clients(text: str) -> dict[str,Any]:
    """Book clients named in a message; anyone else named is refused and audited (same reply whether or not they exist)."""
    return await rm_records.match_clients(_ctx(),text)


async def rm_client_profile(customer_id: int) -> dict[str,Any]:
    """One book client's 360 view: profile, products, open requests, appointments, preferences, notes, gaps."""
    return await rm_records.client_profile(_ctx(),customer_id)


async def rm_meeting_notes(customer_id: int) -> dict[str,Any]:
    """Meeting notes the RM may read for a book client (previous RMs' shared notes included; private notes withheld)."""
    return await rm_records.meeting_notes(_ctx(),customer_id)


async def rm_shared_summary(customer_id: int) -> dict[str,Any]:
    """The client's handover note, returned only under an active rm_brief consent to this RM (the check is audited)."""
    return await rm_records.shared_summary(_ctx(),customer_id)


async def rm_tickets() -> dict[str,Any]:
    """Open tickets in the RM's book: her own, and specialist-managed ones with client-safe status only."""
    return await rm_records.tickets(_ctx())


async def rm_ticket(reference: str) -> dict[str,Any]:
    """One ticket in the RM's book with what the client was told; restricted specialist detail is withheld."""
    return await rm_records.ticket(_ctx(),reference)


async def rm_review_status() -> dict[str,Any]:
    """Open compliance reviews across the RM's book (client-safe fields only)."""
    return await rm_records.review_status(_ctx())


async def rm_appointments(start: str | None=None,end: str | None=None) -> dict[str,Any]:
    """The RM's confirmed and held appointments with book clients (default: the next 14 days)."""
    return await rm_records.appointments(_ctx(),date.fromisoformat(start) if start else None,date.fromisoformat(end) if end else None)


async def rm_availability() -> dict[str,Any]:
    """The RM's next 14 days: weekly hours, blocked time, bookings and free times clients can book."""
    return await rm_records.availability(_ctx())


async def rm_followups() -> dict[str,Any]:
    """Follow-up candidates across the book (tickets, deadlines, maturities, tasks, meetings, commitments) with sources."""
    return await rm_records.followups(_ctx())


async def rm_handover_sources(customer_id: int) -> dict[str,Any]:
    """Sources for an inherited client's handover brief (only with a recorded reassignment; audited with the sources used)."""
    return await rm_records.handover_sources(_ctx(),customer_id)


TOOLS: dict[str,Callable[...,Awaitable[dict[str,Any]]]]={'knowledge_search':knowledge_search,'request_categories':request_categories,
    'my_tickets':my_tickets,'my_cards':my_cards,'my_transactions':my_transactions,
    'rm_book':rm_book,'rm_match_clients':rm_match_clients,'rm_client_profile':rm_client_profile,'rm_meeting_notes':rm_meeting_notes,
    'rm_shared_summary':rm_shared_summary,'rm_tickets':rm_tickets,'rm_ticket':rm_ticket,'rm_review_status':rm_review_status,
    'rm_appointments':rm_appointments,'rm_availability':rm_availability,'rm_followups':rm_followups,'rm_handover_sources':rm_handover_sources}


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
