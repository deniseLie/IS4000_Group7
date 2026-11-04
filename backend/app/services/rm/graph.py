"""The RM assistant on the v5 graph (build plan phase 9; architecture §14.11): planner → agent (Send ×N) → gather →
[agent (wave 2) → gather →] compose, the same waves as the client graph, with the RM catalogue (registry.RM_SPECS).
api/rm.py runs it only for a verified `rm` login (rm_caller); a client never reaches it.

- planner: a fixed book check first (anyone outside the RM's book is refused and audited before any record is read or
  the model is called, R06); then a workspace button's intent, or one rm_planner/v4 call. Each intent belongs to one
  agent (registry.INTENT_AGENT), so the model never picks an agent; orchestrator.linked drops a link the specs do not
  declare.
- agent: resolves the client, ticket or meeting against the book with the RM's own context, then runs the intent's
  handler (rm/handlers.py) with the agent's context, so the tool layer allows only that spec's tools. Exports are
  computed here by code from what the handler read: `clients_due` (reviews due in the asked period, overdue ones
  included), `latest_note` (the client's latest saved note) and `consent_active` (the handover read's consent check).
- gather: orchestrator.gather unchanged (conditions, fan-out of at most 5 with "first 5 of N", fixed skip wording).
- compose: one task's reply is returned as it is (the workspace reply shape the UI already shows); several are merged
  in plan order: sections in order, every draft as a review card, sources and checks added up.

The assistant never writes: drafts, documentation and proposed actions are review cards the RM approves one by one
through the separate write routes, so the RM graph has no `act` node and saves no client offers.
"""
import json
import operator
from dataclasses import dataclass,field
from datetime import date,timedelta
from typing import Annotated,TypedDict
from langgraph.graph import END,START,StateGraph
from langgraph.runtime import Runtime
from app.core.observability import span
from app.schemas.chat import Message
from app.schemas.contracts import AgentOutput,Export,PlanTask,RequestContext
from app.schemas.events import AnswerSection
from app.schemas.rm import RmAssistantRequest
from app.services import llm_gateway,orchestrator,prompts,tools
from app.services.agents import registry
from app.services.privacy import redact
from app.services.rm import evidence as ev
from app.services.rm import handlers
from app.services.rm.handlers import Say,capability_refusal,clarify,refusal,today

PLANNER_VERSION='v4'
MAX_TASKS=5
SEVERAL='Here are the parts of your request in order. Review each draft or proposal on its own: nothing goes to a client and nothing is saved or created until you approve it.'


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
    task=o({'intent':{'type':'string','enum':sorted(registry.INTENT_AGENT)},'client_name':handlers.S,'uses_active_client':{'type':'boolean'},
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
    """Model rows → tasks: the agent comes from the intent, depends_on is the 1-based number of another row (0: none)."""
    rows=rows or [{'intent':'help'}]
    def text(row: dict,key: str) -> str:return value.strip()[:80] if isinstance(value:=row.get(key),str) else ''
    plan=[]
    for i,row in enumerate(rows):
        intent=row.get('intent') if row.get('intent') in registry.INTENT_AGENT else 'help'
        n=row.get('depends_on')
        up=f't{n}' if isinstance(n,int) and not isinstance(n,bool) and 1<=n<=len(rows) and n!=i+1 else None
        entities={'client_name':text(row,'client_name'),'uses_active_client':bool(row.get('uses_active_client')),
            'ticket_ref':text(row,'ticket_ref'),'draft_scope':'ticket' if row.get('draft_scope')=='ticket' else 'client',
            'appointment_hint':text(row,'appointment_hint'),'is_new_notes':bool(row.get('is_new_notes'))}
        plan.append(PlanTask(id=f't{i+1}',agent=registry.INTENT_AGENT[intent],intent=intent,question=message,entities=entities,
            **({'depends_on':up,'condition':text(row,'condition')[:40] or None,'for_each':text(row,'for_each')[:40] or None} if up else {})))
    # R23: a handover brief whose client note is a dependent step leaves the note to that step (shown once, under consent).
    noted={t.depends_on for t in plan if t.intent=='shared_note'}
    return [t.model_copy(update={'entities':{**t.entities,'shared':False}}) if t.intent=='handover_brief' and t.id in noted else t for t in plan]


async def planner(state: RmTurn,runtime: Runtime[RmRuntime]) -> dict:
    rt=runtime.context
    message,_=redact(rt.req.message)
    with span('planner','agent',side='rm') as attrs:
        await rt.say('Checking your client book…')
        rt.book=(await tools.call('rm_book',{},rt.ctx))['data']
        turn=handlers.Turn(rt.req,rt.ctx,rt.say,book=rt.book)
        active=rt.req.context.client_id
        if active is not None and turn.name(active) is None:  # the selected client has left her book
            attrs['route']='refused'
            return {'tasks':[],'lines':[],'reply':refusal(turn.context(None))}
        named=await tools.call('rm_match_clients',{'text':message},rt.ctx)
        rt.named,rt.outside=named['in_book'],bool(named['outside_book'])
        if named['outside_book'] and not named['in_book']:
            attrs['route']='refused'
            return {'tasks':[],'lines':[],'reply':refusal(turn.context(active))}
        if (restricted:=capability_refusal(message,turn.context(active))) is not None:  # specialist notes, ticket status, card actions
            attrs['route']='refused'
            return {'tasks':[],'lines':[],'reply':restricted}
        if rt.req.intent:
            attrs['route']='button'
            tasks=[PlanTask(id='t1',agent=registry.INTENT_AGENT[rt.req.intent],intent=rt.req.intent,question=message,
                entities={'client_name':'','uses_active_client':True,'ticket_ref':'','appointment_hint':message,
                    'draft_scope':'ticket' if rt.req.context.ticket_ref else 'client',
                    'is_new_notes':rt.req.intent=='document_interaction' and rt.req.context.documentation is None,'button':True})]
        else:
            attrs['route']='model'
            tasks=tasks_from(await model_plan(rt,message),message)
        tasks,lines=orchestrator.linked(tasks,rt.req)
        if len(tasks)>1:tasks=[t.model_copy(update={'entities':{**t.entities,'several':True}}) for t in tasks]
        attrs['tasks'],attrs['dependent']=len(tasks),sum(1 for t in tasks if t.depends_on)
    return {'tasks':tasks,'lines':lines}


# ---- Agents ----

def exports_for(intent: str,turn: handlers.Turn) -> dict[str,Export]:
    """Small values computed by code from what the handler read (§14.6 rule 4), never by the model."""
    reads,found=turn.reads,{}
    if intent=='reviews' and (rows:=reads.get('rm_review_status')) is not None:
        period=ev.asked_period(turn.message,today())
        end=period[1] if period else today()+timedelta(days=30)
        due=[{'customer_id':r['customer_id'],'name':r['client'],'due_date':str(r['due_date'])} for r in rows['data']
            if r.get('due_date') and date.fromisoformat(str(r['due_date'])[:10])<=end]  # an overdue review is still due
        found['clients_due']=Export(value=due,source_ids=[f"review-{r['customer_id']}" for r in due] or ['reviews-none'])
    if intent in ('meeting_brief','meeting_history') and (notes:=reads.get('rm_meeting_notes')) is not None and notes['data']['notes']:
        found['latest_note']=Export(value=str(notes['data']['notes'][0]['note_id']),source_ids=[ev.note_ids(notes['data']['notes'])[0]])
    if intent=='handover_brief' and (sources:=reads.get('rm_handover_sources')) is not None and sources['data'].get('eligible'):
        found['consent_active']=Export(value=bool((sources['data'].get('shared') or {}).get('consent_active')),source_ids=['consent-status'])
    return found


def status_of(reply: dict) -> str:
    return {'ok':'ok','clarify':'needs_clarification','denied':'error'}.get(reply.get('status'),'needs_human')


async def agent(item: orchestrator.AgentInput,runtime: Runtime[RmRuntime]) -> dict:
    rt,task=runtime.context,item['task']
    spec=registry.SPECS[task.agent]
    e,inputs=task.entities,item.get('inputs') or {}
    turn=handlers.Turn(rt.req,rt.ctx.model_copy(update={'agent':spec.name}),rt.say,base=rt.ctx,book=rt.book)
    plan={'intent':task.intent,'client_name':e.get('client_name') or '','uses_active_client':bool(e.get('uses_active_client')),
        'ticket_ref':e.get('ticket_ref') or '','draft_scope':e.get('draft_scope') or 'client','appointment_hint':e.get('appointment_hint') or '',
        'is_new_notes':bool(e.get('is_new_notes')),'shared':e.get('shared',True),'fan_out':isinstance(e.get('item'),dict)}
    with span('agent','agent',agent=spec.name,intent=task.intent,wave=2 if task.depends_on else 1):
        if plan['fan_out']:  # one client from the upstream's list; checked against the book again
            client_id=e['item'].get('customer_id')
            stop=None if any(c['customer_id']==client_id for c in rt.book) else refusal(turn.context(None))
        else:
            # The names in the message apply only when the message is one task; otherwise each task has its own name.
            client_id,stop=await turn.resolve_client(plan,[] if rt.req.intent or e.get('several') else rt.named)
            if rt.req.intent and rt.req.context.client_id and not client_id:client_id=rt.req.context.client_id
        if 'latest_note' in inputs and client_id:  # R22: follow-ups from the note the brief read
            turn.req=rt.req.model_copy(update={'context':rt.req.context.model_copy(update={'client_id':client_id,'note_id':inputs['latest_note'].value})})
        reply=stop or await handlers.HANDLERS[task.intent](turn,plan,client_id)
    rt.prompt_ids+=turn.prompt_ids
    reply=json.loads(json.dumps(reply,ensure_ascii=False,default=str))
    if plan['fan_out']:reply['client']=e['item'].get('name')
    status=status_of(reply)
    exports={k:v for k,v in exports_for(task.intent,turn).items() if k in spec.exports} if status=='ok' else {}
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
        'drafts':drafts if len(drafts)>1 else [],'documentation':next((r['documentation'] for _,r in replies if r.get('documentation')),None)}


def compose(state: RmTurn) -> dict:
    if state.get('reply') is not None:return {}
    return {'reply':merged(orchestrator.ordered(state),state.get('lines') or [])}


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
