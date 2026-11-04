"""LangSmith target (docs/evaluation/README.md §4): plays one dataset example through the real app, in process.

Each example's turns go to POST /chat in a saved chat, exactly as the browser sends them, as a guest or as the persona
signed in with its own JWT (password login from the git-ignored persona fixture, as in tests/test_rls_live.py). Nothing
is faked: the real guardrail, v2 graph, agents, tools, model, database and RLS run. While a turn runs the target
records which graph nodes acted (a LangChain callback hook) and which agents, tools and model calls ran (the
observability span sink), and it reads the persona's tickets before and after so evaluators can see what opened.
Steps the chat cannot play (form, upload, a button other than a chat reply) are skipped and listed in the output; a chat
reply button is played only when the previous reply offers it, otherwise the conversation stops there (`not_offered`).
Graph cases may name a `fault` (architecture §14.12); `inject` makes that one piece fail for the whole case and the output
says whether the pipeline has the piece at all (v4 has no external tools, so `external_down` changes nothing there)."""
import json
import re
import secrets
from collections import Counter
from contextlib import ExitStack,contextmanager
from contextvars import ContextVar
from typing import Any
from unittest.mock import patch
from uuid import UUID,uuid4
import httpx
from pydantic import TypeAdapter
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.tracers.context import register_configure_hook
from app.core.observability import span_sink,evidence_sink
from app.schemas.agents import Evidence
from app.schemas.chat import ChatRequest
from app.schemas.tickets import CustomerTicket
from app.services.rm_handover import SEND_REPLY,PREVIEW_MARK,OFFERED
from app.services.guardrail import assess_request

PERSONAS={'michelle':'demo.michelle@example.com','sophie':'demo.sophie@example.com'}
CHAT_TAPS={SEND_REPLY,'yes'}  # buttons whose label is sent as the next chat message (a typed yes confirms an offer)
TAP_ALIASES={'Confirm':'yes'}  # the offer's Confirm button; a typed yes takes the same act path
NODES=('planner','agent','gather','act','compose')


def prompt_ids(spans) -> list[str]:
    """family/version+hash of every model call the case made (the admin dashboard's accuracy per prompt version)."""
    return sorted({f"{a['agent']}/{a['prompt_version']}" for n,a in spans if n=='llm' and a.get('agent') and a.get('prompt_version')})


class NodeLog(AsyncCallbackHandler):
    """A graph node acted when its update changes the state (a new or different non-empty value)."""
    def __init__(self) -> None:
        self.inputs: dict[UUID,tuple[str,Any]]={}
        self.ran: set[str]=set()
        self.acted: set[str]=set()

    async def on_chain_start(self,serialized,inputs,*,run_id,metadata=None,**kw) -> None:
        name=kw.get('name')
        if metadata and metadata.get('langgraph_node')==name and name in NODES:
            self.inputs[run_id]=(name,inputs);self.ran.add(name)

    async def on_chain_end(self,outputs,*,run_id,**kw) -> None:
        if (node:=self.inputs.pop(run_id,None)) is None:return
        name,state=node
        if isinstance(outputs,dict) and any(v and v!=(state.get(k) if isinstance(state,dict) else None) for k,v in outputs.items()):self.acted.add(name)

    async def on_chain_error(self,error,*,run_id,**kw) -> None:
        self.inputs.pop(run_id,None)


node_log: ContextVar[NodeLog | None]=ContextVar('eval_node_log',default=None)
register_configure_hook(node_log,True)  # every LangChain/LangGraph run in this context reports to the active NodeLog


async def _withheld(*args,**kw):
    from app.services.llm_gateway import LLMError
    raise LLMError('injected fault: upstream agent unavailable')


async def _receipt_failed(*args,**kw):
    raise RuntimeError('injected fault: offer executor failed')


async def _external_down(tool,*args,**kw):
    from app.services.external import ExternalError
    raise ExternalError(tool,'unavailable')


from app.services.rm import records as _rm_records
_RM_RPC=_rm_records.rpc


async def _consent_revoked(name,params,ctx,tool):
    """R23a: the client's rm_brief grant reads as revoked; every other RM read is the real one."""
    result=await _RM_RPC(name,params,ctx,tool)
    revoked={'consent_active':False,'shared':[],'excluded_revoked':1,'excluded_expired':0}
    if name=='rm_shared_summary':result={**result,'data':{**result['data'],**revoked}}
    if name=='rm_handover_sources' and result['data'].get('eligible'):result={**result,'data':{**result['data'],'shared':revoked}}
    return result


# fault -> (patch target, replacement); None would mean the piece is not built yet.
FAULTS={'external_down':('app.services.external.fetch',_external_down),'upstream_withheld':('app.services.agents.runner.checked_model_answer',_withheld),
    'receipt_failed':('app.services.offers.confirm',_receipt_failed),'consent_revoked':('app.services.rm.records.rpc',_consent_revoked)}


@contextmanager
def inject(fault: str | None):
    """Yields whether the fault was applied."""
    with ExitStack() as stack:
        if fault and (spec:=FAULTS[fault]):stack.enter_context(patch(spec[0],spec[1]))
        yield bool(fault and FAULTS[fault])


def sign_in(persona: str,fixture: list[dict[str,str]],url: str,public_key: str) -> str:
    """The persona's own access token from Supabase Auth; the password never leaves the fixture file."""
    from supabase import create_client
    email=PERSONAS[persona]
    login=next((x for x in fixture if x['email']==email),None)
    if login is None:raise LookupError(f'No login for {persona} in the persona fixture')
    session=create_client(url,public_key).auth.sign_in_with_password({'email':email,'password':login['password']}).session
    return session.access_token


def sse(body: str) -> list[tuple[str,str]]:
    """(event, data) pairs from an SSE body."""
    found=[]
    for block in re.split(r'\r?\n\r?\n',body):
        lines=block.splitlines()
        event=next((l[6:].strip() for l in lines if l.startswith('event:')),'message')
        data='\n'.join(l[5:].removeprefix(' ') for l in lines if l.startswith('data:'))
        if any(l.startswith(('event:','data:')) for l in lines):found.append((event,data))
    return found


def source_id(value: str) -> str:
    return re.sub(r'^kb:','',value).split('@')[0].split('#')[0]


def turns_of(inputs: dict[str,Any]) -> list[dict[str,str]]:
    """QA1 examples carry turns; the public datasets carry a question (the planner-only one a `say`) with earlier user
    messages as history."""
    if 'turns' in inputs:return inputs['turns']
    return [*({'say':m['content']} for m in inputs.get('history',[]) if m['role']=='user'),{'say':inputs.get('question',inputs.get('say'))}]


async def plan_only(inputs: dict[str,Any]) -> dict[str,Any]:
    """The planner-only dataset: the planner node alone (rules, one planner call, overrides and the plan checks), with no
    agents, no database writes and no saved chat. `open_offers` stands in for the client's open offer groups in this chat.
    An RM persona gets the RM planner (one rm_planner call, the intent-to-agent map and the plan check) without the book
    reads, so the planner sees no client names."""
    from types import SimpleNamespace
    from app.schemas.contracts import Principal,RequestContext
    from app.services import offers,orchestrator
    if inputs['persona']=='sophie':return await plan_only_rm(inputs)
    mode='public' if inputs['persona']=='guest' else 'personal'
    req=ChatRequest(messages=[*inputs.get('history',[]),{'role':'user','content':inputs['say']}],mode=mode)
    risk=assess_request(req)
    if risk.fixed_response is not None:return {'played':True,'plan':{'route':'fixed','tasks':[]},'model_calls':0}
    principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt='evaluation-planner-only') if mode=='personal' else Principal(kind='guest')
    tctx=orchestrator.TurnContext(request=RequestContext(session_id=uuid4(),principal=principal),turn_id=uuid4(),conversation_id=uuid4())
    async def open_groups(ctx,conversation):
        return {uuid4():[] for _ in range(inputs.get('open_offers',0))}
    spans: list[tuple[str,dict[str,Any]]]=[];token=span_sink.set(spans)
    try:
        with patch.object(offers,'open_groups',open_groups):
            update=await orchestrator.planner(orchestrator.start(req,risk),SimpleNamespace(context=tctx))
    finally:span_sink.reset(token)
    fixed_route=next((attrs.get('route') for name,attrs in spans if name=='planner'),None)
    route=fixed_route if fixed_route in {'ask_which','expired'} else 'act' if update.get('confirm') else 'plan' if update.get('tasks') else 'rephrase' if update.get('lines') else 'none'
    tasks=plan_rows(update.get('tasks') or [])
    return {'played':True,'plan':{'route':route,'tasks':tasks,'unlinked':len(update.get('lines') or []) if route=='plan' else 0},
        'model_calls':sum(name=='llm' for name,_ in spans),'prompt_ids':prompt_ids(spans)}


def plan_rows(tasks: list) -> list[dict[str,Any]]:
    by_id={t.id:t for t in tasks}
    return [{'agent':t.agent,'intent':t.intent,'priority':t.priority,'depends_on':by_id[t.depends_on].agent if t.depends_on in by_id else None,
        'condition':t.condition,'for_each':t.for_each,'entities':sorted(t.entities)} for t in tasks]


async def plan_only_rm(inputs: dict[str,Any]) -> dict[str,Any]:
    from app.schemas.contracts import Principal,RequestContext
    from app.schemas.rm import RmAssistantRequest
    from app.services import orchestrator
    from app.services.rm import graph
    async def say(text):pass
    req=RmAssistantRequest(message=inputs['say'])
    rt=graph.RmRuntime(req=req,ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),jwt='evaluation-planner-only')),say=say)
    spans: list[tuple[str,dict[str,Any]]]=[];token=span_sink.set(spans)
    try:tasks,lines=orchestrator.linked(graph.tasks_from(await graph.model_plan(rt,req.message),req.message),req)
    finally:span_sink.reset(token)
    return {'played':True,'plan':{'route':'plan','tasks':plan_rows(tasks),'unlinked':len(lines)},'model_calls':sum(name=='llm' for name,_ in spans),'prompt_ids':prompt_ids(spans)}


def rm_text(reply: dict[str,Any]) -> str:
    """An RM reply as one text, as the workspace shows it (frontend lib/rm.js replyText, unbounded)."""
    drafts=reply.get('drafts') or ([reply['draft']] if reply.get('draft') else [])
    return '\n'.join(filter(None,[reply.get('summary') or '',*(x for s in reply.get('sections') or [] for x in [s['heading'],*(f"- {i['text']}" for i in s['items'])]),
        *(f"Draft{' to '+d['client'] if d.get('client') else ''}: {d['text']}" for d in drafts)]))


async def play_rm(inputs: dict[str,Any],app,token: str) -> dict[str,Any]:
    """An RM conversation on POST /rm/assistant (the v5 RM graph), with the workspace context carried between turns."""
    log=NodeLog();spans: list[tuple[str,dict[str,Any]]]=[]
    replies: list[dict[str,Any]]=[];skipped: list[dict[str,str]]=[];history: list[dict[str,Any]]=[];context: dict[str,Any]={}
    keep=('client_id','ticket_ref','appointment_id','note_id','draft','documentation')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://evaluation',timeout=300) as http:
        for step in turns_of(inputs):
            if not step.get('say'):
                skipped.append(step);continue
            body={'message':step['say'],'history':history[-40:],'context':{k:context.get(k) for k in keep if context.get(k) is not None}}
            tokens=(node_log.set(log),span_sink.set(spans));start=len(spans)
            try:
                with inject(inputs.get('fault')):response=await http.post('/rm/assistant',headers={'Authorization':f'Bearer {token}'},json=body)
            finally:span_sink.reset(tokens[1]);node_log.reset(tokens[0])
            reply: dict[str,Any]={'say':step['say'],'status':response.status_code,'agents':[a.get('agent') for n,a in spans[start:] if n=='agent'],
                'model_calls':sum(n=='llm' for n,_ in spans[start:])}
            if response.status_code!=200:reply['error']=response.json().get('error',{}).get('code','http_error')
            for event,data in sse(response.text) if response.status_code==200 else []:
                if event=='answer':
                    answer=json.loads(data)
                    context={**(answer.get('context') or {})}
                    reply.update(answer=rm_text(answer),origin=answer.get('origin'),needs_human=answer.get('status')!='ok',status_text=answer.get('status'),
                        sources=sorted({s.get('id') for s in answer.get('sources') or []}),drafts=len(answer.get('drafts') or [])+bool(answer.get('draft')))
                elif event=='error':reply['error']=data
            replies.append(reply)
            client=context.get('client_id')
            history+=[{'role':'user','content':step['say'][:6000],'client_id':client},{'role':'assistant','content':(reply.get('answer') or '-')[:6000],'client_id':client}]
            if 'error' in reply:break
    waves=[attrs for name,attrs in spans if name=='gather']
    return {'answer':'\n\n'.join(r.get('answer') or '' for r in replies),'replies':replies,'routes':[None]*len(replies),'skipped':skipped,'not_offered':[],
        'played':bool(replies),'nodes':{'ran':sorted(log.ran),'acted':sorted(log.acted)},
        'agents':dict(Counter(attrs.get('agent') for name,attrs in spans if name=='agent')),
        'tools':sorted({attrs['tool'] for name,attrs in spans if name=='tool_call'}),'model_calls':sum(name=='llm' for name,_ in spans),'prompt_ids':prompt_ids(spans),
        'dependencies':{'waves':max((2 if w.get('sends') else 1 for w in waves),default=0),'exports':sorted({e for w in waves for e in w.get('exports',[])}),
            'skipped':{a:r for w in waves for a,r in w.get('skipped',{}).items()},'rerouted':next((w['rerouted'] for w in waves if w.get('rerouted')),{})},
        'fault':{'name':inputs['fault'],'applied':bool(FAULTS[inputs['fault']])} if inputs.get('fault') else None,'tickets':{'opened':[],'all':[]}}


async def tickets(http: httpx.AsyncClient,headers: dict[str,str]) -> list[dict[str,Any]]:
    response=await http.get('/tickets',headers=headers)
    response.raise_for_status()
    rows=TypeAdapter(list[CustomerTicket]).validate_python(response.json()['data'])
    return [row.model_dump(mode='json',include={'reference','category','summary','source','status','updated_at','last_update'}) for row in rows]


def handover_reply(answer: str) -> str | None:
    """Choose the actual offered reuse/send button; never submit without a visible preview."""
    if PREVIEW_MARK not in answer:return None
    offered=OFFERED.search(answer)
    if offered:return 'Add to '+offered.group(1)
    return SEND_REPLY if '“'+SEND_REPLY+'”' in answer else None


async def play(inputs: dict[str,Any],app,token: str | None) -> dict[str,Any]:
    """Run every playable turn in one new saved chat and return the replies plus what ran. An RM persona plays on the
    RM assistant instead (play_rm)."""
    if inputs.get('persona')=='sophie' and token:return await play_rm(inputs,app,token)
    mode,language=inputs.get('mode','public'),inputs.get('language','English')
    headers={'Authorization':f'Bearer {token}'} if token else {'X-Conversation-Token':secrets.token_urlsafe(32)}
    log=NodeLog();spans: list[tuple[str,dict[str,Any]]]=[];evidence: list[Evidence]=[]
    replies: list[dict[str,Any]]=[];skipped: list[dict[str,str]]=[];missing: list[dict[str,str]]=[];routes: list[str | None]=[];history: list[dict[str,str]]=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://evaluation',timeout=300) as http:
        before=await tickets(http,headers) if token else []
        session=str(uuid4())
        created=await http.post('/conversations',headers=headers,json={'session_id':session,'mode':mode,'language':language})
        created.raise_for_status()
        revision=created.json()['revision']
        for step in turns_of(inputs):
            tap=TAP_ALIASES.get(step.get('tap'),step.get('tap'))
            text=step.get('say') or (tap if tap in CHAT_TAPS else None)
            if 'confirm_rm_handover' in step:
                if step['confirm_rm_handover']!='reuse_or_send':raise ValueError('Unknown RM handover confirmation policy')
                text=handover_reply(replies[-1].get('answer') or '') if replies else None
                if text is None:missing.append(step);break
            if text is None:
                skipped.append(step);continue
            if 'tap' in step and not (replies and text in (replies[-1].get('answer') or '')):
                missing.append(step);break  # the browser shows this button only when the last reply offers it
            asked=[*history,{'role':'user','content':text}]
            fixed=assess_request(ChatRequest(messages=asked,mode=mode,language=language)).fixed_response
            routes.append(fixed.code if fixed else None)
            body={'messages':[{'role':'user','content':text}],'mode':mode,'language':language,
                'conversation_id':session,'turn_id':str(uuid4()),'revision':revision}
            tokens=(node_log.set(log),span_sink.set(spans),evidence_sink.set(evidence));start=len(spans);evidence_start=len(evidence)
            try:
                with inject(inputs.get('fault')):response=await http.post('/chat',headers=headers,json=body)
            finally:evidence_sink.reset(tokens[2]);span_sink.reset(tokens[1]);node_log.reset(tokens[0])
            reply: dict[str,Any]={'say':text,'status':response.status_code,'agents':[a.get('agent') for n,a in spans[start:] if n=='agent'],
                'model_calls':sum(n=='llm' for n,_ in spans[start:])}
            if response.status_code!=200:reply['error']=response.json().get('error',{}).get('code','http_error')
            for event,data in sse(response.text) if response.status_code==200 else []:
                if event=='conversation':revision=json.loads(data)['revision']
                elif event=='answer':
                    answer=json.loads(data)
                    cited={(s['id'],s['version']) for s in answer['sources']}
                    reply.update(answer=answer['answer'],origin=answer['response_origin'],needs_human=answer['needs_human'],
                        evidence=[item.model_dump(mode='json') for item in evidence[evidence_start:] if (item.source.id,item.source.version) in cited],
                        sources=sorted({source_id(s['id']) for s in answer['sources']}),
                        sections=[{'domain':s['domain'],'priority':s['priority'],'origin':s['response_origin']} for s in answer.get('sections') or []])
                elif event=='error':reply['error']=data
            replies.append(reply)
            history+=[{'role':'user','content':text},{'role':'assistant','content':reply.get('answer') or ''}]
            if 'error' in reply:break  # a failed turn ends the conversation
        after=await tickets(http,headers) if token else []
    agents=Counter(attrs.get('agent') for name,attrs in spans if name=='agent')
    known={t['reference'] for t in before}
    waves=[attrs for name,attrs in spans if name=='gather']  # one per turn that ran agents (phase 7)
    return {'answer':'\n\n'.join(r.get('answer') or '' for r in replies),'replies':replies,'routes':routes,'skipped':skipped,'not_offered':missing,'played':bool(replies),
        'nodes':{'ran':sorted(log.ran),'acted':sorted(log.acted)},'agents':dict(agents),
        'tools':sorted({attrs['tool'] for name,attrs in spans if name=='tool_call'}),
        'service_reads':sorted({attrs['tool'] for _,attrs in spans if 'call_id' in attrs and 'tool' in attrs}),
        'model_calls':sum(name=='llm' for name,_ in spans),'prompt_ids':prompt_ids(spans),
        'dependencies':{'waves':max((2 if w.get('sends') else 1 for w in waves),default=0),
            'exports':sorted({e for w in waves for e in w.get('exports',[])}),
            'skipped':{a:r for w in waves for a,r in w.get('skipped',{}).items()},
            'rerouted':next((w['rerouted'] for w in waves if w.get('rerouted')),{})},
        'fault':{'name':inputs['fault'],'applied':bool(FAULTS[inputs['fault']])} if inputs.get('fault') else None,
        'tickets':{'opened':[t for t in after if t['reference'] not in known],'all':after}}
