"""The RM agents' intent handlers (Persona 2), moved from services/rm/assistant.py in build plan phase 9. The RM graph
(services/rm/graph.py) plans the turn and runs each task on the Client, Requests or Calendar agent (agents/registry.py,
build plan 3a); the agent runs its intent's handler here, or the bounded loop (h_open) for an open question. No canned
answers: what is already a record is written by code from the RM's own records (the client reports' record sections,
services/rm/brief_facts.py; the shared handover note; the Calendar's decision and availability cards, which the RM
approves through the existing write routes), and what needs judgement is written by the model and checked item by item
(services/rm/checks.py).

1. Before an agent runs, the graph has done the fixed book check (anyone outside the RM's book is refused and audited
   before any record is read, R06) and resolves the client, ticket or meeting against the book (Turn.resolve_client);
   ambiguity becomes a question with options. Only turns about the same client go to the model.
2. The handler reads its intent's records through the RM tools with the agent's own context, so the tool layer allows
   only that spec's tools (SQL book checks, consent checks, audit). Turn.reads keeps them for the exports (rm/graph.py).
3. One model call (prompt rm_assistant/v6 + the intent's task file) with strict JSON output and cited record ids. A model
   call's time follows the tokens it writes (docs/planning/rm-answer-speed.md), so the client reports (meeting_brief,
   client_overview, handover_brief, meeting_history) give the model a bounded set of facts (brief_facts.facts) and ask it
   only for the judgement parts; code writes the record sections. For
   `today` and `priorities` code has already ranked the records (services/rm/priority.py, build plan 3b): the model
   explains each record's score parts and code puts the items back in rank order (in_rank_order). The model sees only
   the top RANKED_SHOWN ranked records (and FOLLOWUPS_SHOWN other follow-ups), so the answer's length stays bounded
   however full her queue is; code adds one fixed line counting the rest (not_listed).
4. Checks: figures, dates and references must be in the cited records; no advice, decisions, action claims, secrets or
   links. Failing items are withheld and counted. Proposed actions must quote the RM's notes and are checked for
   missing fields and duplicates. Nothing is saved or sent here: the RM approves each write separately.
"""
import asyncio
import inspect
import json
import re
from collections import Counter
from collections.abc import Awaitable,Callable
from datetime import date,datetime,timedelta,timezone
from difflib import SequenceMatcher
from typing import Any
from app.schemas.contracts import RequestContext
from app.schemas.rm import BOOK_WIDE,NEEDS_CLIENT,RmAssistantRequest
from app.services import customer_data,llm_gateway,prompts,tools
from app.services.agents import registry,runner
from app.services.authorization import PolicyDenied
from app.services.privacy import redact
from app.services.rm import brief_facts,checks,priority
from app.services.rm import evidence as ev
from app.services.rm.records import RmDenied

SGT=timezone(timedelta(hours=8))
TASK_FILE={'ticket_inbox':'inbox','priorities':'priorities','reviews':'reviews','schedule':'schedule','client_overview':'overview',
    'meeting_history':'meetings','meeting_brief':'brief','ticket_summary':'ticket','draft_message':'draft','document_interaction':'documentation',
    'actions_from_note':'actions','handover_brief':'handover','book_question':'book','help':'help',
    'meeting_requests':'meeting_requests','availability_change':'availability','open':'open','today':'today'}  # decide_request: a card from code, no model
# Hidden reasoning per intent: one table, set by code, never by a model. Off for every RM answer: those tokens count against
# MAX_TOKENS and vary per call (1,300-3,600 measured), so with reasoning on 7 of 11 QA1 RM cases and 3 of 5 call notes (R13) hit the
# output limit; off, they answered in about a third of the time. An intent turns it back on only with evaluation evidence and
# output headroom. The planner (graph.model_plan) and the open loop's steps (runner.loop) keep the model's default: short outputs.
REASONING={intent:False for intent in TASK_FILE}
HANDOVER_SECTIONS=['Client overview','Open requests and tickets','Recorded commitments','Pending reviews','Recorded preferences',
    'What the client was previously told','Gaps to clarify','Suggested introductory topics']
STOP={'the','a','an','to','of','for','and','on','my','me','his','her','their','about','please','request','reply','ticket','client','draft','with','what','is','in'}
# Question and function words carry no topic: "Should we approve…" must not match a ticket that says "Should I invest…" (R08).
STOP|={'should','could','would','will','can','shall','may','might','must','we','you','our','your','she','him','they','them','this','that',
    'these','those','how','why','when','where','who','which','does','did','has','have','had','was','were','are','any','from','into','not',
    'but','just','also','now','still','there','here','its','client','question','asked','tell','show','give','get','need','want','know'}
# Words that ask about a request without saying what it is about ("summarise her request", "any update?").
GENERIC={'summarise','summarize','summary','brief','explain','check','update','updates','status','latest','open','current','see','look',
    'help','happening','going','progress','next','new','reply','respond','answer','write','message','handle','deal'}
Say=Callable[[str],Awaitable[None]]
Show=Callable[[dict],Awaitable[None]]  # a `section` event on /rm/assistant (docs/planning/rm-answer-speed.md slice 4)


# Schemas (strict JSON) -----------------------------------------------------------------------------------------------
def _ids(ids: list[str]) -> dict:return {'type':'array','items':{'type':'string',**({'enum':ids} if ids else {})}}


def _obj(props: dict) -> dict:return {'type':'object','additionalProperties':False,'properties':props,'required':list(props)}


S={'type':'string'}
CLARIFY={'clarification_question':S,'clarification_options':{'type':'array','items':S}}


def brief_schema(ids: list[str]) -> dict:
    item=_obj({'text':S,'kind':{'type':'string','enum':['record','client_stated','suggestion','gap']},
        'owner':{'type':'string','enum':['rm','specialist','client','none']},'source_ids':_ids(ids)})
    return {'title':'rm_brief',**_obj({'status':{'type':'string','enum':['ok','needs_clarification']},'summary':S,
        'sections':{'type':'array','items':_obj({'heading':S,'items':{'type':'array','items':item}})},**CLARIFY})}


def draft_schema(ids: list[str]) -> dict:
    return {'title':'rm_draft',**_obj({'status':{'type':'string','enum':['ok','needs_clarification']},'draft':S,
        'facts_used':{'type':'array','items':_obj({'text':S,'source_ids':_ids(ids)})},'cautions':{'type':'array','items':S},
        'suggested_status':{'type':'string','enum':['none','in_review','waiting_on_client','resolved']},**CLARIFY})}


def documentation_schema(categories: list[str]) -> dict:
    item=_obj({'category':{'type':'string','enum':['fact','decision','commitment','client_request','topic']},'text':S,
        'certainty':{'type':'string','enum':['confirmed','tentative']},'origin':{'type':'string','enum':['notes','rm_correction','ai_inference']},'owner':S,'due':S})
    action=_obj({'kind':{'type':'string','enum':['client_note','follow_up_task','service_request','meeting']},'title':S,'details':S,'owner':S,'due':S,
        'category':{'type':'string','enum':[*categories,'']},'fields':{'type':'array','items':_obj({'key':S,'value':S})},'evidence_quote':S,
        'origin':{'type':'string','enum':['notes','rm_correction','ai_inference']}})
    return {'title':'rm_documentation',**_obj({'status':{'type':'string','enum':['ok','needs_clarification']},'summary':S,
        'interaction':_obj({'met_on':S,'format':{'type':'string','enum':['video','phone','in_person','unknown']},'title':S}),
        'items':{'type':'array','items':item},'gaps':{'type':'array','items':S},'actions':{'type':'array','items':action},**CLARIFY})}


# Small deterministic helpers --------------------------------------------------------------------------------------
def today() -> date:return datetime.now(SGT).date()


def day_text(d: date) -> str:return f'{d:%a} {d.day} {d:%b %Y}'  # portable (no %-d on Windows)


def time_text(hhmm: str) -> str:
    h,m=(int(x) for x in hhmm.split(':'))
    return f"{h%12 or 12}:{m:02d} {'am' if h<12 else 'pm'}"


def name_parts(name: str) -> list[str]:return re.sub(r'[^a-z ]','',name.lower()).split()


def names_match(query: str,name: str) -> bool:
    """Same rule as rm_match_clients: two consecutive name parts or one part of four letters or more, as whole words;
    a near spelling of a first or last name also counts (typos)."""
    q=' '+re.sub(r'[^a-z ]',' ',query.lower())+' '
    parts=name_parts(name)
    if any(len(p)>=4 and f' {p} ' in q for p in parts) or any(f' {a} {b} ' in q for a,b in zip(parts,parts[1:])):return True
    words=q.split()
    return any(len(w)>=4 and len(p)>=4 and SequenceMatcher(None,w,p).ratio()>=0.85 for w in words for p in parts)


def keywords(text: str) -> set[str]:return {w for w in re.findall(r'[a-z]{3,}',text.lower()) if w not in STOP}


def ticket_words(ticket: dict) -> set[str]:
    return keywords(f"{ticket.get('category_label','')} {ticket.get('category','').replace('_',' ')} {ticket.get('summary') or ''}")


def pick_ticket(message: str,tickets: list[dict]) -> tuple[str|None,list[dict]]:
    """A unique best keyword match between the message and a ticket's type or summary, else the candidates to ask about.
    A lone open ticket needs a match too (R08: "approve Michelle's limit increase" is not about her only, unrelated ticket)."""
    words=keywords(message)
    scored=sorted(((len(words&ticket_words(t)),t) for t in tickets),key=lambda pair:-pair[0])
    if scored and scored[0][0]>0 and (len(scored)==1 or scored[0][0]>scored[1][0]):return scored[0][1]['reference'],tickets
    return None,tickets


WEEKDAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
TIME=re.compile(r'\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b|\b(\d{1,2})[:.](\d{2})\b',re.I)


def narrows(hint: str) -> bool:
    """Whether a hint names a time or a day ("10am", "Tuesday", "tomorrow"), so it can pick out one appointment."""
    text=(hint or '').lower()
    return bool(TIME.search(text) or 'today' in text or 'tomorrow' in text or any(w in text or w[:3]+' ' in text+' ' for w in WEEKDAYS))


def match_appointments(rows: list[dict],hint: str,customer_id: int|None) -> list[dict]:
    """Appointments that fit a time/day hint ("10am", "Tuesday", "tomorrow 2:30pm") for the client, if one is set."""
    rows=[r for r in rows if customer_id is None or r['customer_id']==customer_id]
    text=(hint or '').lower()
    if not text:return rows
    day=None
    if 'today' in text:day=today()
    elif 'tomorrow' in text:day=today()+timedelta(days=1)
    wd=next((i+1 for i,w in enumerate(WEEKDAYS) if w in text or w[:3]+' ' in text+' '),None)
    m=TIME.search(text)
    minutes=None
    if m:
        h,mi,ap=(int(m.group(1)),int(m.group(2) or 0),m.group(3).lower()) if m.group(1) else (int(m.group(4)),int(m.group(5)),'')
        h=h%12+(12 if ap=='pm' else 0) if ap else h
        minutes=h*60+mi
    def fits(r):
        local=datetime.fromisoformat(str(r['starts_at'])).astimezone(SGT)
        return (day is None or local.date()==day) and (wd is None or local.isoweekday()==wd) and (minutes is None or local.hour*60+local.minute==minutes)
    return [r for r in rows if fits(r)]


def history_for(req: RmAssistantRequest,client_id: int|None,book_wide: bool) -> list[tuple[str,str]]:
    """Only turns about the same client (or book-wide turns for book-wide questions) reach the model."""
    keep=[t for t in req.history if (t.client_id is None if book_wide else t.client_id==client_id)]
    turns=[(t.role,redact(t.content)[0][:3000]) for t in keep][-12:]
    while turns and turns[0][0]!='user':turns.pop(0)
    return turns


def clarify(question: str,options: list[dict],intent: str,context: dict,origin: str='records') -> dict:
    return {'status':'clarify','intent':intent,'summary':question,'sections':[],'clarification':{'question':question,'options':options[:8]},
        'context':context,'sources':[],'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':origin}


def refusal(context: dict) -> dict:
    """R06: no record of anyone outside the book is read and the model is not called; the attempt is audited in SQL."""
    return {'status':'denied','intent':'access_check','summary':'That person is not in your client book, so I can’t show or discuss their records. '
        'If you need something for them, contact their relationship manager through the bank’s usual process.','sections':[],'clarification':None,
        'context':context,'sources':[],'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'access_check'}


SPECIALIST_NOTES=re.compile(r'\b(?:fraud|specialist)\b.{0,80}\b(?:internal|restricted|private)\b.{0,40}\bnotes\b',re.I)
CHAT_WRITE=re.compile(r'^\s*(?:please\s+)?(?:(?:mark|close|resolve)\b.{0,70}\bticket\b|(?:block|freeze|unfreeze|cancel)\b.{0,50}\bcard\b)',re.I)


def capability_refusal(message: str,context: dict) -> dict | None:
    """Explain a forbidden capability before asking which ticket; never reads specialist detail or writes."""
    if not (SPECIALIST_NOTES.search(message) or CHAT_WRITE.search(message)):return None
    return {'status':'denied','intent':'access_check','summary':'I can’t show specialist teams’ internal notes, change ticket status or carry out card actions from this chat. '
        'The specialist team controls its investigation and ticket status; card operations handles card actions. '
        'Select a reference in Ticket inbox to see its client-safe status. '
        'For a ticket assigned to you, use the workspace controls and review the exact update before approving it. Nothing has been changed.',
        'sections':[],'clarification':None,'context':context,'sources':[],
        'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'access_check'}


# One agent's turn ----------------------------------------------------------------------------------------------------
class Turn:
    """ctx is the agent's context (its spec's tools only); base is the RM's own, for the book check and resolution."""
    def __init__(self,req: RmAssistantRequest,ctx: RequestContext,say: Say,base: RequestContext|None=None,book: list[dict]|None=None,
        on_section: Show|None=None,task_id: str|None=None):
        self.req,self.ctx,self.say,self.base=req,ctx,say,base or ctx
        self.on_section,self.task_id=on_section,task_id
        self.message,self.redacted=redact(req.message)
        self.book: list[dict]=book or []
        self.prompt_ids: list[str]=[]
        self.reads: dict[str,dict]={}
        self.inputs: dict={}  # exports from upstream tasks (orchestrator.gather), set by rm/graph.py
        self.facts: dict[str,Any]={}  # codes and counts for the agent span (the priority score's factors)

    async def read(self,name: str,args: dict) -> dict:
        self.reads[name]=result=await tools.call(name,args,self.ctx)
        return result

    async def show(self,sections: list[dict],bundle: ev.Bundle):
        """Sections the RM may see before the answer: written by code from the records, or model items after their checks.
        Never unchecked model text. The final answer repeats them in their place."""
        if self.on_section and sections:
            await self.on_section({'task_id':self.task_id,'sections':sections,
                'sources':_sources(bundle,[i for s in sections for x in s['items'] for i in x['source_ids']])})

    def name(self,customer_id: int|None) -> str|None:
        return next((c['name'] for c in self.book if c['customer_id']==customer_id),None)

    def context(self,client_id: int|None,**extra) -> dict:
        return {'client_id':client_id,'client_name':self.name(client_id),'ticket_ref':None,'appointment_id':None,'note_id':None,**extra}

    async def resolve_client(self,plan: dict,named: list[dict]) -> tuple[int|None,dict|None]:
        """Returns (client id, clarification reply or refusal)."""
        req,c=self.req,self.req.context
        intent=plan['intent']
        candidates=[x['customer_id'] for x in named]
        ref=(plan.get('ticket_ref') or '').upper().strip()
        if re.fullmatch(r'TCK-\d{6,}',ref):
            detail=await tools.call('rm_ticket',{'reference':ref},self.base)  # refused and audited if the ticket's client is outside the book
            candidates=[detail['data']['customer_id']]
        if not candidates and plan.get('client_name'):
            candidates=[x['customer_id'] for x in self.book if names_match(plan['client_name'],x['name'])]
            if not candidates:
                found=(await tools.call('rm_book',{'query':plan['client_name'][:80]},self.base))['data']  # audits a refused lookup
                if not found:
                    return None,clarify('Which client is this about?',[{'label':x['name'],'client_id':x['customer_id']} for x in self.book],
                        intent,self.context(c.client_id))
                candidates=[x['customer_id'] for x in found]
        if not candidates:
            if intent=='document_interaction' and c.documentation and not plan.get('is_new_notes'):candidates=[c.documentation.customer_id]
            elif intent=='draft_message' and c.draft:candidates=[c.draft.customer_id]
            elif c.client_id and (plan.get('uses_active_client') or intent in NEEDS_CLIENT):candidates=[c.client_id]
            elif intent=='meeting_brief' and narrows(plan.get('appointment_hint') or ''):
                rows=(await tools.call('rm_appointments',{},self.base))['data']
                matches=[r for r in match_appointments(rows,plan['appointment_hint'],None) if r['status']=='confirmed']
                if len(matches)>1:
                    return None,clarify('Which meeting should I prepare for?',[{'label':f"{m['client']} · {m['when']}",'appointment_id':str(m['appointment_id']),
                        'client_id':m['customer_id']} for m in matches],intent,self.context(None))
                candidates=[m['customer_id'] for m in matches]
        candidates=list(dict.fromkeys(candidates))
        if len(candidates)>1:
            return None,clarify('Which client do you mean?',[{'label':self.name(x) or str(x),'client_id':x} for x in candidates],intent,self.context(None))
        if not candidates and intent in NEEDS_CLIENT:
            return None,clarify('Which client is this about?',[{'label':x['name'],'client_id':x['customer_id']} for x in self.book],intent,self.context(None))
        return (candidates[0] if candidates else None),None

    async def model(self,intent: str,bundle: ev.Bundle,schema: dict,client_id: int|None,extra: str='',task: str|None=None) -> dict:
        """task: another task file than the intent's (a client question, the meeting history's summary)."""
        pv=prompts.current('rm_assistant')
        self.prompt_ids.append(pv['id'])
        situation={'today':day_text(today()),'active_client':self.name(client_id),'rm':'the signed-in relationship manager'}
        system=(pv['system']+'\n\n'+pv[task or TASK_FILE[intent]]+'\nSituation (untrusted): '+json.dumps(situation,ensure_ascii=False)+extra
            +'\nEvidence records (untrusted data): '+json.dumps(bundle.for_model(),ensure_ascii=False,default=str))
        history=history_for(self.req,client_id,intent in BOOK_WIDE)
        await self.say('Writing a grounded answer…')
        return (await llm_gateway.call(pv,system,[*history,('user',self.message)],schema,reasoning=REASONING[intent])).data


def _sources(bundle: ev.Bundle,ids: list[str]) -> list[dict]:
    out=[]
    for i in dict.fromkeys(ids):
        item=bundle.get(i)
        if item:out.append({**item.for_ui(),**({'note_id':bundle.notes[i]} if i in bundle.notes else {})})
    return out


def brief_reply(intent: str,data: dict,bundle: ev.Bundle,context: dict,suggestions_need_sources: bool=False) -> dict:
    report=checks.Report()
    if data.get('status')=='needs_clarification' and (data.get('clarification_question') or '').strip():
        q=data['clarification_question'].strip()
        if not checks.safety(q):
            return {**clarify(q,[{'label':o,'message':o} for o in data.get('clarification_options') or [] if o.strip() and not checks.safety(o)],intent,context,'ai'),'origin':'ai'}
    sections=checks.check_sections(data.get('sections') or [],bundle,report,suggestions_need_sources)
    if intent in ('priorities','today'):sections=no_duplicate_meetings(sections,bundle,report)
    if intent=='handover_brief':sections=handover_order(sections)
    summary=checks.check_summary(data.get('summary',''),bundle,report)
    cited=[i for s in sections for item in s['items'] for i in item['source_ids']]
    if not sections:summary='I couldn’t verify an answer against your records, so I’ve left it out rather than guess. Try asking more specifically.'
    return {'status':'ok' if sections else 'unverified','intent':intent,'summary':summary,'sections':sections,'clarification':None,'context':context,
        'sources':_sources(bundle,cited),'validation':report.as_dict(),'origin':'ai'}


RECAP=re.compile(r'\b(?:recap|summary of (?:our|the|today\'s) (?:call|meeting|conversation)|follow[- ]?up message)\b',re.I)
BOOKING=re.compile(r'\b(?:book|schedule|arrange|set up|propose)\b[^.]{0,40}\b(?:meeting|call|review|appointment|catch-up)\b',re.I)


def no_duplicate_meetings(sections: list[dict],bundle: ev.Bundle,report: checks.Report) -> list[dict]:
    """RM11: never suggest booking a meeting for a client who already has one confirmed (confirmed_meeting on a follow-up
    or ranked record)."""
    confirmed={e.customer_id for e in bundle.items if isinstance(e.data,dict) and e.data.get('confirmed_meeting')}
    clients={e.customer_id:(e.data.get('client') or '') for e in bundle.items if isinstance(e.data,dict) and e.customer_id and e.data.get('client')}
    out=[]
    for s in sections:
        keep=[]
        for item in s['items']:
            cited={bundle.get(i).customer_id for i in item['source_ids'] if bundle.get(i)}
            named={cid for cid,name in clients.items() if name and name.split()[0] in item['text']}
            if item.get('kind')=='suggestion' and BOOKING.search(item['text']) and (cited|named)&confirmed:
                report.withheld.append('duplicate_meeting_suggestion');continue
            keep.append(item)
        if keep:out.append({**s,'items':keep})
    return out


TODAY_SECTIONS=['Do first','Meeting requests to answer','Client requests','Meetings in the next 7 days','Deadlines and tasks']
PRIORITY_SECTIONS=['Do first','Coming up','Specialist-managed (for awareness)']


def in_rank_order(sections: list[dict],bundle: ev.Bundle,headings: list[str]) -> list[dict]:
    """Build plan 3b: the order is code's, never the model's. Sections in the task file's order (others after them, as
    written) and items within a section by the best rank among the records they cite (an item citing no ranked record
    keeps its place after the ranked ones)."""
    def rank(item: dict) -> int:
        ranks=[e.data['rank'] for i in item['source_ids'] if (e:=bundle.get(i)) and isinstance(e.data,dict) and isinstance(e.data.get('rank'),int)]
        return min(ranks,default=10**6)
    place={h.lower():n for n,h in enumerate(headings)}
    return [{**s,'items':sorted(s['items'],key=rank)} for s in sorted(sections,key=lambda s:place.get(s['heading'].strip().lower(),len(headings)))]


# Context budget for `today` and `priorities`: the prompt writes one item per record it is given, so the answer grows with
# her queue (18 records ran into MAX_TOKENS). The model gets the top records only; the rest are counted in a fixed line.
RANKED_SHOWN,FOLLOWUPS_SHOWN=10,5
RANKED_WORDS={'request':('client request','client requests'),'meeting_request':('meeting request to answer','meeting requests to answer'),
    'review':('compliance review','compliance reviews'),'meeting':('confirmed meeting','confirmed meetings'),'task':('task','tasks'),
    'milestone':('milestone','milestones')}
FOLLOWUP_WORDS={'specialist_ticket':('specialist-managed ticket','specialist-managed tickets'),'commitment':('recorded commitment','recorded commitments'),
    'review_deadline':('compliance review','compliance reviews'),'meeting':('meeting','meetings'),'task':('task','tasks'),
    'milestone':('milestone','milestones'),'rm_ticket':('client request','client requests')}


def counted(kinds: list,words: dict) -> str:
    """'3 client requests, 1 task and 1 milestone': counts in the order of words, any other kind last."""
    n=Counter(kinds)
    parts=[f"{n[k]} {words.get(k,('other item','other items'))[n[k]!=1]}" for k in sorted(n,key=lambda k:list(words).index(k) if k in words else len(words))]
    return ', '.join(parts[:-1])+' and '+parts[-1] if len(parts)>1 else parts[0]


def not_listed(ranked: list[dict],followups: list[dict]=()) -> dict|None:
    """The fixed section (no model, counts only) for the records left out of the model's evidence: lower-ranked items
    (priority.rank rows) and other follow-ups (rm_followups rows)."""
    lines=[]
    if ranked:lines.append(f"{len(ranked)} lower-ranked {'item is' if len(ranked)==1 else 'items are'} not listed here: "
        f"{counted([x['kind'] for x in ranked],RANKED_WORDS)}. Your Today page and Ticket inbox show them.")
    if followups:lines.append(f"{len(followups)} more {'follow-up is' if len(followups)==1 else 'follow-ups are'} not listed here: "
        f"{counted([f.get('kind') for f in followups],FOLLOWUP_WORDS)}. Your Follow-ups page shows them.")
    return {'heading':'Not listed here','items':[{'text':t,'kind':'gap','owner':'none','source_ids':[]} for t in lines]} if lines else None


def with_rest(reply: dict,rest: dict|None) -> dict:
    """A checked answer ends with the not-listed line; a clarification or an unverified reply is left as it is."""
    return {**reply,'sections':[*reply['sections'],rest]} if rest and reply.get('status')=='ok' and reply.get('sections') else reply


def confirmed_meetings(followups: list[dict]) -> dict[int,str]:
    """Each client's next confirmed meeting (rm_followups confirmed_meeting), so a ranked item can refer to it (RM11)."""
    return {f['customer_id']:f['confirmed_meeting'] for f in followups if f.get('confirmed_meeting')}


def fed(t: 'Turn',name: str) -> list[dict]|None:
    """An upstream's list export passed in by orchestrator.gather (None when it was not passed)."""
    export=t.inputs.get(name)
    return export.value if export is not None and isinstance(export.value,list) else None


def handover_order(sections: list[dict]) -> list[dict]:
    """RM16: the fixed structure, in order; an empty heading becomes an explicit gap."""
    by={s['heading'].strip().lower():s for s in sections}
    out=[]
    for heading in HANDOVER_SECTIONS:
        found=by.get(heading.lower()) or next((s for k,s in by.items() if heading.lower().split()[0] in k and heading.lower().split()[-1] in k),None)
        out.append({'heading':heading,'items':found['items'] if found else [{'text':'Nothing recorded or nothing could be verified for this section.','kind':'gap','owner':'none','source_ids':[]}]})
    return out


# Intent handlers ------------------------------------------------------------------------------------------------------
async def h_inbox(t: Turn,plan,client_id):
    await t.say('Reading open tickets across your book…')
    bundle=ev.tickets_bundle(await t.read('rm_tickets',{}))
    return brief_reply('ticket_inbox',await t.model('ticket_inbox',bundle,brief_schema(bundle.ids()),None),bundle,t.context(None))


async def h_priorities(t: Turn,plan,client_id):
    """Follow-ups across the book (RM11). Code ranks her requests, the meeting requests to answer (the Calendar's
    requests_to_answer), review deadlines, meetings in the next 7 days, tasks and milestones (services/rm/priority.py);
    the other follow-ups (specialist tickets, commitments, later dates) stay as unscored follow-up records."""
    await t.say('Reading deadlines, tickets, tasks and meetings across your book…')
    tickets,followups=await t.read('rm_tickets',{}),await t.read('rm_followups',{})
    appointments=await t.read('rm_appointments',{'start':today().isoformat(),'end':(today()+timedelta(days=30)).isoformat()})
    rows=followups['data']
    items=priority.rank(today=today(),now=datetime.now(SGT),tickets=tickets['data'],requests=fed(t,'requests_to_answer') or [],
        reviews=priority.reviews_due([f for f in rows if f.get('kind')=='review_deadline'],today()+timedelta(days=priority.DEADLINE_DAYS)),
        meetings=priority.meetings_next_7d(appointments['data'],today()),followups=rows)
    t.facts.update(priority.facts(items))
    ranked={x['key'] for x in items}  # every ranked record, shown or not, stays out of the unscored follow-ups
    others=[f for f in rows if priority.key_of(f) not in ranked]
    t.facts.update(ranked_shown=min(len(items),RANKED_SHOWN),not_shown=max(len(items)-RANKED_SHOWN,0),followups_not_shown=max(len(others)-FOLLOWUPS_SHOWN,0))
    bundle=ev.ranked_bundle(items[:RANKED_SHOWN],confirmed_meetings(rows),str(followups.get('as_of')))
    bundle=ev.followups_bundle({**followups,'data':others[:FOLLOWUPS_SHOWN]},appointments,bundle)
    reply=brief_reply('priorities',await t.model('priorities',bundle,brief_schema(bundle.ids()),None),bundle,t.context(None),True)
    return with_rest({**reply,'sections':in_rank_order(reply['sections'],bundle,PRIORITY_SECTIONS)},not_listed(items[RANKED_SHOWN:],others[FOLLOWUPS_SHOWN:]))


TODAY_NONE={'meeting_request':('meeting-requests-none','Meeting requests to answer','requests_to_answer'),
    'request':('requests-none','Client requests waiting for you',None),'meeting':('meetings-none','Meetings in the next 7 days','meetings_next_7d'),
    'review':('reviews-none','Compliance reviews due within 14 days','reviews_due'),'task':('tasks-none','Your tasks due within 14 days',None)}


async def h_today(t: Turn,plan,client_id):
    """Build plan 3b: her day in one answer. Her requests and tasks are read here; the meetings in the next 7 days and the
    meeting requests to answer come from the Calendar, the reviews due from the Client (their exports, passed on by
    orchestrator.gather). Code ranks them all (services/rm/priority.py); the model explains each record's score parts in
    the task file's sections, and code puts the items back in rank order. A category with nothing in it, one whose
    records were not passed on, or one whose records all ranked below the top RANKED_SHOWN is a record of its own, so
    every section the task file asks for has something true to say instead of being left out or guessed."""
    await t.say('Putting your day in order…')
    tickets,followups=await t.read('rm_tickets',{}),await t.read('rm_followups',{})
    requests,meetings,reviews=fed(t,'requests_to_answer'),fed(t,'meetings_next_7d'),fed(t,'reviews_due')
    items=priority.rank(today=today(),now=datetime.now(SGT),tickets=tickets['data'],requests=requests or [],reviews=reviews or [],
        meetings=meetings or [],followups=followups['data'])
    t.facts.update(priority.facts(items),ranked_shown=min(len(items),RANKED_SHOWN),not_shown=max(len(items)-RANKED_SHOWN,0))
    as_of=str(tickets.get('as_of'))
    shown,rest=items[:RANKED_SHOWN],items[RANKED_SHOWN:]
    bundle=ev.ranked_bundle(shown,confirmed_meetings(followups['data']),as_of)
    for kind,(source_id,title,name) in TODAY_NONE.items():
        lower=sum(x['kind']==kind for x in rest)
        if name and fed(t,name) is None:
            bundle.add(ev.Evidence(source_id.replace('-none','-not-read'),title,'record',{'read':False,'meaning':'Not read for this answer, so say it was not checked.'},as_of))
        elif lower and not any(x['kind']==kind for x in shown):  # all ranked lower: never "None recorded", never guessed
            bundle.add(ev.Evidence(source_id.replace('-none','-ranked-lower'),title,'record',{'count':lower,'listed_here':0,
                'meaning':'Recorded but ranked below the items listed here, so not listed in this answer; say so in one item citing this record.'},as_of))
        elif not lower and not any(x['kind']==kind for x in shown):bundle.add(ev.Evidence(source_id,title,'record',{'count':0,'meaning':'None recorded.'},as_of))
    reply=brief_reply('today',await t.model('today',bundle,brief_schema(bundle.ids()),None),bundle,t.context(None),True)
    return with_rest({**reply,'sections':in_rank_order(reply['sections'],bundle,TODAY_SECTIONS)},not_listed(rest))


async def h_reviews(t: Turn,plan,client_id):
    await t.say('Reading compliance reviews across your book…')
    bundle=ev.reviews_bundle(await t.read('rm_review_status',{}))
    return brief_reply('reviews',await t.model('reviews',bundle,brief_schema(bundle.ids()),None),bundle,t.context(None))


async def h_schedule(t: Turn,plan,client_id):
    await t.say('Reading your appointments…')
    rows=await t.read('rm_appointments',{'start':today().isoformat(),'end':(today()+timedelta(days=30)).isoformat()})
    c=t.req.context
    selected=next((r for r in rows['data'] if c.appointment_id and str(r['appointment_id'])==str(c.appointment_id)),None)
    hint=plan.get('appointment_hint') or ''
    if narrows(hint) and not t.req.intent:
        matches=match_appointments(rows['data'],hint,client_id)
        if len(matches)==1:selected=matches[0]
        elif len(matches)>1:
            return clarify('Which appointment do you mean?',[{'label':f"{m['client']} · {m['when']}",'appointment_id':str(m['appointment_id']),
                'client_id':m['customer_id']} for m in matches],'schedule',t.context(client_id))
    bundle=ev.Bundle()
    bundle.add(ev.appointments_evidence(rows,client_id))
    if selected:bundle.add(ev.Evidence('selected-appointment',f"Selected appointment: {selected['client']}, {selected['when']}",'calendar',
        {k:v for k,v in selected.items() if k not in ('starts_at','ends_at','customer_id')},str(rows.get('as_of')),selected['customer_id']))
    ctx=t.context(selected['customer_id'] if selected else client_id,appointment_id=str(selected['appointment_id']) if selected else None)
    return brief_reply('schedule',await t.model('schedule',bundle,brief_schema(bundle.ids()),client_id),bundle,ctx)


def fixed(intent: str,tool: str) -> dict:
    """A consent-gated read's fixed arguments (its purpose) from the Client spec: set by the intent, never by a model."""
    return dict(next(r.fixed for r in registry.RM_CLIENT.intents[intent] if r.tool==tool))


ASK_LABELS={'transaction_insights':'Transaction insights','chat_topics':'Topics from her chats'}


def ask_cards(reads: list[dict],client_id: int,client: str|None,meeting: dict|None) -> list[dict]:
    """"Ask the client to share" (consent v2, 0113): one card per gated category a read found she has not shared, built
    from the consent gaps (no model). The RM approves it through POST /rm/consent-requests; the assistant never asks."""
    gaps=dict.fromkeys(r.get('category') for r in reads if isinstance(r,dict) and r.get('shared') is False and r.get('category') in ASK_LABELS)
    return [{'customer_id':client_id,'client':client,'category':c,'category_label':ASK_LABELS[c],'reason':'meeting_prep' if meeting else 'review',
        'meeting_id':str(meeting['appointment_id']) if meeting else None,'meeting_when':meeting.get('when') if meeting else None} for c in gaps]


def with_asks(reply: dict,cards: list[dict]) -> dict:
    return {**reply,'consent_asks':cards} if cards else reply


OVERVIEW=re.compile(r"\b(?:overview|snapshot|360|summary|summari[sz]e|rundown|run-down|background|profile|tell me about|catch me up)\b",re.I)
HISTORY=re.compile(r"\b(?:history|summary|summari[sz]e|past|previous|all (?:our|the|my) (?:meetings|notes)|meeting notes|notes|recap)\b",re.I)
FILLER={'give','now','show','overview','about','tell','what','meetings','meeting','notes','please','quick','brief','full'}


def wants_report(t: Turn,intent: str,words: re.Pattern) -> bool:
    """A whole report (code's record sections and the model's points) or a direct question, which the model answers in
    one short section from the full records. A workspace button is a report; so is report wording without a question
    mark, and a message that names nothing but the client ("Now Mei Ling"). R11: "When is her review due?" is a question."""
    if t.req.intent==intent:return True
    if '?' in t.message:return False
    names={p for c in t.book for p in name_parts(c['name'])}
    return bool(words.search(t.message)) or not keywords(t.message)-GENERIC-FILLER-names


async def h_overview(t: Turn,plan,client_id):
    """RM2. An overview: code writes the record sections (brief_facts.overview_sections) and the model only the summary
    and up to 3 points worth raising. A question about the client: the model answers it from the full records."""
    await t.say(f'Reading {t.name(client_id)}’s records…')
    r=await client_reads(t,'client_overview',client_id,notes=False)
    bundle,_,_=report_bundle(r,client_id)
    reads,ctx,name=[r['cash'],r['insights'],r['points']],t.context(client_id),t.name(client_id)
    if not wants_report(t,'client_overview',OVERVIEW):
        reply=brief_reply('client_overview',await t.model('client_overview',bundle,brief_schema(bundle.ids()),client_id,task='question'),bundle,ctx)
        return with_asks(reply,ask_cards(reads,client_id,name,None))
    data=r['profile']['data']
    records=brief_facts.overview_sections(data,bundle)
    await t.show(brief_facts.in_order(records,brief_facts.OVERVIEW_ORDER),bundle)
    facts=brief_facts.facts(bundle,data.get('interactions') or [])
    points=await t.model('client_overview',facts,brief_facts.points_schema(facts.ids(),brief_facts.OVERVIEW_POINTS,'rm_overview_points'),client_id)
    reply=await split_reply(t,'client_overview',points,facts,bundle,records,brief_facts.OVERVIEW_ORDER,ctx,f'Overview of {name} from your records.',
        brief_facts.OVERVIEW_POINTS)
    return with_asks(reply,ask_cards(reads,client_id,name,None))


async def _notes(t: Turn,client_id: int,bundle: ev.Bundle):
    notes=await t.read('rm_meeting_notes',{'customer_id':client_id})
    ev.add_notes(bundle,notes['data']['notes'],str(notes.get('as_of')),client_id,notes['data']['withheld_private_notes'])
    if not notes['data']['notes']:bundle.add(ev.Evidence('notes-none','Meeting notes','record',{'notes_recorded':0},str(notes.get('as_of')),client_id))
    return notes['data']['notes']


async def h_history(t: Turn,plan,client_id):
    """RM3. A summary of past meetings: code writes one section per note (brief_facts.history_sections) and the model
    only the summary and up to 3 gaps (unclear or incomplete notes). A question about a meeting: the model answers it
    from the notes (rm_assistant meetings.md), asking which meeting when several could match."""
    await t.say(f'Reading meeting notes for {t.name(client_id)}…')
    profile,read=await asyncio.gather(t.read('rm_client_profile',{'customer_id':client_id}),t.read('rm_meeting_notes',{'customer_id':client_id}))
    bundle=ev.Bundle()
    bundle.add(ev.client_bundle({'data':{**profile['data'],'interactions':[]},'as_of':profile['as_of']}).get('profile'))
    notes,withheld=read['data']['notes'],read['data'].get('withheld_private_notes') or 0
    ev.add_notes(bundle,notes,str(read.get('as_of')),client_id,withheld)
    if not notes:bundle.add(ev.Evidence('notes-none','Meeting notes','record',{'notes_recorded':0},str(read.get('as_of')),client_id))
    ctx,name=t.context(client_id),t.name(client_id)
    if not wants_report(t,'meeting_history',HISTORY):
        return brief_reply('meeting_history',await t.model('meeting_history',bundle,brief_schema(bundle.ids()),client_id),bundle,ctx)
    records=brief_facts.history_sections(notes,withheld)
    order=[s['heading'] for s in records]+['Gaps to check']
    if not notes:
        return {'status':'ok','intent':'meeting_history','summary':f'No meeting notes are recorded for {name}.','sections':records,'clarification':None,
            'context':ctx,'sources':_sources(bundle,['notes-none','notes-withheld']),'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'records'}
    await t.show(records,bundle)
    facts=brief_facts.facts(bundle,notes,brief_facts.HISTORY_SHOWN)
    data=await t.model('meeting_history',facts,brief_facts.points_schema(facts.ids(),brief_facts.HISTORY_POINTS,'rm_history_points'),client_id,task='history')
    return await split_reply(t,'meeting_history',data,facts,bundle,records,order,ctx,
        f"{brief_facts.plural(len(notes),'meeting note')} for {name}, newest first.",brief_facts.HISTORY_POINTS)


async def split_reply(t: Turn,intent: str,data: dict,facts: ev.Bundle,full: ev.Bundle,records: list[dict],order: list[str],context: dict,
    fallback: str,keys: tuple=brief_facts.BRIEF_POINTS) -> dict:
    """A client report whose record sections code wrote (records) and whose judgement points the model wrote (data). The
    model's points are checked against the facts it saw; every citation is shown from the full records."""
    report=checks.Report()
    points=checks.check_sections(brief_facts.point_sections(data,keys),facts,report,True)
    await t.show(points,full)
    summary=checks.check_summary(data.get('summary',''),facts,report) or fallback
    sections=brief_facts.in_order([*records,*points],order)
    return {'status':'ok','intent':intent,'summary':summary,'sections':sections,'clarification':None,'context':context,
        'sources':_sources(full,[i for s in sections for x in s['items'] for i in x['source_ids']]),'validation':report.as_dict(),'origin':'ai'}


async def client_reads(t: Turn,intent: str,client_id: int,notes: bool=True,shared: bool=False) -> dict[str,dict]:
    """A client report's reads, all at once (docs/planning/database-speed.md, option 2): the profile, her notes, the
    handover note (consent is checked in SQL first) and the three consent-gated reads with the intent's fixed purpose."""
    reads={'profile':t.read('rm_client_profile',{'customer_id':client_id}),'cash':t.read('rm_cash_events',{'customer_id':client_id}),
        'insights':t.read('rm_transaction_insights',{'customer_id':client_id,**fixed(intent,'rm_transaction_insights')}),
        'points':t.read('rm_talking_points',{'customer_id':client_id,**fixed(intent,'rm_talking_points')})}
    if notes:reads['notes']=t.read('rm_meeting_notes',{'customer_id':client_id})
    if shared:reads['shared']=t.read('rm_shared_summary',{'customer_id':client_id})
    return dict(zip(reads,await asyncio.gather(*reads.values())))


def report_bundle(r: dict[str,dict],client_id: int) -> tuple[ev.Bundle,list[dict],int]:
    """The full records of a client report (shown as its sources) and her notes, newest first."""
    profile=r['profile']
    # with its own notes read, the profile's last 5 notes (interactions) are left out; without one, they are the notes
    bundle=ev.client_bundle({'data':{**profile['data'],**({'interactions':[]} if 'notes' in r else {})},'as_of':profile['as_of']})
    notes,withheld=[],0
    if 'notes' in r:
        notes,withheld=r['notes']['data']['notes'],r['notes']['data'].get('withheld_private_notes') or 0
        ev.add_notes(bundle,notes,str(r['notes'].get('as_of')),client_id,withheld)
        if not notes:bundle.add(ev.Evidence('notes-none','Meeting notes','record',{'notes_recorded':0},str(r['notes'].get('as_of')),client_id))
    if 'shared' in r:bundle.add(ev.shared_evidence(r['shared'],client_id))
    bundle.add(ev.cash_evidence(r['cash'],client_id))
    bundle.add(ev.insights_evidence(r['insights'],client_id))
    bundle.add(ev.talking_points_evidence(r['points'],client_id))
    return bundle,notes,withheld


async def h_brief(t: Turn,plan,client_id):
    from uuid import UUID
    from app.core.config import settings
    from app.services.meetings import preparation
    from app.services.meetings.assistant import reply as saved_brief_reply
    from app.services.meetings.database import MeetingStoreError
    await t.say('Opening the saved meeting brief…')
    c=t.req.context
    profile=await t.read('rm_client_profile',{'customer_id':client_id})
    upcoming=[a for a in profile['data']['appointments'] if a['status']=='confirmed']
    chosen=next((a for a in upcoming if c.appointment_id and str(a['appointment_id'])==str(c.appointment_id)),None)
    if c.appointment_id and chosen is None:
        return clarify('The selected meeting is not in this client’s current confirmed appointments. Choose the meeting to prepare.',
            [{'label':a['when'],'appointment_id':str(a['appointment_id']),'client_id':client_id} for a in upcoming],
            'meeting_brief',t.context(client_id,appointment_id=str(c.appointment_id)))
    if not chosen and narrows(plan.get('appointment_hint') or '') and not t.req.intent:
        rows=[{**a,'customer_id':client_id} for a in upcoming]
        matches=match_appointments(rows,plan['appointment_hint'],client_id)
        if len(matches)>1:
            return clarify('Which meeting should I prepare for?',[{'label':m['when'],'appointment_id':str(m['appointment_id']),'client_id':client_id} for m in matches],
                'meeting_brief',t.context(client_id))
        chosen=matches[0] if matches else None
    if not chosen and len(upcoming)==1:chosen=upcoming[0]
    ctx=t.context(client_id,appointment_id=str(chosen['appointment_id']) if chosen else None)
    if chosen is None:
        return clarify('Choose the confirmed meeting to prepare. Schedule a meeting first if none is listed.',
            [{'label':a['when'],'appointment_id':str(a['appointment_id']),'client_id':client_id} for a in upcoming],'meeting_brief',ctx)
    if not settings.meetings_enabled:
        return clarify('Meeting preparation is disabled. Enable the meeting lifecycle before preparing this meeting.',[],'meeting_brief',ctx)
    # Keep the existing latest-note export for dependent follow-up drafts. The brief itself has one canonical writer.
    await t.read('rm_meeting_notes',{'customer_id':client_id})
    try:prepared=await preparation.prepare(UUID(str(chosen['appointment_id'])),t.base)
    except MeetingStoreError as error:
        if error.code!='P0002':raise
        return {**clarify('Open this meeting workspace and agree its intended outcome before preparing the saved brief.',[],
            'meeting_brief',ctx),'meeting_workspace':str(chosen['appointment_id'])}
    reply=saved_brief_reply(prepared,ctx)
    bundle=ev.Bundle()
    for source in reply['sources']:
        bundle.add(ev.Evidence(source['id'],source['title'],'record',source['record'],source['as_of'],client_id))
    await t.show(reply['sections'],bundle)
    sharing=[{'category':category,'shared':prepared.card.consent[category]} for category in ASK_LABELS]
    return with_asks(reply,ask_cards(sharing,client_id,t.name(client_id),chosen))


async def _ticket_ref(t: Turn,plan,client_id,profile) -> tuple[str|None,dict|None]:
    c=t.req.context
    ref=(plan.get('ticket_ref') or '').upper().strip()
    if re.fullmatch(r'TCK-\d{6,}',ref):return ref,None
    open_tickets=profile['data']['tickets']
    if c.draft and c.draft.customer_id==client_id and c.draft.reference and plan['intent']=='draft_message':return c.draft.reference,None
    if c.ticket_ref and c.client_id==client_id and any(x['reference']==c.ticket_ref for x in open_tickets):
        chosen,_=pick_ticket(t.message,open_tickets)
        if chosen is None or chosen==c.ticket_ref or not keywords(t.message)&keywords(' '.join(x.get('category_label','') for x in open_tickets)):return c.ticket_ref,None
        return chosen,None
    if not open_tickets:return None,None
    chosen,candidates=pick_ticket(t.message,open_tickets)
    if chosen:return chosen,None
    # Topic words are the message's keywords without the client's name. None ("summarise her request") points at a ticket:
    # her only one, or ask which. Topic words that match no ticket mean the question is about something else, so it is
    # answered from her client record, which has her cards and profile (R08: "approve Michelle's limit increase").
    topic=keywords(t.message)-GENERIC-set(name_parts(t.name(client_id) or ''))
    if not topic and len(candidates)==1:return candidates[0]['reference'],None
    if topic and not any(topic&ticket_words(x) for x in candidates):return None,None
    return None,clarify('Which request do you mean?',[{'label':f"{x['reference']} · {x['category_label']}",'ticket_ref':x['reference'],'client_id':client_id}
        for x in candidates],plan['intent'],t.context(client_id))


async def h_ticket(t: Turn,plan,client_id):
    profile=await t.read('rm_client_profile',{'customer_id':client_id})
    ref,stop=await _ticket_ref(t,plan,client_id,profile)
    if stop:return stop
    if ref is None:
        bundle=ev.client_bundle(profile)
        return brief_reply('ticket_summary',await t.model('ticket_summary',bundle,brief_schema(bundle.ids()),client_id),bundle,t.context(client_id))
    await t.say(f'Reading {ref} and what the client was told…')
    detail=await t.read('rm_ticket',{'reference':ref})
    bundle=ev.ticket_detail_bundle(detail,await ev.category_guidance(detail,t.read))
    return brief_reply('ticket_summary',await t.model('ticket_summary',bundle,brief_schema(bundle.ids()),client_id),bundle,t.context(client_id,ticket_ref=ref))


async def h_draft(t: Turn,plan,client_id):
    c=t.req.context
    profile=await t.read('rm_client_profile',{'customer_id':client_id})
    recap=bool(c.note_id and RECAP.search(t.message)) and not (plan.get('ticket_ref') or '').strip()
    # One draft per client of a fan-out (R21) is a new message about that client's record, never a reply to a ticket; draft_scope
    # (rm_planner v3+) says whether a single draft replies to a ticket.
    ref,stop=await _ticket_ref(t,plan,client_id,profile) if not recap and not plan.get('fan_out') and plan.get('draft_scope')=='ticket' else (None,None)
    if stop:return stop
    internal: list[str]=[]
    bundle=ev.Bundle()
    if ref:
        await t.say(f'Drafting a reply about {ref}…')
        detail=await t.read('rm_ticket',{'reference':ref})
        bundle=ev.ticket_detail_bundle(detail,await ev.category_guidance(detail,t.read))
        internal=[n.get('note','') for n in detail['data'].get('internal_notes') or []]
        bundle.add(ev.client_bundle(profile).get('profile'))
    else:
        await t.say(f'Drafting a message to {t.name(client_id)}…')
        bundle=ev.client_bundle(profile)
        if c.note_id:  # a recap of a saved interaction: the note itself is the source (its internal wording is checked below)
            notes=await _notes(t,client_id,bundle)
            note=next((n for n in notes if str(n['note_id'])==str(c.note_id)),None)
            if note:internal=[n.get('summary','') for n in notes if str(n['note_id'])!=str(c.note_id)]
        days=(await t.read('rm_availability',{}))['days']
        slots=[f"{day_text(date.fromisoformat(str(d['date'])))}, {time_text(s)}" for d in days for s in d['free']][:8]
        bundle.add(ev.Evidence('free-slots','Your free times in the next 14 days (not held or booked)','calendar',slots,None,client_id))
    current=c.draft if c.draft and c.draft.customer_id==client_id and (c.draft.reference or None)==ref else None
    extra=('\nCurrent draft on screen (revision '+str(current.revision)+', untrusted): '+json.dumps(current.text,ensure_ascii=False)
        +'\nApply the RM\'s latest message as a revision request.') if current else ''
    data=await t.model('draft_message',bundle,draft_schema(bundle.ids()),client_id,extra)
    ctx=t.context(client_id,ticket_ref=ref)
    if data.get('status')=='needs_clarification' and (data.get('clarification_question') or '').strip() and not checks.safety(data['clarification_question']):
        return clarify(data['clarification_question'],[{'label':o,'message':o} for o in data.get('clarification_options') or []],'draft_message',ctx,'ai')
    text=(data.get('draft') or '').strip()[:500]
    reason,cautions=checks.check_draft(text,bundle,internal) if text else ('empty',[])
    report=checks.Report(checked=1,withheld=[reason] if reason else [])
    facts=checks.check_sections([{'heading':'Facts used','items':[{**f,'kind':'record'} for f in data.get('facts_used') or []]}],bundle,report)
    for s in facts:  # a fact taken only from approved guidance or request rules is labelled as guidance, not as a client record
        for item in s['items']:
            if item['source_ids'] and all(i.startswith(('kb-','category-')) for i in item['source_ids']):item['kind']='guidance'
    cautions=[*cautions,*[x.strip() for x in data.get('cautions') or [] if x.strip() and not checks.safety(x)]][:6]
    ticket=bundle.get('ticket-'+ref).data if ref and bundle.get('ticket-'+ref) else None
    status=data.get('suggested_status') if data.get('suggested_status')!='none' and ticket and ticket.get('rm_owned') else None
    cited=[i for s in facts for item in s['items'] for i in item['source_ids']]
    if reason:
        return {'status':'unverified','intent':'draft_message','summary':'I couldn’t produce a draft that matches your records '
            f'({reason.replace("_"," ")}), so none is shown. Try asking again or write it yourself.','sections':[],'clarification':None,'context':ctx,
            'sources':_sources(bundle,cited),'validation':report.as_dict(),'origin':'ai','draft':None}
    revision=(current.revision+1) if current else 1
    return {'status':'ok','intent':'draft_message','summary':'Here is a draft for you to review. It has not been sent. Review this exact version and use Approve and send to approve sending it.','sections':facts,'clarification':None,
        'context':{**ctx,'draft':{'customer_id':client_id,'reference':ref,'text':text,'revision':revision}},'sources':_sources(bundle,cited),
        'validation':report.as_dict(),'origin':'ai','draft':{'customer_id':client_id,'client':t.name(client_id),'reference':ref,'text':text,'revision':revision,
        'cautions':cautions,'suggested_status':status,'can_change_status':bool(ticket and ticket.get('rm_owned')),
        'restricted':bool(ticket and ticket.get('restricted') and not ticket.get('rm_owned'))}}


async def _categories(t: Turn) -> list[dict]:
    rows=await customer_data.get_categories(t.ctx)
    return [r.model_dump(mode='json') for r in rows if r.customer_raisable]


def documentation_reply(t: Turn,data: dict,bundle: ev.Bundle,sources: list[str],client_id: int,categories: list[dict],profile: dict,
    state: dict,intent: str,open_tasks: list[dict]) -> dict:
    """RM5/RM6/RM10 checks: quotes from the notes, figures and dates from the notes or records, request types and fields,
    missing details and duplicates. The result is a proposal; nothing is saved until the RM approves it."""
    report=checks.Report()
    ctx=t.context(client_id)
    if data.get('status')=='needs_clarification' and (data.get('clarification_question') or '').strip() and not checks.safety(data['clarification_question']):
        return {**clarify(data['clarification_question'],[{'label':o,'message':o} for o in data.get('clarification_options') or []],intent,ctx,'ai'),
            'context':{**ctx,'documentation':state}}
    source_text='\n'.join(sources)
    support=ev.Bundle([ev.Evidence('rm-notes','Your notes','note',source_text),*bundle.items])
    def ok(text: str) -> bool:
        reason=checks.safety(text) or (checks.grounded(text,['rm-notes'],support)[0] if checks.has_facts(text) else None)
        if reason:report.withheld.append(reason)
        return reason is None
    items=[]
    for item in data.get('items') or []:
        report.checked+=1
        if (item.get('text') or '').strip() and ok(item['text']):items.append({**item,'text':item['text'].strip()})
    gaps=[g.strip() for g in data.get('gaps') or [] if g.strip() and ok(g)]
    by_cat={c['category']:c for c in categories}
    tickets=profile['data']['tickets']
    actions=[]
    for action in data.get('actions') or []:
        report.checked+=1
        if not checks.quoted(action.get('evidence_quote',''),sources):report.withheld.append('not_in_notes');continue
        if not ok(action.get('title','')+' '+action.get('details','')):continue
        a={k:(v.strip() if isinstance(v,str) else v) for k,v in action.items()}
        flags=[]
        due=a.get('due') or ''
        try:a['due_on']=date.fromisoformat(due).isoformat() if due else None
        except ValueError:a['due_on']=None;flags.append(f'Due date “{due}” needs a calendar date')
        if a['kind']=='follow_up_task' and not a.get('owner'):flags.append('No owner stated in the notes')
        if a['kind']=='follow_up_task' and not due:flags.append('No due date stated in the notes')
        if a['kind']=='follow_up_task':
            dup=next((x for x in open_tasks if checks.similar(x['title'],a.get('title',''))),None)
            if dup:a['duplicate_of']={'task_id':dup['task_id'],'title':dup['title']};flags.append(f"Similar open task already exists: “{dup['title']}”")
        if a['kind']=='service_request':
            cat=by_cat.get(a.get('category') or '')
            if not cat:a['kind']='follow_up_task';flags.append('No matching request type; proposed as a task instead')
            else:
                keys={f['key'] for f in cat['fields']}
                values={f['key']:f['value'] for f in a.get('fields') or [] if f.get('key') in keys and (f.get('value') or '').strip() and ok(f['value'])}
                a['details']={k:v for k,v in values.items()}
                a['request']={'category':cat['category'],'label':cat['label'],'owner_team':cat['owner_team'],'decided_by':cat['decided_by'],
                    'fields':cat['fields'],'documents':cat['documents']}
                a['missing']=[f['label'] for f in cat['fields'] if f.get('required') and f['key'] not in values]
                if a['missing']:flags.append('Missing: '+', '.join(a['missing']))
                dup=next((x for x in tickets if x['category']==cat['category']),None)
                if dup:a['duplicate_of']={'reference':dup['reference'],'status':dup['status']};flags.append(f"An open {cat['label'].lower()} request already exists: {dup['reference']}")
        if a['kind']=='meeting' and profile['data']['appointments']:
            first=profile['data']['appointments'][0]
            flags.append(f"A meeting is already {'confirmed' if first['status']=='confirmed' else 'held'} for {first['when']}")
        a['flags']=flags
        a.pop('fields',None)
        a['id']=f"action-{len(actions)+1}"
        actions.append(a)
    summary=(data.get('summary') or '').strip()
    if summary and not ok(summary):summary=''
    interaction=data.get('interaction') or {}
    met=interaction.get('met_on') or ''
    try:met=date.fromisoformat(met).isoformat() if met else ''
    except ValueError:met=''
    draft={'summary':summary,'interaction':{**interaction,'met_on':met},'items':items,'gaps':gaps,'actions':actions}
    state={**state,'draft':draft}
    return {'status':'ok','intent':intent,'summary':'Review the documentation and proposed actions. Nothing is saved or submitted until you approve it.' if intent=='document_interaction'
            else 'Proposed follow-ups from the selected note. Nothing is created until you confirm each one.',
        'sections':[],'clarification':None,'context':{**ctx,'documentation':state,'note_id':state.get('note_id')},'sources':_sources(support,['rm-notes']),
        'validation':report.as_dict(),'origin':'ai','documentation':{**state,'client':t.name(client_id),'from_note':intent=='actions_from_note'}}


async def h_document(t: Turn,plan,client_id):
    c=t.req.context
    doc=c.documentation if c.documentation and c.documentation.customer_id==client_id else None
    if doc and not plan.get('is_new_notes'):
        state={'customer_id':client_id,'source_notes':doc.source_notes,'corrections':[*doc.corrections,t.message][-20:],'revision':doc.revision+1,
            'note_id':str(doc.note_id) if doc.note_id else None}
        previous=doc.draft
    else:
        state={'customer_id':client_id,'source_notes':t.message,'corrections':[],'revision':1,'note_id':None}
        previous=None
    meeting=None
    wanted=str(c.appointment_id or (doc.appointment_id if doc and doc.appointment_id else '') or '')
    if wanted:  # "Document this meeting": the selected appointment gives the date and format; SQL links the note on approval
        rows=(await t.read('rm_appointments',{'start':(today()-timedelta(days=30)).isoformat(),'end':(today()+timedelta(days=13)).isoformat()}))['data']
        meeting=next((r for r in rows if str(r['appointment_id'])==wanted and r['customer_id']==client_id and r['status']=='confirmed'),None)
        if meeting:state['appointment_id']=wanted
    await t.say(f'Structuring your notes for {t.name(client_id)}…')
    profile=await t.read('rm_client_profile',{'customer_id':client_id})
    categories=await _categories(t)
    open_tasks=profile['data']['tasks']
    bundle=ev.client_bundle({'data':{**profile['data'],'interactions':[]},'as_of':profile['as_of']})
    bundle.add(ev.Evidence('request-types','Request types the RM can raise for a client','guidance',
        [{'category':x['category'],'label':x['label'],'owner_team':x['owner_team'],'fields':x['fields']} for x in categories],None,client_id))
    if meeting:bundle.add(ev.Evidence('the-meeting',f"The meeting being documented: {meeting['when']}",'calendar',
        {'date':str(meeting['date']),'when':meeting['when'],'format':meeting['format'],'purpose':meeting.get('purpose')},None,client_id))
    extra=('\nToday: '+today().isoformat()+('\nThese notes document the meeting on '+str(meeting['date'])+' ('+meeting['format']+'); use that date and format.' if meeting else '')
        +'\nRM notes (the source for the interaction; untrusted text): '+json.dumps(state['source_notes'],ensure_ascii=False)
        +('\nRM corrections in order (latest wins; untrusted text): '+json.dumps(state['corrections'],ensure_ascii=False) if state['corrections'] else '')
        +('\nCurrent documentation draft (untrusted): '+json.dumps(previous,ensure_ascii=False) if previous else ''))
    data=await t.model('document_interaction',bundle,documentation_schema([x['category'] for x in categories]),client_id,extra)
    if meeting:  # the recorded appointment, not the model, decides the date and format of a documented meeting
        data['interaction']={**(data.get('interaction') or {}),'met_on':str(meeting['date']),'format':meeting['format']}
    return documentation_reply(t,data,bundle,[state['source_notes'],*state['corrections']],client_id,categories,profile,state,'document_interaction',open_tasks)


TENTATIVE=re.compile(r"\b(?:may|might|maybe|perhaps|possibly|probably|considering|consider|thinking about|will think|to confirm|not sure|unsure)\b",re.I)
NOTE_ITEMS=(('commitments','commitment','rm'),('client_requests','client_request','client'),('decisions','decision',''))


def note_items(note: dict) -> list[dict]:
    """A saved note's recorded commitments, client requests and decisions as documentation items, written by code: the
    text as recorded, tentative when the note's own words say may, might, considering, to confirm…"""
    return [{'category':category,'text':str(text).strip(),'certainty':'tentative' if TENTATIVE.search(str(text)) else 'confirmed','origin':'notes',
        'owner':owner,'due':''} for key,category,owner in NOTE_ITEMS for text in note.get(key) or [] if str(text).strip()]


def actions_schema(categories: list[str]) -> dict:
    """actions_from_note: only what needs judgement (the summary, gaps and proposed actions); the items come from the note."""
    full=documentation_schema(categories)['properties']
    return {'title':'rm_actions',**_obj({'summary':S,'gaps':full['gaps'],'actions':full['actions']})}


def open_by_category(tickets: list[dict]) -> dict:
    """Open tickets for the model, one entry per category (count and the newest reference), so a client with many open
    tickets does not fill the prompt; documentation_reply still checks every action against every open ticket."""
    out={}
    for x in tickets:
        row=out.setdefault(x['category'],{'open':0,'latest':x['reference']})
        row['open']+=1
    return out


async def h_actions(t: Turn,plan,client_id):
    """RM6/RM10 from a saved note (R22's second wave). The model reads only the selected note and the request types and
    writes only the proposed actions; code fills the note's items and the interaction from the note, and
    documentation_reply checks quotes, fields, owners, due dates and duplicates against the full records."""
    c=t.req.context
    profile,notes_read,categories=await asyncio.gather(t.read('rm_client_profile',{'customer_id':client_id}),
        t.read('rm_meeting_notes',{'customer_id':client_id}),_categories(t))
    bundle=ev.client_bundle({'data':{**profile['data'],'interactions':[]},'as_of':profile['as_of']})
    notes=notes_read['data']['notes']
    ev.add_notes(bundle,notes,str(notes_read.get('as_of')),client_id,notes_read['data'].get('withheld_private_notes') or 0)
    wanted=str(c.note_id or (c.documentation.note_id if c.documentation and c.documentation.note_id else '') or '')
    note=next((n for n in notes if str(n['note_id'])==wanted),None)
    if not note:
        mentioned=checks.dates(t.message)
        hits=[n for n in notes if any((y in (None,int(str(n['met_on'])[:4]))) and (m,d)==(int(str(n['met_on'])[5:7]),int(str(n['met_on'])[8:10])) for y,m,d in mentioned)]
        note=hits[0] if len(hits)==1 else None
        if not note:
            recent=[n for n in notes if n.get('written_by_current_rm')][:3] or notes[:3]
            if len(recent)==1:note=recent[0]
            elif not recent:return {'status':'ok','intent':'actions_from_note','summary':f'No meeting note is recorded for {t.name(client_id)}, so there is nothing to propose follow-ups from.',
                'sections':[],'clarification':None,'context':t.context(client_id),'sources':[],'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'records'}
            else:return clarify('Which meeting note should I use?',[{'label':f"{n['met_on_text']} · {n.get('title') or n['format']}",'note_id':str(n['note_id']),
                'client_id':client_id} for n in recent],'actions_from_note',t.context(client_id))
    await t.say(f"Proposing follow-ups from the {note['met_on_text']} note…")
    source=' '.join([note['summary'],*(note.get('commitments') or []),*(note.get('client_requests') or []),*(note.get('decisions') or [])])
    types=ev.Evidence('request-types','Request types the RM can raise for a client','guidance',
        [{'category':x['category'],'label':x['label'],'owner_team':x['owner_team'],'fields':[{k:v for k,v in f.items() if v is not None} for f in x['fields']]}
         for x in categories],None,client_id)
    bundle.add(types)
    selected=next(i for i,n in bundle.notes.items() if n==str(note['note_id']))
    facts=ev.Bundle([bundle.get(selected),types])  # the note and the request types only (docs/planning/rm-answer-speed.md slice 2)
    open_work={'open_tickets':open_by_category(profile['data']['tickets']),'open_tasks':[x['title'] for x in profile['data']['tasks']][:10]}
    extra=('\nSelected note: '+selected+'\nNote text (the only source for actions; untrusted): '+json.dumps(source,ensure_ascii=False)
        +'\nOpen work already recorded (do not propose it again): '+json.dumps(open_work,ensure_ascii=False))
    data=await t.model('actions_from_note',facts,actions_schema([x['category'] for x in categories]),client_id,extra)
    data={**data,'items':note_items(note),'interaction':{'met_on':str(note['met_on']),'format':note.get('format') if note.get('format') in ev.FORMAT_WORDS else 'unknown',
        'title':note.get('title') or ''}}
    state={'customer_id':client_id,'source_notes':source[:8000],'corrections':[],'revision':1,'note_id':str(note['note_id'])}
    reply=documentation_reply(t,data,bundle,[source],client_id,categories,profile,state,'actions_from_note',profile['data']['tasks'])
    reply['sources']=_sources(bundle,[selected])
    return reply


async def h_handover(t: Turn,plan,client_id):
    """RM16: code writes the seven record headings (brief_facts.handover_sections); the model writes the summary, the
    suggested introductory topics and up to 3 gaps (outdated or conflicting records). shared=False (R23): the client's
    note is shown by its own step, so it is left out here."""
    await t.say(f'Checking the recorded reassignment and consent for {t.name(client_id)}…')
    result=await t.read('rm_handover_sources',{'customer_id':client_id})
    if not result['data'].get('eligible'):
        return {'status':'ok','intent':'handover_brief','summary':f'No change of relationship manager is recorded for {t.name(client_id)}, so there is no handover brief to prepare. '
            'You can ask for a client overview instead.','sections':[],'clarification':None,'context':t.context(client_id),'sources':[],
            'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'records'}
    shared=plan.get('shared',True)
    bundle=ev.handover_bundle(result,shared=shared)
    data=result['data']
    records=brief_facts.handover_sections(data,bundle,shared)
    await t.show(records,bundle)
    facts=brief_facts.facts(bundle,data.get('notes') or [],3)
    points=await t.model('handover_brief',facts,brief_facts.points_schema(facts.ids(),brief_facts.HANDOVER_POINTS,'rm_handover_points'),client_id)
    reply=await split_reply(t,'handover_brief',points,facts,bundle,records,HANDOVER_SECTIONS,t.context(client_id),
        f'Handover brief for {t.name(client_id)} from the records you can read.',brief_facts.HANDOVER_POINTS)
    return {**reply,'sections':handover_order(reply['sections'])}


async def h_shared(t: Turn,plan,client_id):
    """R23 (wave 2, only when the handover read found active consent): the handover note the client shared, quoted from
    the record and labelled as the client's own words. No model call; SQL checks the rm_brief consent again."""
    await t.say(f'Reading the handover note {t.name(client_id)} shared with you…')
    item=ev.shared_evidence(await t.read('rm_shared_summary',{'customer_id':client_id}),client_id)
    bundle=ev.Bundle([item])
    report=checks.Report()
    items=[]
    for note in (item.data.get('notes') or []) if item.id=='shared-note' else []:
        report.checked+=1
        text=(note.get('content') or '').strip()
        if reason:=checks.safety(text):report.withheld.append(reason);continue
        items.append({'text':f"{text} (shared {note.get('shared_text')})",'kind':'client_stated','owner':'client','source_ids':['shared-note']})
    name=t.name(client_id)
    if not items:
        return {'status':'ok','intent':'shared_note','summary':f'{name} has not shared a handover note with you under an active consent, so nothing from one is included.',
            'sections':[],'clarification':None,'context':t.context(client_id),'sources':[],'validation':report.as_dict(),'origin':'records'}
    return {'status':'ok','intent':'shared_note','summary':f'The handover note {name} shared with you, in the client’s own words (active consent).',
        'sections':[{'heading':f'Handover note shared by {name}','items':items}],'clarification':None,'context':t.context(client_id),
        'sources':_sources(bundle,['shared-note']),'validation':report.as_dict(),'origin':'records'}


async def h_book(t: Turn,plan,client_id):
    await t.say('Reading a summary of every client in your book…')
    bundle=ev.book_bundle(await t.read('rm_book_facts',{}),t.req.message,today())
    return brief_reply('book_question',await t.model('book_question',bundle,brief_schema(bundle.ids()),None),bundle,t.context(None))


async def h_help(t: Turn,plan,client_id):
    bundle=ev.Bundle()
    return brief_reply('help',await t.model('help',bundle,brief_schema([]),None),bundle,t.context(t.req.context.client_id))


# Calendar agent (build plan 3a) ---------------------------------------------------------------------------------------
# Decisions and availability changes are cards built by code from the records (no model): the RM approves each one
# through the existing routes (POST /rm/meeting-requests/{id}/decide, /rm/availability/blocks, PUT /rm/availability/hours).
DECLINE=re.compile(r"\b(?:decline|reject|turn (?:it |this |her |him )?down|say no|refuse)\b",re.I)
COUNTER=re.compile(r"\b(?:propose|counter|suggest|offer (?:her|him|them)?|instead|another time|different time)\b",re.I)
DECLINE_REASON={'answered_by_message':re.compile(r"\b(?:by message|in a message|reply|answer (?:it|her|him|them) in writing)\b",re.I),
    'redirected':re.compile(r"\b(?:redirect|another team|specialist|right team|pass(?:ed)? (?:it |this )?(?:on )?to)\b",re.I),
    'not_needed':re.compile(r"\b(?:not needed|no need|unnecessary|does(?:n['’]t| not) need)\b",re.I)}
CHANGE_NOTE='Nothing is sent, booked or changed until you approve it on the card.'
PROBLEM_WORDS={'advisor_unavailable':'you are not available','too_soon':'it is less than an hour away or past','too_far':'it is more than 60 days away',
    'outside_hours':'it is outside your working hours','blocked':'you blocked this time','taken':'it is already booked',
    'weekly_limit':'the client already has the most meetings allowed that week'}
RANGE=re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*(?:to|until|till|-|–)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b",re.I)
PARTS={'morning':('07:00','12:00'),'lunch':('12:00','14:00'),'afternoon':('12:00','18:00'),'evening':('17:00','21:00')}
UNBLOCK=re.compile(r"\b(?:unblock|open (?:up |it |them )?(?:again|up)|free up|remove (?:the |my )?blocks?)\b",re.I)
BLOCK=re.compile(r"\b(?:block|blocking|unavailable|not available|busy|away|out of (?:the )?office|leave)\b",re.I)
HOURS=re.compile(r"\b(?:working hours|work(?:ing)? (?:from|hours|days)|hours|start(?:ing)? (?:at|work)|finish(?:ing)? (?:at|work)|stop working|not working|day off)\b",re.I)


def free_times(availability: dict) -> list[dict]:
    """Her free half hours over the next 14 days (what clients can book), as Singapore instants with their words."""
    return [{'starts_at':f"{d['date']}T{hhmm}:00+08:00",'when':f"{day_text(date.fromisoformat(str(d['date'])))}, {time_text(hhmm)}"}
        for d in availability.get('days') or [] for hhmm in d.get('free') or []]


def clock(h: int,m: int,ap: str) -> int:
    """Minutes after midnight; a bare hour from 1 to 7 is an afternoon time ("4:30" in a meeting request is 4:30 pm)."""
    ap=(ap or '').lower()
    if ap:return (h%12+(12 if ap=='pm' else 0))*60+m
    return ((h+12) if 1<=h<=7 else h)*60+m


def times_in(text: str) -> list[int]:
    return [clock(int(m.group(1)),int(m.group(2) or 0),m.group(3)) if m.group(1) else clock(int(m.group(4)),int(m.group(5)),'')
        for m in TIME.finditer(text or '')]


def match_times(rows: list[dict],hint: str) -> list[dict]:
    """Times (dicts with starts_at) that fit a day or time hint ("Monday 4:30", "tomorrow", "12 Nov 10am")."""
    text=(hint or '').lower()
    day=today() if 'today' in text else today()+timedelta(days=1) if 'tomorrow' in text else None
    wd=next((i+1 for i,w in enumerate(WEEKDAYS) if w in text or w[:3]+' ' in text+' '),None)
    on={(m,d) for _,m,d in checks.dates(hint or '')}
    minutes=times_in(hint)
    def fits(r):
        local=datetime.fromisoformat(str(r['starts_at'])).astimezone(SGT)
        return ((day is None or local.date()==day) and (wd is None or local.isoweekday()==wd) and (not on or (local.month,local.day) in on)
            and (not minutes or local.hour*60+local.minute in minutes))
    return [r for r in rows if fits(r)]


def card_reply(intent: str,summary: str,sections: list[dict],bundle: ev.Bundle,context: dict,**card) -> dict:
    cited=[i for s in sections for item in s['items'] for i in item['source_ids']]
    return {'status':'ok','intent':intent,'summary':summary,'sections':sections,'clarification':None,'context':context,
        'sources':_sources(bundle,cited),'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'records',**card}


def no_change(intent: str,summary: str,context: dict) -> dict:
    return {'status':'ok','intent':intent,'summary':summary,'sections':[],'clarification':None,'context':context,'sources':[],
        'validation':{'checked':0,'withheld':0,'reasons':[]},'origin':'records'}


async def h_meeting_requests(t: Turn,plan,client_id):
    """Clients' meeting requests, summarised by the model; each purpose reaches it only as the client's quoted words."""
    status='scheduled' if re.search(r'\b(?:scheduled|accepted|booked)\b',t.message,re.I) else 'closed' if re.search(r'\b(?:declined|withdrawn|closed)\b',t.message,re.I) else 'open'
    await t.say('Reading clients’ meeting requests…')
    result=await t.read('rm_meeting_requests',{'status':status})
    rows=[r for r in result.get('data') or [] if client_id is None or r.get('customer_id')==client_id]
    bundle=ev.meeting_requests_bundle({**result,'data':rows})
    return brief_reply('meeting_requests',await t.model('meeting_requests',bundle,brief_schema(bundle.ids()),client_id),bundle,t.context(client_id))


async def h_decide(t: Turn,plan,client_id):
    """Accept one of the client's times, propose another (held for the client) or decline with a reason: a card from the
    records, approved through the 2a route. Which request and which time are resolved here; anything unclear is asked."""
    await t.say('Reading open meeting requests and your free times…')
    result=await t.read('rm_meeting_requests',{'status':'open'})
    rows=[r for r in result.get('data') or [] if r.get('status') in ('requested','countered') and (client_id is None or r.get('customer_id')==client_id)]
    hint=plan.get('appointment_hint') or t.message
    if len(rows)>1:
        named=[r for r in rows if r.get('created_text') and str(r['created_text']).lower() in t.message.lower()]
        timed=[r for r in rows if narrows(hint) and match_times(r.get('preferred_times') or [],hint)]
        rows=named or timed or rows
    if not rows:
        who=t.name(client_id)
        return no_change('decide_request',f"There is no meeting request {'from '+who+' ' if who else ''}waiting for your answer, so there is nothing to accept, "
            'propose or decline.',t.context(client_id))
    if len(rows)>1:
        same=len({r['customer_id'] for r in rows})==1
        return clarify('Which meeting request do you mean?',[{'label':f"{r['client']} · received {r['created_text']}",
            **({'message':f"{t.message} (the request received {r['created_text']})"} if same else {'client_id':r['customer_id']})} for r in rows],
            'decide_request',t.context(client_id))
    r=rows[0]
    availability=await t.read('rm_availability',{})
    bundle=ev.meeting_requests_bundle({**result,'data':[r]})
    rid=bundle.ids()[0]
    bundle.add(ev.availability_evidence(availability))
    decision='decline' if DECLINE.search(t.message) else 'counter' if COUNTER.search(t.message) else 'accept'
    preferred=r.get('preferred_times') or []
    free=[f for f in free_times(availability) if f['starts_at'] not in {str(x.get('starts_at')) for x in preferred}][:40]
    chosen,note=None,None
    if decision=='accept':
        matches=match_times(preferred,hint) if narrows(hint) else [x for x in preferred if not x.get('problem')]
        if len(matches)==1 and not matches[0].get('problem'):chosen=matches[0]
        elif len(matches)==1:note=f"{matches[0]['when']} cannot be accepted: {PROBLEM_WORDS.get(matches[0]['problem'],matches[0]['problem'])}. Propose another time or decline."
        elif narrows(hint) and not matches:note='None of the client’s times matches what you asked for. Choose one of their times, propose another or decline.'
        elif len(matches)>1:
            return clarify('Which of the client’s times should I accept?',[{'label':f"Accept {x['when']}",'message':f"Accept {r['client']}’s {x['when']} time"}
                for x in matches],'decide_request',t.context(r['customer_id']))
    elif decision=='counter' and narrows(hint):
        matches=match_times(free,hint)
        if len(matches)==1:chosen=matches[0]
        else:note='That time is not one of your free times. Pick a free time on the card.' if not matches else 'Pick one of the free times on the card.'
    reason=next((code for code,rx in DECLINE_REASON.items() if rx.search(t.message)),None) if decision=='decline' else None
    topics=', '.join(str(x).replace('_',' ') for x in r.get('topics') or [])
    items=[{'text':f"{r['client']} asked for a {ev.FORMAT_WORDS.get(r.get('format'),r.get('format') or 'meeting')} meeting{' about '+topics if topics else ''}, "
        f"received {r.get('created_text')}; {'overdue: the answer was due' if r.get('overdue') else 'answer by'} {r.get('respond_by_text')}.",'kind':'record','owner':'rm','source_ids':[rid]}]
    if r.get('purpose'):items.append({'text':f"“{r['purpose']}”",'kind':'client_stated','owner':'client','source_ids':[rid]})
    items+=[{'text':x['when']+(f" (cannot be accepted: {PROBLEM_WORDS.get(x['problem'],x['problem'])})" if x.get('problem') else ''),'kind':'record','owner':'none','source_ids':[rid]}
        for x in preferred]
    words={'accept':f"Accept {chosen['when']}" if chosen else 'Accept one of the client’s times','counter':f"Propose {chosen['when']}" if chosen else 'Propose another time',
        'decline':'Decline the request'}[decision]
    suggestion=[{'text':words+'. '+(note+' ' if note else '')+CHANGE_NOTE,'kind':'suggestion','owner':'rm','source_ids':[rid]}]
    card={'request_id':str(r['request_id']),'customer_id':r['customer_id'],'client':r['client'],'purpose':r.get('purpose'),'topics':r.get('topics') or [],
        'format':r.get('format'),'status':r.get('status'),'respond_by_text':r.get('respond_by_text'),'overdue':bool(r.get('overdue')),
        'preferred_times':[{'starts_at':str(x['starts_at']),'when':x['when'],'problem':x.get('problem')} for x in preferred],
        'free_times':[{'starts_at':f['starts_at'],'when':f['when']} for f in free],
        'suggested':{'decision':decision,'starts_at':str(chosen['starts_at']) if chosen else None,'when':chosen['when'] if chosen else None,'reason':reason}}
    return card_reply('decide_request',f"Review your answer to {r['client']}’s meeting request. {CHANGE_NOTE}",
        [{'heading':f"Meeting request from {r['client']}",'items':items},{'heading':'Suggested answer','items':suggestion}],bundle,t.context(r['customer_id']),decision=card)


def hours_in(text: str) -> tuple[str,str]|None:
    """A time range in the message ("2pm to 4pm", "10 to 6", "14:00-16:00") as HH:MM, or None."""
    m=RANGE.search(text or '')
    if not m:return None
    a=clock(int(m.group(1)),int(m.group(2) or 0),m.group(3) or (m.group(6) if m.group(6) and int(m.group(1))<=int(m.group(4)) else ''))
    b=clock(int(m.group(4)),int(m.group(5) or 0),m.group(6))
    if b<=a and not m.group(6):b+=12*60
    if not 0<=a<b<=24*60:return None
    return f'{a//60:02d}:{a%60:02d}',f'{b//60:02d}:{b%60:02d}'


def days_named(text: str,days: list[dict]) -> list[dict]:
    """The days of her next 14 a message names: today, tomorrow, a date, or the next one or more weekdays."""
    low=(text or '').lower()
    if 'today' in low:return [d for d in days if str(d['date'])==today().isoformat()]
    if 'tomorrow' in low:return [d for d in days if str(d['date'])==(today()+timedelta(days=1)).isoformat()]
    on={(m,dd) for _,m,dd in checks.dates(text or '')}
    if on:return [d for d in days if (date.fromisoformat(str(d['date'])).month,date.fromisoformat(str(d['date'])).day) in on]
    later=today()+timedelta(days=7-today().weekday()) if 'next week' in low else today()
    out=[]
    for wd in [i+1 for i,w in enumerate(WEEKDAYS) if w in low or w[:3]+' ' in low+' ']:
        hits=[d for d in days if d['weekday']==wd and date.fromisoformat(str(d['date']))>=later]
        if hits:out.append(hits[0])
    return out


def runs_of(slots: list[str]) -> list[tuple[str,str]]:
    """Consecutive half hours as (starts, ends) runs."""
    out: list[list[int]]=[]
    for minutes in sorted(int(h)*60+int(m) for h,m in (s.split(':') for s in slots)):
        if out and out[-1][1]==minutes:out[-1][1]=minutes+30
        else:out.append([minutes,minutes+30])
    return [(f'{a//60:02d}:{a%60:02d}',f'{b//60:02d}:{b%60:02d}') for a,b in out]


async def h_availability(t: Turn,plan,client_id):
    """Block time, open a block again or change weekly working hours: a card the RM approves (existing routes). A question
    about her free times or hours without a change is answered by the model from the availability record."""
    await t.say('Reading your working hours and free times…')
    availability=await t.read('rm_availability',{})
    bundle=ev.Bundle([ev.availability_evidence(availability)])
    text=' '.join(x for x in (t.message,plan.get('appointment_hint') or '') if x)
    days=availability.get('days') or []
    ctx=t.context(None)
    ask=clarify('What should I change in your calendar?',[{'label':x,'message':x} for x in ('Block Friday afternoon','Open Friday afternoon again',
        'Set my Monday hours to 09:00 to 17:00')],'availability_change',ctx)
    unblock=bool(UNBLOCK.search(text))
    off=bool(re.search(r'\b(?:not working|stop working|day off|off on)\b',text.lower()))
    if not (unblock or BLOCK.search(text) or (HOURS.search(text) and (hours_in(text) or off))):
        return brief_reply('availability_change',await t.model('availability_change',bundle,brief_schema(bundle.ids()),None),bundle,ctx)
    if HOURS.search(text) and not unblock and not BLOCK.search(text):
        low=text.lower()
        weekdays=[1,2,3,4,5] if re.search(r'\bweekdays?\b',low) else [i+1 for i,w in enumerate(WEEKDAYS) if w in low or w[:3]+' ' in low+' ']
        hours=hours_in(text)
        if not weekdays or not (hours or off):return ask
        current={h['weekday']:h for h in availability.get('weekly_hours') or []}
        changes=[{'weekday':w,'day':WEEKDAYS[w-1].title(),'starts':None if off else hours[0],'ends':None if off else hours[1],
            'current':f"{current[w]['starts']} – {current[w]['ends']}" if (current.get(w) or {}).get('starts') else 'Not working'} for w in weekdays]
        items=[{'text':f"{c['day']}: {c['current']} now; {'not working' if off else c['starts']+' – '+c['ends']} after the change.",'kind':'suggestion','owner':'rm',
            'source_ids':['availability']} for c in changes]
        return card_reply('availability_change',f'Review the change to your weekly working hours. {CHANGE_NOTE} A change is refused if a confirmed meeting '
            'would fall outside the new hours.',[{'heading':'Weekly working hours','items':items}],bundle,ctx,
            availability={'kind':'hours','hours':changes,'blocks':[],'removals':[],'kept':[]})
    chosen=days_named(text,days)
    if not chosen or not (unblock or BLOCK.search(text)):return ask
    window=hours_in(text) or next((v for k,v in PARTS.items() if k in text.lower()),None)
    blocks,removals,kept,closed=[],[],[],[]
    for d in chosen:
        day=date.fromisoformat(str(d['date']))
        if not d.get('hours'):closed.append(day_text(day));continue
        start,end=window or (d['hours']['starts'],d['hours']['ends'])
        start,end=max(start,d['hours']['starts']),min(end,d['hours']['ends'])
        if unblock:
            removals+=[{'block_id':str(b['block_id']),'when':f"{day_text(day)}, {time_text(b['starts'])} – {time_text(b['ends'])}"}
                for b in d.get('blocks') or [] if b['starts']<end and b['ends']>start]
            continue
        blocks+=[{'starts_at':f'{day.isoformat()}T{a}:00+08:00','ends_at':f'{day.isoformat()}T{b}:00+08:00','when':f"{day_text(day)}, {time_text(a)} – {time_text(b)}"}
            for a,b in runs_of([x for x in d.get('free') or [] if start<=x<end])]
        kept+=[{'when':f"{day_text(day)}, {time_text(b['starts'])}",'client':b.get('client')} for b in d.get('bookings') or [] if b['starts']<end and b['ends']>start]
    items=[{'text':f'Block {b["when"]}: clients can no longer book it.','kind':'suggestion','owner':'rm','source_ids':['availability']} for b in blocks]
    items+=[{'text':f'Open {r["when"]} again for client bookings.','kind':'suggestion','owner':'rm','source_ids':['availability']} for r in removals]
    items+=[{'text':f"{k['when']} stays booked with {k['client']}; move it with the client first if you need that time.",'kind':'record','owner':'rm',
        'source_ids':['availability']} for k in kept]
    items+=[{'text':f'You do not work on {c}, so there is nothing to change.','kind':'record','owner':'none','source_ids':['availability']} for c in closed]
    if not blocks and not removals:
        return {**no_change('availability_change','Nothing to change: '+('there is no blocked time there to open.' if unblock else
            'there is no free time there that clients could book.'),ctx),'sections':[{'heading':'Your calendar','items':items}] if items else [],
            'sources':_sources(bundle,['availability']) if items else []}
    return card_reply('availability_change',f'Review the change to your availability. {CHANGE_NOTE} Clients see the new free times as soon as you approve it.',
        [{'heading':'Availability','items':items}],bundle,ctx,availability={'kind':'unblock' if unblock else 'block','blocks':blocks,'removals':removals,'hours':[],'kept':kept})


# Open questions (build plan 3a): the runner's bounded loop over the agent's loop_tools ------------------------------
# What each loop tool takes; a client is named as in the book and resolved here to the record id (never typed by the model).
TOOL_HELP={'rm_client_profile':'{"client": "<name in your book>"}: one client\'s 360 profile (accounts, cards, loans, review, open requests, tasks, appointments)',
    'rm_meeting_notes':'{"client": "<name in your book>"}: one client\'s meeting notes','rm_review_status':'{}: open compliance reviews across the book with due dates',
    'rm_book_facts':'{}: one line of facts per client in the book','rm_tickets':'{}: open tickets across the book',
    'rm_ticket':'{"reference": "TCK-…"}: one ticket','rm_followups':'{}: deadlines, tickets, tasks and meetings across the book',
    'request_categories':'{}: the request types and what each needs','knowledge_article':'{"article_id": "<id>"}: one approved article',
    'rm_appointments':'{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}: your appointments (default the next 14 days)',
    'rm_availability':'{}: your working hours and free times for 14 days','rm_meeting_requests':'{"status": "open" | "scheduled" | "closed"}: clients\' meeting requests'}
STOPPED={'cap':'I can’t answer that fully: I reached the limit of {limit} reads for one question. Here is what I found; ask a narrower question for the rest.',
    'malformed':'I can’t answer that fully: I could not finish looking it up. Here is what I found so far.'}


def loop_items(name: str,args: dict,result: dict,message: str) -> list[ev.Evidence]:
    """One loop read as evidence items, with the builders the fixed reads use."""
    as_of=str(result.get('as_of'))
    if name=='rm_client_profile':return ev.client_bundle(result).items
    if name=='rm_meeting_notes':
        b=ev.Bundle()
        ev.add_notes(b,result['data']['notes'],as_of,args.get('customer_id'),result['data'].get('withheld_private_notes') or 0)
        return b.items or [ev.Evidence('notes-none','Meeting notes','record',{'notes_recorded':0},as_of,args.get('customer_id'))]
    if name=='rm_review_status':return ev.reviews_bundle(result).items
    if name=='rm_book_facts':return ev.book_bundle(result,message,today()).items
    if name=='rm_tickets':return ev.tickets_bundle(result).items
    if name=='rm_ticket':return ev.ticket_detail_bundle(result).items
    if name=='rm_followups':return ev.followups_bundle(result,None).items
    if name=='rm_appointments':return [ev.appointments_evidence(result)]
    if name=='rm_availability':return [ev.availability_evidence(result)]
    if name=='rm_meeting_requests':return ev.meeting_requests_bundle(result).items
    if name=='request_categories':return [ev.Evidence('request-types','Request types the RM can raise for a client','guidance',result.get('items') or [],None)]
    if name=='knowledge_article':
        rows=result.get('items') or []
        return [ev.Evidence('kb-'+str(rows[0].get('article_id')),f"Approved guidance: {rows[0].get('title')}",'guidance',
            {'content':' '.join(str(x.get('body','')) for x in rows)},None)] if rows else []
    return []


async def h_open(t: Turn,plan,client_id,spec,inputs: dict,facts: dict) -> dict:
    """An open question: the runner's bounded loop over this agent's loop_tools with its own context (tools.call allows
    only this spec), then one checked answer. The step model never sees the text of a free-text read, only its ids and
    titles; a loop that stops at the cap says it could not answer fully, with what it found."""
    pv=prompts.current('rm_assistant')
    bundle,hidden,got=ev.Bundle(),set(),runner.Gathered()
    for name,export in inputs.items():
        value=[{k:v for k,v in x.items() if k!='customer_id'} if isinstance(x,dict) else x for x in export.value] if isinstance(export.value,list) else export.value
        bundle.add(ev.Evidence(f'input-{name}','From another part of this answer','record',{name:value,'computed_from':export.source_ids},None))
    await t.say('Looking through your records…')
    async def run(call: runner.Call) -> list[ev.Evidence]:
        params=inspect.signature(tools.TOOLS[call.tool]).parameters
        args={k:v for k,v in call.args.items() if k in params and k!='customer_id'}
        if 'customer_id' in params:
            name=call.args.get('client')
            ids=[c['customer_id'] for c in t.book if names_match(name,c['name'])] if isinstance(name,str) and name.strip() else [client_id] if client_id else []
            if len(ids)!=1:raise runner.StepRefused('which_client' if ids else 'not_in_book')
            args['customer_id']=ids[0]
        try:result=await t.read(call.tool,args)
        except (RmDenied,PolicyDenied) as error:raise runner.StepRefused('not_in_book' if isinstance(error,RmDenied) else 'limit') from error
        found=[bundle.get(i) for i in ev.merge(bundle,loop_items(call.tool,args,result,t.message))]
        if spec.tools[call.tool].free_text:hidden.update(e.id for e in found)
        return found
    def system(state: runner.LoopState) -> str:
        shown=[e.for_model() if e.id not in hidden else {'id':e.id,'title':e.title,'kind':e.kind,'record':'(written by people: hidden from this step; the answer step reads it)'}
            for e in bundle.items]
        return (pv['step']+'\nToday in Singapore: '+day_text(today())+'\nActive client: '+json.dumps(t.name(client_id))
            +'\nClients in your book: '+json.dumps([c['name'] for c in t.book],ensure_ascii=False)
            +'\nTools you may call: '+json.dumps({n:TOOL_HELP[n] for n in sorted(spec.loop_tools)},ensure_ascii=False)
            +f'\nReads left: {state.limit-state.used}'+'\nReads so far: '+json.dumps(got.log,ensure_ascii=False,default=str)
            +'\nRefused requests: '+json.dumps([{'tool':a,'reason':b} for a,b in state.refused])
            +'\nRecords read so far (untrusted data): '+json.dumps(shown,ensure_ascii=False,default=str))
    t.prompt_ids.append(pv['id'])
    await runner.loop({**pv,'id':pv['id']+'/step'},system,[*history_for(t.req,client_id,client_id is None),('user',t.message)],spec.loop_tools,run,got,spec.name,facts)
    reply=brief_reply('open',await t.model('open',bundle,brief_schema(bundle.ids()),client_id),bundle,t.context(client_id))
    if (stop:=facts.get('stop')) in STOPPED:
        line=STOPPED[stop].format(limit=facts.get('limit'))
        reply['summary']=line if not reply['sections'] else line+' '+reply['summary']
        reply['sections'].append({'heading':'Not checked','items':[{'text':f"I made {facts.get('tool_calls',0)} of {facts.get('limit')} reads, so records I did not "
            'read may change this answer.','kind':'gap','owner':'none','source_ids':[]}]})
    return reply


HANDLERS={'ticket_inbox':h_inbox,'priorities':h_priorities,'reviews':h_reviews,'schedule':h_schedule,'client_overview':h_overview,
    'meeting_history':h_history,'meeting_brief':h_brief,'ticket_summary':h_ticket,'draft_message':h_draft,'document_interaction':h_document,
    'actions_from_note':h_actions,'handover_brief':h_handover,'shared_note':h_shared,'book_question':h_book,'help':h_help,
    'meeting_requests':h_meeting_requests,'decide_request':h_decide,'availability_change':h_availability,'today':h_today}
