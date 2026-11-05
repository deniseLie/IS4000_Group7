"""The RM assistant on the v5 graph (build plan phase 9; architecture §14.11): planner → agent (Send ×N) → gather →
[agent (wave 2) → gather →] compose, the same waves as the client graph, with the RM catalogue (registry.RM_SPECS).
api/rm.py runs it only for a verified `rm` login (rm_caller); a client never reaches it.

- planner: a fixed book check first (anyone outside the RM's book is refused and audited before any record is read or
  the model is called, R06); then a workspace button's intent, or one rm_planner/v5 call. Each intent belongs to one of
  the three agents (Client, Requests, Calendar; registry.INTENT_AGENT), so the model picks an agent only for an `open`
  question; orchestrator.linked drops a link the specs do not declare.
- agent: resolves the client, ticket or meeting against the book with the RM's own context, then runs the intent's
  handler (rm/handlers.py) with the agent's context, so the tool layer allows only that spec's tools. An `open` task runs
  the runner's bounded loop over the spec's loop_tools (handlers.h_open, build plan 3a). Exports are computed here by
  code from what the handler read (exports_for), never by the model.
- feeds (build plan 3b): `today` and `priorities` rank items other agents own (services/rm/priority.py), so code links
  them (FEEDS) to the Calendar's and Client's fixed reads: an independent task of that intent already in the plan, or a
  feed task added for it that only reads (no model, nothing shown). The first is depends_on, the others inputs_from.
- gather: orchestrator.gather unchanged (conditions, fan-out of at most 5 with "first 5 of N", fixed skip wording; the
  exports of every upstream a task names).
- compose: one task's reply is returned as it is (the workspace reply shape the UI already shows); several are merged
  in plan order: sections in order, every draft as a review card, sources and checks added up. Feeds are left out.

Tracing (build plan 3a): the planner, agent, loop_step, tool_call, gather and compose spans carry ids, codes and counts
only (side, source, tasks, refusal, path, stop reason, evidence ids, exports, cards); client text reaches LangSmith only
through the redacting client.

The assistant never writes: drafts, documentation and proposed actions are review cards the RM approves one by one
through the separate write routes, so the RM graph has no `act` node and saves no client offers.
"""
import json
import operator
from dataclasses import dataclass,field
from datetime import timedelta
from typing import Annotated,TypedDict
from langgraph.graph import END,START,StateGraph
from langgraph.runtime import Runtime
from app.core.observability import span
from app.schemas.chat import Message
from app.schemas.contracts import OPEN_INTENT,AgentOutput,Export,PlanTask,RequestContext
from app.schemas.events import AnswerSection
from app.schemas.rm import RmAssistantRequest
from app.services import llm_gateway,orchestrator,prompts,tools
from app.services.agents import registry
from app.services.privacy import redact
from app.services.rm import evidence as ev
from app.services.rm import handlers,priority
from app.services.rm.handlers import Say,capability_refusal,clarify,refusal,today

PLANNER_VERSION='v5'
MAX_TASKS=5
SEVERAL='Here are the parts of your request in order. Review each draft or proposal on its own: nothing goes to a client and nothing is saved or created until you approve it.'
FEEDS={'today':('schedule','meeting_requests','reviews'),'priorities':('meeting_requests',)}  # intent -> the intents it ranks


@dataclass
class RmRuntime:
    """Runtime identity, never graph state: the request, the RM's own context and what the planner found in her book."""
    req: RmAssistantRequest
    ctx: RequestContext
    say: Say
    book: list[dict]=field(default_factory=list)
    named: list[dict]=field(default_factory=list)  # clients named in the message who are in the book
    outside: bool=False  # someone named is not in the book (nothing about them is read)
    prompt_ids: list[str]=field(default_factory=list)


class RmTurn(TypedDict,total=False):
    request: RmAssistantRequest
    history: list[Message]  # the RM's redacted message, for orchestrator.agent_history; handlers read req.history
    tasks: list[PlanTask]
    lines: list[AnswerSection]  # fixed lines from the plan check and gather (unlinked, "first 5 of N")
    outputs: Annotated[list[AgentOutput],operator.add]
    next: list[orchestrator.AgentInput]
    reply: dict  # set by the planner for a refusal, else by compose


# ---- Planner ----

def plan_schema() -> dict:
    o=handlers._obj
    task=o({'intent':{'type':'string','enum':[*sorted(registry.INTENT_AGENT),OPEN_INTENT]},
        'agent':{'type':'string','enum':['',*sorted(n for n,s in registry.RM_SPECS.items() if s.loop_tools)]},  # for `open` only
        'client_name':handlers.S,'uses_active_client':{'type':'boolean'},
        'ticket_ref':handlers.S,'draft_scope':{'type':'string','enum':['client','ticket']},'appointment_hint':handlers.S,'is_new_notes':{'type':'boolean'},
        'depends_on':{'type':'integer','minimum':0,'maximum':MAX_TASKS},'condition':handlers.S,'for_each':handlers.S})
    return {'title':'rm_plan',**o({'tasks':{'type':'array','items':task},'clarification_question':handlers.S})}


async def model_plan(rt: RmRuntime,message: str) -> list[dict]:
    pv=prompts.load('rm_planner',PLANNER_VERSION)
    rt.prompt_ids.append(pv['id'])
    c=rt.req.context
    name=lambda cid:next((x['name'] for x in rt.book if x['customer_id']==cid),None)
    situation={'today':today().isoformat(),'active_client':name(c.client_id),'active_ticket':c.ticket_ref,
        'appointment_selected':bool(c.appointment_id),'draft_message_in_progress':bool(c.draft),'documentation_in_progress':bool(c.documentation),
        'clients_in_book':[x['name'] for x in rt.book]}
    system=pv['system']+'\nWorkspace context (untrusted): '+json.dumps(situation,ensure_ascii=False)
    history=handlers.history_for(rt.req,c.client_id,False)[-6:]
    result=await llm_gateway.call(pv,system,[*history,('user',message)],plan_schema())
    rows=result.data.get('tasks') if isinstance(result.data,dict) else None
    return [r for r in rows if isinstance(r,dict)][:MAX_TASKS] if isinstance(rows,list) else []


def tasks_from(rows: list[dict],message: str) -> list[PlanTask]:
    """Model rows → tasks: the agent comes from the intent (an `open` row names one of the three agents), depends_on is the
    1-based number of another row (0: none)."""
    rows=rows or [{'intent':'help'}]
    def text(row: dict,key: str) -> str:return value.strip()[:80] if isinstance(value:=row.get(key),str) else ''
    plan=[]
    for i,row in enumerate(rows):
        is_open=row.get('intent')==OPEN_INTENT and row.get('agent') in registry.RM_SPECS and registry.RM_SPECS[row['agent']].loop_tools
        intent=OPEN_INTENT if is_open else row.get('intent') if row.get('intent') in registry.INTENT_AGENT else 'help'
        n=row.get('depends_on')
        up=f't{n}' if isinstance(n,int) and not isinstance(n,bool) and 1<=n<=len(rows) and n!=i+1 else None
        entities={'client_name':text(row,'client_name'),'uses_active_client':bool(row.get('uses_active_client')),
            'ticket_ref':text(row,'ticket_ref'),'draft_scope':'ticket' if row.get('draft_scope')=='ticket' else 'client',
            'appointment_hint':text(row,'appointment_hint'),'is_new_notes':bool(row.get('is_new_notes'))}
        plan.append(PlanTask(id=f't{i+1}',agent=row['agent'] if is_open else registry.INTENT_AGENT[intent],intent=intent,question=message,entities=entities,
            **({'depends_on':up,'condition':text(row,'condition')[:40] or None,'for_each':text(row,'for_each')[:40] or None} if up else {})))
    # R23: a handover brief whose client note is a dependent step leaves the note to that step (shown once, under consent).
    noted={t.depends_on for t in plan if t.intent=='shared_note'}
    return [t.model_copy(update={'entities':{**t.entities,'shared':False}}) if t.intent=='handover_brief' and t.id in noted else t for t in plan]


def with_feeds(tasks: list[PlanTask]) -> list[PlanTask]:
    """Build plan 3b: link `today` and `priorities` to the intents whose records they rank (FEEDS): an independent task of
    that intent already in the plan, else a feed task (entities feed: its intent's fixed reads, no model, left out of the
    reply). The first upstream is depends_on, the rest inputs_from; a link the model gave such a task is replaced."""
    out=list(tasks)
    for i,task in enumerate(tasks):
        if task.intent not in FEEDS:continue
        ups=[]
        for intent in FEEDS[task.intent]:
            found=next((t for t in out if t.intent==intent and t.depends_on is None and t.id!=task.id),None)
            if found is None:
                found=PlanTask(id=f't{len(out)+1}',agent=registry.INTENT_AGENT[intent],intent=intent,question=task.question,entities={'feed':True})
                out.append(found)
            ups.append(found.id)
        out[i]=task.model_copy(update={'depends_on':ups[0],'inputs_from':ups[1:],'condition':None,'for_each':None})
    return out


async def planner(state: RmTurn,runtime: Runtime[RmRuntime]) -> dict:
    rt=runtime.context
    message,_=redact(rt.req.message)
    with span('planner','agent',side='rm',source='rule') as attrs:
        await rt.say('Checking your client book…')
        rt.book=(await tools.call('rm_book',{},rt.ctx))['data']
        turn=handlers.Turn(rt.req,rt.ctx,rt.say,book=rt.book)
        active=rt.req.context.client_id
        if active is not None and turn.name(active) is None:  # the selected client has left her book
            attrs.update(route='refused',refusal='left_book')
            return {'tasks':[],'lines':[],'reply':refusal(turn.context(None))}
        named=await tools.call('rm_match_clients',{'text':message},rt.ctx)
        rt.named,rt.outside=named['in_book'],bool(named['outside_book'])
        attrs.update(named_in_book=len(rt.named),named_outside=rt.outside)  # rm_match_clients: outside_book is a flag (0089)
        if named['outside_book'] and not named['in_book']:
            attrs.update(route='refused',refusal='outside_book')
            return {'tasks':[],'lines':[],'reply':refusal(turn.context(active))}
        if (restricted:=capability_refusal(message,turn.context(active))) is not None:  # specialist notes, ticket status, card actions
            attrs.update(route='refused',refusal='capability')
            return {'tasks':[],'lines':[],'reply':restricted}
        if rt.req.intent:
            attrs.update(route='button',source='button')
            tasks=[PlanTask(id='t1',agent=registry.INTENT_AGENT[rt.req.intent],intent=rt.req.intent,question=message,
                entities={'client_name':'','uses_active_client':True,'ticket_ref':'','appointment_hint':message,
                    'draft_scope':'ticket' if rt.req.context.ticket_ref else 'client',
                    'is_new_notes':rt.req.intent=='document_interaction' and rt.req.context.documentation is None,'button':True})]
        else:
            attrs.update(route='model',source='model')
            tasks=tasks_from(await model_plan(rt,message),message)
        tasks,lines=orchestrator.linked(with_feeds(tasks),rt.req,attrs)
        if sum(not t.entities.get('feed') for t in tasks)>1:
            tasks=[t if t.entities.get('feed') else t.model_copy(update={'entities':{**t.entities,'several':True}}) for t in tasks]
        attrs['tasks'],attrs['dependent'],attrs['feeds']=len(tasks),sum(1 for t in tasks if t.depends_on),sum(1 for t in tasks if t.entities.get('feed'))
        attrs['plan']=orchestrator.plan_facts(tasks)
    return {'tasks':tasks,'lines':lines}


# ---- Agents ----

def exports_for(intent: str,turn: handlers.Turn,client_id: int|None=None,feed: bool=False) -> dict[str,Export]:
    """Small values computed by code from what the handler read (§14.6 rule 4), never by the model. Only the agent's
    own declared exports leave it (agent() filters by spec.exports); an `open` task exports from what its loop read.
    Never transaction, talking-point or note content: references, dates, counts and codes only. A feed's window is the
    fixed one (the task it feeds ranks its own), never a period named in her message."""
    reads,found=turn.reads,{}
    if intent in ('reviews',OPEN_INTENT) and (rows:=reads.get('rm_review_status')) is not None:
        period=None if feed else ev.asked_period(turn.message,today())
        due=priority.reviews_due(rows['data'],period[1] if period else today()+timedelta(days=30))  # an overdue review is still due
        for name in ('clients_due','reviews_due'):found[name]=Export(value=due,source_ids=[f"review-{r['customer_id']}" for r in due] or ['reviews-none'])
    if intent in ('meeting_brief','meeting_history',OPEN_INTENT) and (notes:=reads.get('rm_meeting_notes')) is not None and notes['data']['notes']:
        found['latest_note']=Export(value=str(notes['data']['notes'][0]['note_id']),source_ids=[ev.note_ids(notes['data']['notes'])[0]])
    if intent=='handover_brief' and (sources:=reads.get('rm_handover_sources')) is not None and sources['data'].get('eligible'):
        found['consent_active']=Export(value=bool((sources['data'].get('shared') or {}).get('consent_active')),source_ids=['consent-status'])
    if client_id and (profile:=reads.get('rm_client_profile')) is not None:
        found['client_ref']=Export(value={'customer_id':client_id,'name':turn.name(client_id)},source_ids=['profile'])
        tickets=profile['data'].get('tickets') or []
        confirmed=[a for a in profile['data'].get('appointments') or [] if a.get('status')=='confirmed']
        found['needs_meeting']=Export(value=bool(tickets) and not confirmed,source_ids=['profile'])  # open requests and no meeting booked
        found['topics']=Export(value=sorted({str(t['category']) for t in tickets if t.get('category')}),source_ids=[f"ticket-{t['reference']}" for t in tickets] or ['profile'])
    if (rows:=reads.get('rm_appointments')) is not None and intent in ('schedule',OPEN_INTENT):
        found['meetings_next_7d']=Export(value=priority.meetings_next_7d(rows['data'],today()),source_ids=['appointments'])
    if (rows:=reads.get('rm_meeting_requests')) is not None and intent in ('meeting_requests',OPEN_INTENT):  # 3b: today, priorities
        mine=[r for r in rows.get('data') or [] if client_id is None or r.get('customer_id')==client_id]
        found['requests_to_answer']=Export(value=priority.requests_to_answer(mine),source_ids=['meeting-requests'])
    return found


async def feed_reads(turn: handlers.Turn,intent: str,spec) -> dict:
    """A feed task (FEEDS): its intent's fixed reads, for the exports only. No model call and nothing shown."""
    for read in spec.intents[intent]:await turn.read(read.tool,dict(read.fixed))
    return handlers.no_change(intent,'',turn.context(None))


def cards_of(reply: dict) -> dict[str,int]:
    """The review cards a reply proposes, by type (each is approved separately through the /rm write routes)."""
    found={'draft':int(bool(reply.get('draft')))+len(reply.get('drafts') or []),'documentation':int(bool(reply.get('documentation'))),
        'decision':int(bool(reply.get('decision'))),'availability':int(bool(reply.get('availability'))),'consent_ask':len(reply.get('consent_asks') or [])}
    if (doc:=reply.get('documentation')) and isinstance(doc.get('draft'),dict):found['actions']=len(doc['draft'].get('actions') or [])
    return {k:v for k,v in found.items() if v}


def status_of(reply: dict) -> str:
    return {'ok':'ok','clarify':'needs_clarification','denied':'error'}.get(reply.get('status'),'needs_human')


async def agent(item: orchestrator.AgentInput,runtime: Runtime[RmRuntime]) -> dict:
    rt,task=runtime.context,item['task']
    spec=registry.SPECS[task.agent]
    e,inputs=task.entities,item.get('inputs') or {}
    turn=handlers.Turn(rt.req,rt.ctx.model_copy(update={'agent':spec.name}),rt.say,base=rt.ctx,book=rt.book)
    turn.inputs,feed=inputs,bool(e.get('feed'))
    plan={'intent':task.intent,'client_name':e.get('client_name') or '','uses_active_client':bool(e.get('uses_active_client')),
        'ticket_ref':e.get('ticket_ref') or '','draft_scope':e.get('draft_scope') or 'client','appointment_hint':e.get('appointment_hint') or '',
        'is_new_notes':bool(e.get('is_new_notes')),'shared':e.get('shared',True),'fan_out':isinstance(e.get('item'),dict)}
    with span('agent','agent',agent=spec.name,intent=task.intent,wave=2 if task.depends_on else 1,side='rm',
            path='loop' if task.intent==OPEN_INTENT else 'reads',**({'feed':True} if feed else {})) as facts:  # 'rule' when the book check or resolution answers first
        if feed:client_id,stop=None,None  # FEEDS: book-wide reads for the task that ranks them
        elif plan['fan_out']:  # one client from the upstream's list; checked against the book again
            client_id=e['item'].get('customer_id')
            stop=None if any(c['customer_id']==client_id for c in rt.book) else refusal(turn.context(None))
        else:
            # The names in the message apply to a one-task message; with several tasks each task's own name wins, and a task
            # without one takes the message's client only when exactly one client in her book is named.
            several=e.get('several') and (e.get('client_name') or len(rt.named)!=1)
            client_id,stop=await turn.resolve_client(plan,[] if rt.req.intent or several else rt.named)
            if rt.req.intent and rt.req.context.client_id and not client_id:client_id=rt.req.context.client_id
        if 'latest_note' in inputs and client_id:  # R22: follow-ups from the note the brief read
            turn.req=rt.req.model_copy(update={'context':rt.req.context.model_copy(update={'client_id':client_id,'note_id':inputs['latest_note'].value})})
        if stop:reply=stop
        elif feed:reply=await feed_reads(turn,task.intent,spec)
        elif task.intent==OPEN_INTENT:reply=await handlers.h_open(turn,plan,client_id,spec,inputs,facts)
        else:reply=await handlers.HANDLERS[task.intent](turn,plan,client_id)
        facts.update(turn.facts)  # codes and counts a handler adds (the priority score's factors)
        status=status_of(reply)
        exports={k:v for k,v in exports_for(task.intent,turn,client_id,feed).items() if k in spec.exports} if status=='ok' else {}
        facts.update(status=status,reads=sorted(turn.reads),evidence=[s.get('id') for s in reply.get('sources') or []][:40],export_names=sorted(exports),
            cards=cards_of(reply),withheld=(reply.get('validation') or {}).get('withheld',0),origin=reply.get('origin'))
        if stop:facts['path']='rule'
    rt.prompt_ids+=turn.prompt_ids
    reply=json.loads(json.dumps(reply,ensure_ascii=False,default=str))
    if plan['fan_out']:reply['client']=e['item'].get('name')
    return {'outputs':[AgentOutput(task_id=task.id,agent=task.agent,status=status,origin='ai' if reply.get('origin')=='ai' else 'safety',
        exports=exports,reply=reply,prompt_id=turn.prompt_ids[-1] if turn.prompt_ids else None)]}


# ---- Compose ----

def gap(heading: str,text: str) -> dict:
    return {'heading':heading,'items':[{'text':text,'kind':'gap','owner':'none','source_ids':[]}]}


def merged(rows: list[tuple[PlanTask,AgentOutput]],lines: list[AnswerSection]) -> dict:
    replies=[(t,o.reply) for t,o in rows if o.reply is not None]
    if len(rows)==1 and replies and not lines:return replies[0][1]
    sections,drafts,sources,reasons=[],[],{},[]
    checked=withheld=0
    for item,output in rows:
        reply=output.reply
        if reply is None:  # a skipped dependant: the fixed line from gather
            sections.append(gap('Not done',output.result.text if output.result else orchestrator.UPSTREAM_UNKNOWN));continue
        who=reply.get('client') or (reply.get('context') or {}).get('client_name')
        prefix=f'{who}: ' if who and item.entities.get('item') else ''
        if reply.get('status')=='clarify':sections.append(gap(prefix+'Needs your answer',reply['summary']))
        elif reply.get('sections'):sections+=[{**s,'heading':prefix+s['heading']} for s in reply['sections']]
        elif reply.get('summary'):sections.append(gap(prefix+'Summary',reply['summary']))
        if reply.get('draft'):drafts.append(reply['draft'])
        for s in reply.get('sources') or []:sources.setdefault(s.get('id'),s)
        v=reply.get('validation') or {}
        checked,withheld=checked+v.get('checked',0),withheld+v.get('withheld',0)
        reasons+=v.get('reasons',[])
    sections+=[gap('Not done',line.answer) for line in lines]
    first=replies[0][1] if replies else {}
    context={k:v for k,v in (first.get('context') or {}).items() if k!='draft'} or {'client_id':None,'client_name':None,'ticket_ref':None,'appointment_id':None,'note_id':None}
    return {'status':'ok' if any(r.get('status')=='ok' for _,r in replies) else 'unverified','intent':first.get('intent','help'),'summary':SEVERAL,
        'sections':sections,'clarification':None,'context':context,'sources':list(sources.values()),
        'validation':{'checked':checked,'withheld':withheld,'reasons':list(dict.fromkeys(reasons))[:10]},
        'origin':'ai' if any(r.get('origin')=='ai' for _,r in replies) else 'records','draft':drafts[0] if len(drafts)==1 else None,
        'drafts':drafts if len(drafts)>1 else [],'documentation':next((r['documentation'] for _,r in replies if r.get('documentation')),None),
        **{card:found for card in ('decision','availability') if (found:=next((r[card] for _,r in replies if r.get(card)),None))},
        **({'consent_asks':asks} if (asks:=list({(a['customer_id'],a['category']):a for _,r in replies for a in r.get('consent_asks') or []}.values())) else {})}


def compose(state: RmTurn) -> dict:
    if state.get('reply') is not None:return {}
    every,lines=orchestrator.ordered(state),state.get('lines') or []
    rows=[(t,o) for t,o in every if not t.entities.get('feed')]  # feeds only passed their exports on
    with span('compose','decision',side='rm') as facts:
        reply=merged(rows,lines)
        facts.update(sections=[{'task':t.id,'agent':t.agent,'intent':t.intent,'status':o.status} for t,o in rows],fixed_lines=len(lines),
            cards=cards_of(reply),merged=len(rows)>1 or bool(lines),status=reply.get('status'),feeds=len(every)-len(rows))
    return {'reply':reply}


def build_graph():
    graph=StateGraph(RmTurn,context_schema=RmRuntime)
    graph.add_node('planner',planner)
    graph.add_node('agent',agent)
    graph.add_node('gather',orchestrator.gather)
    graph.add_node('compose',compose)
    graph.add_edge(START,'planner')
    graph.add_conditional_edges('planner',orchestrator.after_planner,['agent','compose'])
    graph.add_edge('agent','gather')
    graph.add_conditional_edges('gather',orchestrator.after_gather,['agent','compose'])
    graph.add_edge('compose',END)
    return graph.compile()


GRAPH=build_graph()


async def respond(req: RmAssistantRequest,ctx: RequestContext,say: Say) -> dict:
    """The /rm/assistant reply: the composed workspace reply plus the prompt versions and the privacy notices."""
    message,redacted=redact(req.message)
    rt=RmRuntime(req=req,ctx=ctx.model_copy(update={'agent':None}),say=say)
    with span('route','agent',route='llm',pipeline='v5-rm'):
        state=await GRAPH.ainvoke({'request':req,'history':[Message(role='user',content=message[:4000])],'outputs':[]},context=rt)
    reply=state['reply']
    reply['prompt_version']='/'.join(dict.fromkeys(rt.prompt_ids)) or None
    if redacted:reply['notice']='Private details (card numbers, emails, ID numbers or codes) were removed from your message before it was used.'
    if rt.outside:reply['notice']=((reply.get('notice') or '')+' Someone you named is not in your book, so nothing about them was read.').strip()
    return reply
