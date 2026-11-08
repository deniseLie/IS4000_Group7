"""Local tool layer (F21): each tool is one audited service call with the caller's own RequestContext.

Every read that feeds a model or an agent's rules is a tool here and goes through call(), which checks the agent's
spec (require_tool, bounded, shaped) and sends the call through the Model Context Protocol: app/mcp_server.py serves
these functions in-process (one shared client per event loop), and over stdio to external MCP clients
(python -m scripts.mcp_server). Policy (require_permission), JWT + RLS and audit run inside the tool, never in the caller
or the model. Writes are not tools: actions, offers, approved RM writes, uploads and insight actions call their services.
The meeting agents' reads (MEETING_READS) are MCP tools too, sent by meeting_read with the RM's context and the agent's
MeetingAccess; their drafts and the meeting worker's lease-fenced calls stay direct (meeting_call).
"""
import asyncio
import inspect
from collections import Counter
from collections.abc import Awaitable,Callable,Iterator
from contextlib import contextmanager
from datetime import date,timedelta
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo
from pydantic import JsonValue
from app.core import data_api
from app.core.observability import span
from app.schemas.contracts import RequestContext,ToolResult,ToolView
from app.schemas.customer_data import CustomerEnvelope
from app.schemas.knowledge import KnowledgeQuery
from app.schemas.meeting_agents import MeetingAccess
from app.schemas.relationship import SlotsQuery
from app.services import customer_data,document_templates,external,kb,prechecks
from app.services.agents import fraud_records,registry
from app.services.authorization import PolicyDenied,require_permission,require_tool
from app.services.rm import records as rm_records

SGT=ZoneInfo('Asia/Singapore')

TOOL_CTX: ContextVar[RequestContext | None]=ContextVar('tool_ctx',default=None)
MEETING_ACCESS: ContextVar[MeetingAccess | None]=ContextVar('meeting_access',default=None)  # set per call for a meeting read


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
    """Search approved UBS Connect knowledge (public scope). Returns cited sections."""
    ctx=_ctx()
    with data_api.connect(ctx) as db:sections=await kb.search(KnowledgeQuery(query=query,scope='public',k=k),ctx,db)
    return {'items':[item.model_dump(mode='json') for item in sections]}


async def knowledge_article(article_id: str) -> dict[str,Any]:
    """One approved UBS Connect article by id (published, in-review sections in anchor order); empty when retired or unknown."""
    ctx=_ctx()
    with data_api.connect(ctx) as db:sections=await kb.get_article(article_id,ctx,db)
    return {'items':[item.model_dump(mode='json') for item in sections]}


def _out(result: ToolResult,key: str='items') -> dict[str,Any]:
    """A Customer Data read as a tool result: its records under `items` (a list) or `data` (one record), with the read's
    call_id and as_of, so a rule cites the same audited read the model would."""
    return {key:result.data,'call_id':str(result.call_id),'as_of':result.as_of.isoformat()}


async def request_categories() -> dict[str,Any]:
    """List the request categories, what each needs and which team decides."""
    return {'items':[item.model_dump(mode='json') for item in await customer_data.get_categories(_ctx())]}


async def my_tickets() -> dict[str,Any]:
    """The signed-in customer's own requests with plain-language status (no internal notes)."""
    return _out(await customer_data.get_tickets(_ctx()))


async def my_cards() -> dict[str,Any]:
    """The signed-in customer's own cards, masked to the last four digits."""
    return _out(await customer_data.get_credit_cards(_ctx()))


async def my_transactions(status: str | None=None,days: int | None=None) -> dict[str,Any]:
    """The signed-in customer's recent transactions, optionally only 'pending' or 'posted' ones, optionally the last `days` days (to today)."""
    ctx=_ctx()
    since=today(ctx)-timedelta(days=days) if days else None
    return _out(await customer_data.get_transactions(ctx,since,today(ctx) if days else None,{'status':status} if status else None))


async def my_profile() -> dict[str,Any]:
    """The signed-in customer's own profile."""
    return _out(await customer_data.get_profile(_ctx()),'data')


async def my_accounts() -> dict[str,Any]:
    """The signed-in customer's own bank accounts, numbers masked to the last four digits."""
    return _out(await customer_data.get_accounts(_ctx()))


async def my_loans() -> dict[str,Any]:
    """The signed-in customer's own loans and next payments."""
    return _out(await customer_data.get_loans(_ctx()))


async def my_portfolio() -> dict[str,Any]:
    """The signed-in customer's own investment portfolio (0116), one row per holding; her time deposit is a cash holding."""
    result=await customer_data.get_portfolio(_ctx())
    p=result.data
    items=[{**h,'mandate':p['mandate'],'as_of':p['as_of'],'portfolio_total':p['total_value']} for h in p['holdings']] if p else []
    return {'items':items,'call_id':str(result.call_id),'as_of':result.as_of.isoformat()}


async def travel_flags() -> dict[str,Any]:
    """Charges on one card in another country less than 4 hours after an earlier charge (a rule, not a fraud decision)."""
    flags=fraud_records.travel_flags(await fraud_records.load(_ctx()))
    return {'items':[{'card_last4':f.charge.card_last4,'gap_minutes':int(f.gap.total_seconds()//60),
        'earlier':f.anchor.model_dump(mode='json'),'later':f.charge.model_dump(mode='json')} for f in flags]}


async def my_rm() -> dict[str,Any]:
    """The signed-in customer's current and previous relationship manager."""
    return _out(await customer_data.get_rm_assignment(_ctx()),'data')


async def my_appointments() -> dict[str,Any]:
    """The signed-in customer's held, confirmed and past appointments with her relationship manager."""
    return _out(await customer_data.get_appointments(_ctx()))


async def my_consents() -> dict[str,Any]:
    """The signed-in customer's consent requests and grants (purpose, recipient, status, expiry)."""
    return _out(await customer_data.get_consent_records(_ctx()),'data')


async def my_requests() -> dict[str,JsonValue]:
    """The signed-in customer's own requests to her relationship manager (who, what, urgency, deadline, status)."""
    return (await customer_data.get_request_records(_ctx())).model_dump(mode='json')


async def my_introduction() -> dict[str,JsonValue]:
    """Her current and previous relationship manager with the approved wording for a change of RM (never the reason code)."""
    return (await customer_data.get_introduction_record(_ctx())).model_dump(mode='json')


async def my_consent_sharing() -> dict[str,JsonValue]:
    """What the signed-in customer shares with her relationship manager, per category (consent v2), with her open accounts,
    her RM and next meeting."""
    return (await customer_data.get_consent_sharing_record(_ctx())).model_dump(mode='json')


async def my_meeting_requests() -> dict[str,JsonValue]:
    """The signed-in customer's own meeting requests to her relationship manager, newest first (the purpose is her own words)."""
    return (await customer_data.get_meeting_request_records(_ctx())).model_dump(mode='json')


async def my_talking_points() -> dict[str,JsonValue]:
    """Her client chat insights as list values (never her words), with removed points, mutes and pause (Data sharing)."""
    return (await customer_data.get_talking_points_record(_ctx())).model_dump(mode='json')


async def my_topic_status() -> dict[str,JsonValue]:
    """Her chat-insights status: whether chat topics are shared, her RM, mutes, recent prompts and meeting topics."""
    return (await customer_data.get_topic_status_record(_ctx())).model_dump(mode='json')


async def my_topic_state(turn_id: UUID) -> dict[str,JsonValue]:
    """Her chat-insights status with her current points and the topics active in this chat's last turns before `turn_id`."""
    return (await customer_data.get_topic_state_record(turn_id,_ctx())).model_dump(mode='json')


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
    """Client chat insights of one book client (list values and dates only). Inferred topics only under the client's chat_topics
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
    'my_requests':my_requests,'my_introduction':my_introduction,'my_consent_sharing':my_consent_sharing,'my_meeting_requests':my_meeting_requests,
    'my_talking_points':my_talking_points,'my_topic_status':my_topic_status,'my_topic_state':my_topic_state,
    **external.TOOLS,  # public data, codes only; an agent may call those named in its spec's `external`
    'rm_book':rm_book,'rm_match_clients':rm_match_clients,'rm_client_profile':rm_client_profile,'rm_meeting_notes':rm_meeting_notes,
    'rm_shared_summary':rm_shared_summary,'rm_tickets':rm_tickets,'rm_ticket':rm_ticket,'rm_review_status':rm_review_status,
    'rm_appointments':rm_appointments,'rm_availability':rm_availability,'rm_followups':rm_followups,'rm_handover_sources':rm_handover_sources,
    'rm_cash_events':rm_cash_events,'rm_book_facts':rm_book_facts,'rm_transaction_insights':rm_transaction_insights,
    'rm_talking_points':rm_talking_points,'rm_meeting_requests':rm_meeting_requests}


# Meeting agents' reads (architecture §15): fixed connectors the code selects, never a model. Each is the assigned RM's
# own meeting record, read with her JWT after the meeting policy (registry.MEETING_TOOLS, require_permission); SQL checks
# the live assignment and consent again. Served in-process only: not in TOOLS (so never through call() or the stdio server).
async def _meeting_rpc(name: str,p_meeting_id: UUID,args: dict[str,Any]) -> dict[str,JsonValue]:
    access,ctx=MEETING_ACCESS.get(),_ctx()
    if access is None:raise ToolContextMissing('Meeting tools run only with a meeting agent access set by the backend')
    require_permission(ctx.principal,name,{'meeting_id':str(p_meeting_id)},access)
    from app.services.meetings.database import ApplicationDatabase
    return {'data':await asyncio.to_thread(ApplicationDatabase(ctx).read,name,{'p_meeting_id':p_meeting_id,**args})}


async def meeting_preparation_inputs(p_meeting_id: UUID) -> dict[str,JsonValue]:
    """Brief Writer: the assigned RM's currently permitted preparation records for one meeting, as cited references."""
    return await _meeting_rpc('meeting_preparation_inputs',p_meeting_id,{})


async def meeting_pack_inputs(p_meeting_id: UUID,p_amounts: list[dict[str,JsonValue]]) -> dict[str,JsonValue]:
    """Proposal Drafter: one meeting's agreed agenda, approved catalogue and the RM's stated amounts (labels redacted)."""
    from psycopg.types.json import Jsonb
    return await _meeting_rpc('meeting_pack_inputs',p_meeting_id,{'p_amounts':Jsonb(p_amounts)})


async def meeting_notes_context(p_meeting_id: UUID) -> dict[str,JsonValue]:
    """Minutes Writer: one meeting's reviewed RM notes, with their source hash, revision and consent epoch."""
    return await _meeting_rpc('meeting_notes_context',p_meeting_id,{})


MEETING_READS: dict[str,Callable[...,Awaitable[dict[str,Any]]]]={'meeting_preparation_inputs':meeting_preparation_inputs,
    'meeting_pack_inputs':meeting_pack_inputs,'meeting_notes_context':meeting_notes_context}
SERVED=MappingProxyType({**TOOLS,**MEETING_READS})  # the MCP server's tool list and schemas; a call runs TOOLS[name] or MEETING_READS[name] (tests may swap one)


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
    """Gate a code-selected meeting connector that stays off MCP (an agent's draft write, or the worker's lease-fenced
    call on its private login) with the shared policy and metadata-only tool tracing. Reads go through meeting_read."""
    tool = name if name in registry.MEETING_TOOLS else 'unregistered_tool'
    with span('meeting.tool_call','tool',tool=tool,agent=ctx.agent,side='meeting',transport='direct') as facts:
        try:
            require_permission(ctx.principal,name,{'meeting_id':str(meeting)} if meeting is not None else None,ctx)
        except PolicyDenied as error:
            facts.update(allowed=False,reason=str(error).rsplit(': ',1)[-1])
            raise
        facts['allowed']=True
        yield facts


async def meeting_read(name: str,args: dict[str,JsonValue],ctx: RequestContext,access: MeetingAccess) -> JsonValue:
    """A meeting agent's read as an MCP tools/call (app/mcp_server.py): the RM's RequestContext and the agent's access go
    in the call's one-time `_meta` key, never in the arguments; the tool runs the meeting policy, then the SQL reader with
    her JWT. Same span and refusal facts as meeting_call, with transport=mcp."""
    if name not in MEETING_READS:raise KeyError(f'Unknown meeting read {name}')
    from app.mcp_server import call_in_process
    from app.services.meetings.database import MeetingStoreError
    with span('meeting.tool_call','tool',tool=name,agent=access.agent,side='meeting',transport='mcp') as facts:
        facts['allowed']=True
        try:result=await call_in_process(name,args,ctx,access)
        except PolicyDenied as error:
            facts.update(allowed=False,reason=str(error).rsplit(': ',1)[-1]);raise
        except MeetingStoreError as error:
            if error.code=='42501':facts.update(allowed=False,reason='meeting_scope_denied')
            raise
    return result['data']


async def call(name: str,args: dict[str,Any],ctx: RequestContext) -> dict[str,Any]:
    """The one way agents and rules call a tool: as an MCP tools/call (app/mcp_server.py) with the caller's context.
    A v5 agent (registry.spec_for) may call only its spec's tools, within its limits, and receives only its field view."""
    if name not in TOOLS:raise KeyError(f'Unknown tool {name}')
    with span('tool_call','tool',tool=name,agent=ctx.agent,transport='mcp') as facts:
        try:
            require_tool(name,ctx)
            spec=registry.spec_for(ctx.agent)
            view=(spec.tools.get(name) or ToolView()) if spec is not None else None  # an external tool returns public data whole
            if view is not None:args=bounded(name,args,view,ctx)
        except PolicyDenied as error:
            facts.update(allowed=False,reason='limit' if isinstance(error,ToolLimitExceeded) else str(error).rsplit(': ',1)[-1][:40]);raise
        facts['allowed']=True
        from app.mcp_server import call_in_process
        try:result=await call_in_process(name,args,ctx)
        except rm_records.RmDenied:
            facts.update(allowed=False,reason='not_in_book');raise
        except PolicyDenied as error:
            facts.update(allowed=False,reason=str(error).rsplit(': ',1)[-1][:40]);raise
        facts.update(result_facts(name,args,result))
    return shaped(result,view) if view is not None else result


def envelope(result: dict[str,Any],ctx: RequestContext) -> dict[str,Any]:
    """A record tool's result as the snapshot a rule validates (customer, call_id, as_of, data). The tool already checked
    the customer scope (customer_data._snapshot); a result without its own customer_id is the caller's."""
    return {'customer_id':result.get('customer_id',ctx.principal.customer_id),'call_id':result['call_id'],'as_of':result['as_of'],
        'data':result['items'] if 'items' in result else result['data']}


async def snapshot(name: str,args: dict[str,Any],ctx: RequestContext,model: Any) -> CustomerEnvelope:
    """One record tool through call(), validated as CustomerEnvelope[model] for the rule that answers from it."""
    return CustomerEnvelope[model].model_validate(envelope(await call(name,args,ctx),ctx))


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
