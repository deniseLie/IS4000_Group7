"""Local tool layer (F21): each tool is one audited service call with the caller's own RequestContext.

Agents reach these tools directly (TOOL_TRANSPORT=direct, the default) or through the Model Context Protocol
(TOOL_TRANSPORT=mcp): app/mcp_server.py exposes the same functions as an MCP server, used in-process by the
orchestrator and over stdio by external MCP clients (python -m scripts.mcp_server). Either way, policy
(require_permission), JWT + RLS and audit are applied inside the tool, never by the caller or the model.
"""
import inspect
from collections import Counter
from collections.abc import Awaitable,Callable,Iterator
from contextlib import contextmanager
from datetime import date,timedelta
from contextvars import ContextVar
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo
from pydantic import JsonValue
from app.core import data_api
from app.core.config import settings
from app.core.observability import span
from app.schemas.contracts import RequestContext,ToolView
from app.schemas.knowledge import KnowledgeQuery
from app.schemas.meeting_agents import MeetingAccess
from app.schemas.relationship import SlotsQuery
from app.services import customer_data,document_templates,external,kb,prechecks
from app.services.agents import fraud_records,registry
from app.services.authorization import PolicyDenied,require_permission,require_tool
from app.services.rm import records as rm_records

SGT=ZoneInfo('Asia/Singapore')

TOOL_CTX: ContextVar[RequestContext | None]=ContextVar('tool_ctx',default=None)


class ToolContextMissing(PermissionError):
    """A tool ran without a backend-assigned request context."""


def _ctx() -> RequestContext:
    ctx=TOOL_CTX.get()
    if ctx is None:raise ToolContextMissing('Tools run only inside a request context set by the backend')
    return ctx


def today(ctx: RequestContext) -> date:
    return ctx.received_at.astimezone(SGT).date()


class ToolLimitExceeded(PolicyDenied):
    """A v5 agent asked for more than its spec allows (date window or rows)."""


async def knowledge_search(query: str,k: int=4) -> dict[str,Any]:
    """Search approved DemoBank knowledge (public scope). Returns cited sections."""
    ctx=_ctx()
    with data_api.connect(ctx) as db:sections=await kb.search(KnowledgeQuery(query=query,scope='public',k=k),ctx,db)
    return {'items':[item.model_dump(mode='json') for item in sections]}


async def knowledge_article(article_id: str) -> dict[str,Any]:
    """One approved DemoBank article by id (published, in-review sections in anchor order); empty when retired or unknown."""
    ctx=_ctx()
    with data_api.connect(ctx) as db:sections=await kb.get_article(article_id,ctx,db)
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


async def my_transactions(status: str | None=None,days: int | None=None) -> dict[str,Any]:
    """The signed-in customer's recent transactions, optionally only 'pending' or 'posted' ones, optionally the last `days` days."""
    ctx=_ctx()
    since=today(ctx)-timedelta(days=days) if days else None
    result=await customer_data.get_transactions(ctx,since,None,{'status':status} if status else None)
    return {'items':result.data,'call_id':str(result.call_id)}


async def my_profile() -> dict[str,Any]:
    """The signed-in customer's own profile."""
    result=await customer_data.get_profile(_ctx())
    return {'data':result.data,'call_id':str(result.call_id)}


async def my_accounts() -> dict[str,Any]:
    """The signed-in customer's own bank accounts, numbers masked to the last four digits."""
    result=await customer_data.get_accounts(_ctx())
    return {'items':result.data,'call_id':str(result.call_id)}


async def my_loans() -> dict[str,Any]:
    """The signed-in customer's own loans and next payments."""
    result=await customer_data.get_loans(_ctx())
    return {'items':result.data,'call_id':str(result.call_id)}


async def my_portfolio() -> dict[str,Any]:
    """The signed-in customer's own investment portfolio (0116), one row per holding; her time deposit is a cash holding."""
    result=await customer_data.get_portfolio(_ctx())
    p=result.data
    items=[{**h,'mandate':p['mandate'],'as_of':p['as_of'],'portfolio_total':p['total_value']} for h in p['holdings']] if p else []
    return {'items':items,'call_id':str(result.call_id)}


async def travel_flags() -> dict[str,Any]:
    """Charges on one card in another country less than 4 hours after an earlier charge (a rule, not a fraud decision)."""
    flags=fraud_records.travel_flags(await fraud_records.load(_ctx()))
    return {'items':[{'card_last4':f.charge.card_last4,'gap_minutes':int(f.gap.total_seconds()//60),
        'earlier':f.anchor.model_dump(mode='json'),'later':f.charge.model_dump(mode='json')} for f in flags]}


async def my_rm() -> dict[str,Any]:
    """The signed-in customer's current and previous relationship manager."""
    result=await customer_data.get_rm_assignment(_ctx())
    return {'data':result.data,'call_id':str(result.call_id)}


async def my_appointments() -> dict[str,Any]:
    """The signed-in customer's held, confirmed and past appointments with her relationship manager."""
    result=await customer_data.get_appointments(_ctx())
    return {'items':result.data,'call_id':str(result.call_id)}


async def my_consents() -> dict[str,Any]:
    """The signed-in customer's consent requests and grants (purpose, recipient, status, expiry)."""
    result=await customer_data.get_consent_records(_ctx())
    return {'data':result.data,'call_id':str(result.call_id)}


async def document_templates_guide() -> dict[str,Any]:
    """What each accepted document type must show, with a made-up example (public configuration)."""
    return {'items':document_templates.describe()}


async def my_compliance_case() -> dict[str,JsonValue]:
    """The signed-in customer's recorded review, checklist and bank questions; never a bank decision."""
    return (await customer_data.get_compliance_case(_ctx())).model_dump(mode='json')


async def my_document_checks() -> dict[str,JsonValue]:
    """The signed-in customer's current uploads and preliminary checks, separate from bank acceptance."""
    return (await prechecks.workspace(_ctx())).model_dump(mode='json')


async def free_slots(from_date: date | None=None,to_date: date | None=None,limit: int=5) -> dict[str,JsonValue]:
    """Bounded free appointment suggestions for the signed-in customer's current relationship manager (default: next 14 days)."""
    start=from_date or today(_ctx())
    query=SlotsQuery(from_date=start,to_date=to_date or start+timedelta(days=13),limit=limit)
    return (await customer_data.get_available_slot_records(query,_ctx())).model_dump(mode='json')


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


async def rm_cash_events(customer_id: int) -> dict[str,Any]:
    """Large money movements (10,000 or more, last 180 days) for one book client, as recorded; recurring ones are marked."""
    return await rm_records.cash_events(_ctx(),customer_id)


async def rm_transaction_insights(customer_id: int,purpose: str,days: int=90) -> dict[str,Any]:
    """One book client's money in and out by month, card and overseas spending, large and recurring movements (30-180 days),
    only under the client's transaction_insights consent; otherwise shared is false and there is no data.
    purpose: meeting_prep (a meeting brief) or client_view, set by code from the intent, never by a model (audited)."""
    return await rm_records.transaction_insights(_ctx(),customer_id,purpose,days)


async def rm_talking_points(customer_id: int,purpose: str) -> dict[str,Any]:
    """Topics from one book client's chats (list values and dates only). Inferred topics only under the client's chat_topics
    consent; data is always a list, and without that consent it holds only topics she shared herself.
    purpose: meeting_prep (a meeting brief) or client_view, set by code from the intent, never by a model (audited)."""
    return await rm_records.talking_points(_ctx(),customer_id,purpose)


async def rm_meeting_requests(status: str='open') -> dict[str,Any]:
    """Meeting requests from clients in the RM's book: open (default), scheduled or closed, with each preferred time's status."""
    return await rm_records.meeting_requests(_ctx(),status)


async def rm_book_facts() -> dict[str,Any]:
    """One line of facts per client in the RM's book: last interaction, next meeting, open requests, maturities, tasks."""
    return await rm_records.book_facts(_ctx())


TOOLS: dict[str,Callable[...,Awaitable[dict[str,Any]]]]={'knowledge_search':knowledge_search,'knowledge_article':knowledge_article,
    'request_categories':request_categories,
    'my_tickets':my_tickets,'my_cards':my_cards,'my_transactions':my_transactions,
    'my_compliance_case':my_compliance_case,'my_document_checks':my_document_checks,
    'free_slots':free_slots,'my_profile':my_profile,'my_accounts':my_accounts,'my_loans':my_loans,'my_portfolio':my_portfolio,'travel_flags':travel_flags,
    'my_rm':my_rm,'my_appointments':my_appointments,'my_consents':my_consents,'document_templates':document_templates_guide,
    **external.TOOLS,  # public data, codes only; an agent may call those named in its spec's `external`
    'rm_book':rm_book,'rm_match_clients':rm_match_clients,'rm_client_profile':rm_client_profile,'rm_meeting_notes':rm_meeting_notes,
    'rm_shared_summary':rm_shared_summary,'rm_tickets':rm_tickets,'rm_ticket':rm_ticket,'rm_review_status':rm_review_status,
    'rm_appointments':rm_appointments,'rm_availability':rm_availability,'rm_followups':rm_followups,'rm_handover_sources':rm_handover_sources,
    'rm_cash_events':rm_cash_events,'rm_book_facts':rm_book_facts,'rm_transaction_insights':rm_transaction_insights,
    'rm_talking_points':rm_talking_points,'rm_meeting_requests':rm_meeting_requests}


def project(value: Any,fields: frozenset[str]) -> Any:
    """Keep only the listed dotted fields; lists are transparent, so `current.name` applies to every item."""
    if isinstance(value,list):return [project(item,fields) for item in value]
    if not isinstance(value,dict):return value
    kept={}
    for key,item in value.items():
        if key in fields:kept[key]=item
        elif sub:=frozenset(f[len(key)+1:] for f in fields if f.startswith(key+'.')):kept[key]=project(item,sub)
    return kept


# Date-window arguments of tools whose views carry max_days: a look-back length, or a forward (from, to) range.
LOOKBACK={'my_transactions':'days'}
FORWARD={'free_slots':('from_date','to_date')}


def bounded(name: str,args: dict[str,Any],view: ToolView,ctx: RequestContext) -> dict[str,Any]:
    """The arguments with the spec's limits applied: a missing window or row count gets the limit, a larger one is refused."""
    args=dict(args)
    if view.max_days and name in LOOKBACK:
        key=LOOKBACK[name]
        if args.get(key) is None:args[key]=view.max_days
        if not 0<int(args[key])<=view.max_days:raise ToolLimitExceeded(f'{name}: at most {view.max_days} days')
    if view.max_days and name in FORWARD:
        start,end=FORWARD[name];first=today(ctx)
        begin=date.fromisoformat(str(args[start])) if args.get(start) else first
        if args.get(end) is None:args[end]=(begin+timedelta(days=view.max_days-1)).isoformat()
        if begin<first or (date.fromisoformat(str(args[end]))-first).days>=view.max_days:
            raise ToolLimitExceeded(f'{name}: within the next {view.max_days} days')
    if view.max_rows and 'limit' in inspect.signature(TOOLS[name]).parameters:
        if int(args.get('limit') or 0)>view.max_rows:raise ToolLimitExceeded(f'{name}: at most {view.max_rows} rows')
    return args


def shaped(result: dict[str,Any],view: ToolView) -> dict[str,Any]:
    """What the agent receives: its field view of the records (`items` or `data`) and at most max_rows of them."""
    out={key:value for key,value in result.items() if key!='customer_id'}  # identifiers never reach the model
    for key in ('items','data'):
        if key not in out:continue
        records=out[key]
        if view.max_rows and isinstance(records,list):records=records[:view.max_rows]
        out[key]=project(records,view.fields) if view.fields is not None else records
    return out


@contextmanager
def meeting_call(name: str,meeting: UUID | None,ctx: MeetingAccess) -> Iterator[dict[str,str|bool]]:
    """Gate a code-selected meeting connector with the shared policy and metadata-only tool tracing."""
    tool = name if name in registry.MEETING_TOOLS else 'unregistered_tool'
    with span('meeting.tool_call','tool',tool=tool,agent=ctx.agent,side='meeting',transport='direct') as facts:
        try:
            require_permission(ctx.principal,name,{'meeting_id':str(meeting)} if meeting is not None else None,ctx)
        except PolicyDenied as error:
            facts.update(allowed=False,reason=str(error).rsplit(': ',1)[-1])
            raise
        facts['allowed']=True
        yield facts


async def call(name: str,args: dict[str,Any],ctx: RequestContext) -> dict[str,Any]:
    """The one way agents call a tool. The transport is a setting; the tool, policy and audit are the same.
    A v5 agent (registry.spec_for) may call only its spec's tools, within its limits, and receives only its field view."""
    if name not in TOOLS:raise KeyError(f'Unknown tool {name}')
    with span('tool_call','tool',tool=name,agent=ctx.agent,transport=settings.tool_transport) as facts:
        try:
            require_tool(name,ctx)
            spec=registry.spec_for(ctx.agent)
            view=(spec.tools.get(name) or ToolView()) if spec is not None else None  # an external tool returns public data whole
            if view is not None:args=bounded(name,args,view,ctx)
        except PolicyDenied as error:
            facts.update(allowed=False,reason='limit' if isinstance(error,ToolLimitExceeded) else str(error).rsplit(': ',1)[-1][:40]);raise
        facts['allowed']=True
        token=TOOL_CTX.set(ctx)
        try:
            if settings.tool_transport=='mcp':
                from app.mcp_server import call_in_process
                result=await call_in_process(name,args)
            else:result=await TOOLS[name](**args)
        except rm_records.RmDenied:
            facts.update(allowed=False,reason='not_in_book');raise
        except PolicyDenied as error:
            facts.update(allowed=False,reason=str(error).rsplit(': ',1)[-1][:40]);raise
        finally:TOOL_CTX.reset(token)
        facts.update(result_facts(name,args,result))
    return shaped(result,view) if view is not None else result


def result_facts(name: str,args: dict[str,Any],result: Any) -> dict[str,JsonValue]:
    """The tool_call span's outcome (ids, codes and counts only): rows returned and, for a consent-gated RM read, whether
    the client shared it, under which category, grant and purpose; talking points also by origin."""
    if not isinstance(result,dict):return {}
    rows=result.get('items',result.get('data'))
    out: dict[str,JsonValue]={'rows':len(rows) if isinstance(rows,list) else int(rows is not None)} if 'items' in result or 'data' in result else {}
    if 'shared' in result:
        out.update(shared=result['shared'],category=result.get('category'),grant_id=str(result['grant_id']) if result.get('grant_id') else None,
            purpose=args.get('purpose'))
    if name=='rm_talking_points' and isinstance(rows,list):
        out['origins']=dict(Counter(str(r.get('origin')) for r in rows if isinstance(r,dict)))
    return out
