import json
import re
from uuid import uuid4
from collections.abc import Awaitable,Callable
from app.core.observability import span, add_span, current_trace
from app.schemas.chat import ChatRequest,Message
from app.schemas.agents import Evidence
from app.schemas.contracts import Principal,RequestContext
from app.schemas.events import Source
from app.schemas.knowledge import KnowledgeQuery
from app.core import data_api
from app.services.privacy import redact
from app.services.guardrail import assess_request,DELEGATE_CLAIM
from app.services.source_checks import check_sources, numbers
from app.services.llm import llm,answer_prompt
from app.services import kb

EvidenceLoader=Callable[[str,list[Message],str],Awaitable[list[Evidence]]]

NOTICE='General information only, not advice or a contract. Demo records are synthetic. Only the bank can confirm fees or approve a loan.'
URL=re.compile(r'https?://',re.I)
# Fixed safety replies never wait for the model or the database. Their wording follows the approved lost-card v4 and
# support v5 articles (migrations 0096, 0098), and they cite the pinned section they follow. Published versions stay
# citable after a revision; tests/test_knowledge_checks.py checks that each pin is the current section and the text matches.
def pinned(article_id: str,version: int,anchor: str,title: str) -> dict:
    return Source(id=f'kb:{article_id}@v{version}#{anchor}',title=title,url=f'/knowledge/sections/{article_id}/{version}/{anchor}',
        version=f'v{version}',publisher='DemoBank coursework project').model_dump()
PINNED={'security':pinned('security',2,'never-share','Protecting private information'),
    'lost-card':pinned('lost-card',4,'main','Lost or stolen card guidance'),
    'support':pinned('support',5,'main','Getting human support'),
    'accounts':pinned('accounts',1,'main','Demo account information'),
    'unrecognised-charge':pinned('unrecognised-charge',3,'urgent-steps','Unrecognised card charge')}
LOST_CARD=('If your card is lost or stolen, freeze it straight away: on Overview, under Cards, choose Manage and then Freeze card. '
    'Then raise a replacement request (Manage, then Report lost, stolen or damaged); a lost or stolen card is frozen as soon as you submit, '
    'and card operations cancel it and send a new card with a new number to your registered address. If you also see charges you do not '
    'recognise, report them in the chat: they go to the fraud team as urgent. You can unfreeze a card yourself only if you froze it and '
    'nothing is open with the fraud team or card operations. Never share your PIN, password, OTP, CVV or full card number.')
HUMAN_SUPPORT=('When you are signed in, Talk to a person shows a short summary; approving it passes your question to your relationship '
    'manager with a reference to follow in Requests. Charges you do not recognise and lost or stolen cards go to the fraud team as urgent '
    'instead. As a guest you can review, edit and copy a summary for your own use; copying does not send anything. For a real issue, contact '
    'your bank using its official app or website. The assistant cannot make payments, block or cancel cards, close accounts, dispute, refund '
    'or reverse charges, or change banking records.')
ACTION_CLAIM=re.compile(r'\b(?:I have|I\x27ve|we have|successfully)\s+(?:blocked|transferred|approved|submitted|refunded|reversed|cancell?ed|disputed|investigated|expedited|sped up|passed|forwarded|escalated|opened|created|sent)',re.I)

def clean_history(messages):
    history=[Message(role=m.role,content=redact(m.content)[0]) for m in messages]
    while len(history)>29 or sum(len(m.content) for m in history)>16000:history.pop(0)
    while len(history)>1 and history[0].role!='user':history.pop(0)
    return history

def decide(info,route):
    info['route']=route
    add_span('route','agent',route=route,risk_flags=info['risk_flags'],priority=info['priority'])


def check_answer(raw,evidence):
    """Return (answer, source_ids, reason). reason is None when every check passes."""
    try:
        parsed=json.loads(raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
        if not isinstance(parsed,dict):return None,None,'bad_json'
        answer=parsed['answer']; ids=parsed['source_ids']
        if not isinstance(answer,str) or not answer.strip() or len(answer)>4000:return None,None,'bad_answer'
        kind=parsed.get('answer_kind','banking')  # v1 responses remain readable
        if kind=='conversation':
            # No citations or financial figures in a short conversational reply.
            # This is a format check, not a semantic guarantee of correct routing.
            if ids!=[] or numbers(answer) or len(answer)>600:return None,None,'source_check'
        elif kind!='banking' or check_sources(answer,ids,evidence):return None,None,'source_check'
    except (ValueError,KeyError,TypeError):return None,None,'bad_json'
    answer,leaked=redact(answer)
    if leaked:return None,None,'secret_leaked'
    if URL.search(answer):return None,None,'url_in_answer'
    if ACTION_CLAIM.search(answer):return None,None,'action_claim'
    return answer,ids,None

def validate(raw,evidence):
    with span('validate','decision') as a:
        answer,ids,reason=check_answer(raw,evidence)
        a['passed']=reason is None
        if reason:a['reason']=reason
    return answer,ids,reason

async def make_answer(req: ChatRequest,context: str | None=None,ctx: RequestContext | None=None,*,allow_model=True) -> dict:
    """The v1 personal/safety flow with ordinary guidance from persisted knowledge; no match stays an empty result.
    Fixed safety routes return before any database read, so they need no `ctx` (a guest context is used otherwise)."""
    ctx=ctx or RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    async def load(question: str,history: list[Message],language: str) -> list[Evidence]:
        with span('retrieve','tool') as attrs:
            with data_api.connect(ctx) as db:
                sections=await kb.search(KnowledgeQuery(query=question,scope='public',k=4),ctx,db)
            attrs.update(store='database',count=len(sections),article_ids=[item.cite for item in sections])
        return [Evidence(id=item.cite,text=item.body,publisher=item.publisher,scope=item.scope,source=item.source()) for item in sections]
    return await produce_answer(req,context,allow_model,load)


async def produce_answer(req,context,allow_model,evidence_loader: EvidenceLoader):
    """Copy the route, retrieved ids and reject reason to the request trace (audit and root-run metadata)."""
    info={}
    result=await _answer(req,context,info,allow_model,evidence_loader)
    trace=current_trace.get()
    if trace:trace.attrs.update(route=info.get('route'),source_check=result['source_check'],needs_human=result['needs_human'],privacy_warning=result['privacy_warning'],prompt_version=result['prompt_version'],reject_reason=info.get('reject_reason'),article_ids=info.get('article_ids',[]),history_trimmed=result['history_trimmed'])
    return result

async def _answer(req,context,info,allow_model,evidence_loader: EvidenceLoader):
    history=clean_history(req.messages)
    question=history[-1].content
    risk=assess_request(req)
    warning=bool(risk.redactions)
    info.update(risk_flags=risk.risk_flags,priority=risk.priority)
    result={'answer':'','sources':[],'privacy_warning':warning,'history_trimmed':len(history)<len(req.messages),'disclaimer':NOTICE,'needs_human':False,'source_check':'fixed_guidance','prompt_version':None,'response_origin':'safety'}
    lost_card='lost_card' in risk.risk_flags
    if warning:
        decide(info,'secrets')
        result.update(answer='Private details were removed. Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. If a real secret was shared, use your bank’s official security or recovery service.',sources=[PINNED['security']],needs_human=True)
        if lost_card:
            result['answer']+=' '+LOST_CARD
            result['sources'].append(PINNED['lost-card'])
        return result
    if lost_card:
        decide(info,'lost_card')
        result.update(answer=LOST_CARD,sources=[PINNED['lost-card']],needs_human=True)
        return result
    if risk.fixed_response and risk.fixed_response.code=='other_customer_data':
        decide(info,'other_customer_data')
        result.update(answer="I can't access or disclose another client's account balances, transactions or records. I can only help you with information you are authorised to view.",needs_human=False)
        if req.mode=='public' and DELEGATE_CLAIM.search(question):
            result['answer']="I can’t verify delegate access through chat or share a client’s review details or accept documents for them. The client needs to sign in herself. I can offer general banking help, or you can use Talk to a person."
        return result
    if risk.fixed_response and risk.fixed_response.code=='instruction_override':
        decide(info,'instruction_override')
        result.update(answer='I cannot bypass access checks, approve a banking request, mark documents accepted, or show another customer’s private records. Only the authorised bank team can make those decisions. I can help you understand your own review status, required steps or the application process.',needs_human=False)
        return result
    if risk.fixed_response and risk.fixed_response.code=='human_support':
        decide(info,'human_support')
        result.update(answer=HUMAN_SUPPORT,sources=[PINNED['support']],needs_human=True)
        return result
    if risk.fixed_response and risk.fixed_response.code=='guest_balance':
        decide(info,'guest_balance')
        result.update(answer='Sign in and select Personal demo records to ask about your synthetic balances, transactions or payment details. General banking help is available without login.',sources=[PINNED['accounts']])
        return result
    if not allow_model:raise RuntimeError('Model calls are disabled for this check')  # before any database read
    retrieved=await evidence_loader(question,history,req.language)
    info['article_ids']=[item.id for item in retrieved]
    evidence=[item.model_dump(exclude={'source'}) for item in retrieved]
    sources={item.id:item.source.model_dump() for item in retrieved}
    if context:
        evidence.append({'id':'customer-records','text':context})
        sources['customer-records']={'id':'customer-records','title':'Your authenticated synthetic records','url':None,'version':'Current request'}
    decide(info,'llm')
    result['source_check']='rejected'
    result['prompt_version']=answer_prompt()['id']
    raw=await llm.answer(history,evidence,req.language)
    answer,ids,reason=validate(raw,evidence)
    if reason=='source_check':
        # One real-model revision, never an article dump or a simulated answer.
        # Do not include any rejected secret in the revision request.
        raw=await llm.answer(history,evidence,req.language,repair={'reason':reason,'draft':redact(raw[:6000])[0]})
        answer,ids,reason=validate(raw,evidence)
    try:
        if reason:raise ValueError()
        result.update(answer=answer,sources=[sources[i] for i in dict.fromkeys(ids)],source_check='basic_checks_passed',response_origin='ai')
        if not ids:result['disclaimer']=''
    except (ValueError,KeyError,TypeError):
        info['reject_reason']=reason or 'unknown_source'
        result.update(answer='I could not verify this answer against the available sources. Please rephrase your question or use your bank’s official support channel.',needs_human=True,response_origin='validation')
    return result
