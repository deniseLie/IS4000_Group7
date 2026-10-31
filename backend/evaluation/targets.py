"""LangSmith target (docs/evaluation/README.md §4): plays one dataset example through the real app, in process.

Each example's turns go to POST /chat in a saved chat, exactly as the browser sends them, as a guest or as the persona
signed in with its own JWT (password login from the git-ignored persona fixture, as in tests/test_rls_live.py). Nothing
is faked: the real guardrail, v2 graph, agents, tools, model, database and RLS run. While a turn runs the target
records which graph nodes acted (a LangChain callback hook) and which agents, tools and model calls ran (the
observability span sink), and it reads the persona's tickets before and after so evaluators can see what opened.
Steps the chat cannot play (form, upload, a button other than a chat reply) are skipped and listed in the output; a chat
reply button is played only when the previous reply offers it, otherwise the conversation stops there (`not_offered`)."""
import json
import re
import secrets
from collections import Counter
from contextvars import ContextVar
from typing import Any
from uuid import UUID,uuid4
import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.tracers.context import register_configure_hook
from app.core.observability import span_sink
from app.schemas.chat import ChatRequest
from app.services.fraud_reports import CONFIRM_REPLY
from app.services.guardrail import assess_request

PERSONAS={'michelle':'demo.michelle@example.com','sophie':'demo.sophie@example.com'}
CHAT_TAPS={CONFIRM_REPLY}  # buttons whose label is sent as the next chat message
NODES=('plan','prioritise','dispatch','propose_actions','review_actions','settle_actions','escalate_validation','compose')


class NodeLog(AsyncCallbackHandler):
    """A graph node acted when its update changes the state (a new or different non-empty value) or it paused the turn."""
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
        if (node:=self.inputs.pop(run_id,None)) is not None and type(error).__name__=='GraphInterrupt':self.acted.add(node[0])


node_log: ContextVar[NodeLog | None]=ContextVar('eval_node_log',default=None)
register_configure_hook(node_log,True)  # every LangChain/LangGraph run in this context reports to the active NodeLog


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
    """QA1 examples carry turns; the public datasets carry a question with earlier user messages as history."""
    if 'turns' in inputs:return inputs['turns']
    return [*({'say':m['content']} for m in inputs.get('history',[]) if m['role']=='user'),{'say':inputs['question']}]


async def tickets(http: httpx.AsyncClient,headers: dict[str,str]) -> list[dict[str,Any]]:
    response=await http.get('/tickets',headers=headers)
    response.raise_for_status()
    return [{k:row.get(k) for k in ('reference','category','status')} for row in response.json()['data']]


async def play(inputs: dict[str,Any],app,token: str | None) -> dict[str,Any]:
    """Run every playable turn in one new saved chat and return the replies plus what ran."""
    mode,language=inputs.get('mode','public'),inputs.get('language','English')
    headers={'Authorization':f'Bearer {token}'} if token else {'X-Conversation-Token':secrets.token_urlsafe(32)}
    log=NodeLog();spans: list[tuple[str,dict[str,Any]]]=[]
    replies: list[dict[str,Any]]=[];skipped: list[dict[str,str]]=[];missing: list[dict[str,str]]=[];routes: list[str | None]=[];history: list[dict[str,str]]=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://evaluation',timeout=300) as http:
        before=await tickets(http,headers) if token else []
        session=str(uuid4())
        created=await http.post('/conversations',headers=headers,json={'session_id':session,'mode':mode,'language':language})
        created.raise_for_status()
        revision=created.json()['revision']
        for step in turns_of(inputs):
            text=step.get('say') or (step.get('tap') if step.get('tap') in CHAT_TAPS else None)
            if text is None:
                skipped.append(step);continue
            if 'tap' in step and not (replies and text in (replies[-1].get('answer') or '')):
                missing.append(step);break  # the browser shows this button only when the last reply offers it
            asked=[*history,{'role':'user','content':text}]
            fixed=assess_request(ChatRequest(messages=asked,mode=mode,language=language)).fixed_response
            routes.append(fixed.code if fixed else None)
            body={'messages':[{'role':'user','content':text}],'mode':mode,'language':language,
                'conversation_id':session,'turn_id':str(uuid4()),'revision':revision}
            tokens=(node_log.set(log),span_sink.set(spans));start=len(spans)
            try:response=await http.post('/chat',headers=headers,json=body)
            finally:span_sink.reset(tokens[1]);node_log.reset(tokens[0])
            reply: dict[str,Any]={'say':text,'status':response.status_code,'agents':[a.get('agent') for n,a in spans[start:] if n=='task'],
                'model_calls':sum(n=='llm' for n,_ in spans[start:])}
            if response.status_code!=200:reply['error']=response.json().get('error',{}).get('code','http_error')
            for event,data in sse(response.text) if response.status_code==200 else []:
                if event=='conversation':revision=json.loads(data)['revision']
                elif event=='answer':
                    answer=json.loads(data)
                    reply.update(answer=answer['answer'],origin=answer['response_origin'],needs_human=answer['needs_human'],
                        sources=sorted({source_id(s['id']) for s in answer['sources']}),
                        sections=[{'domain':s['domain'],'priority':s['priority'],'origin':s['response_origin']} for s in answer.get('sections') or []])
                elif event=='workflow_paused':reply.update(paused=True,answer=json.loads(data).get('preview',{}).get('answer',''))
                elif event=='error':reply['error']=data
            replies.append(reply)
            history+=[{'role':'user','content':text},{'role':'assistant','content':reply.get('answer') or ''}]
            if reply.get('paused') or 'error' in reply:break  # a paused or failed turn ends the conversation
        after=await tickets(http,headers) if token else []
    agents=Counter(attrs.get('agent') for name,attrs in spans if name=='task')
    if mode=='public':agents=Counter({'answer':sum(agents.values())}) if agents else Counter()  # guests use the shared answer agent
    known={t['reference'] for t in before}
    return {'answer':'\n\n'.join(r.get('answer') or '' for r in replies),'replies':replies,'routes':routes,'skipped':skipped,'not_offered':missing,'played':bool(replies),
        'nodes':{'ran':sorted(log.ran),'acted':sorted(log.acted)},'agents':dict(agents),
        'tools':sorted({attrs['tool'] for name,attrs in spans if name=='tool_call'}),
        'model_calls':sum(name=='llm' for name,_ in spans),
        'tickets':{'opened':[t for t in after if t['reference'] not in known],'all':after}}
