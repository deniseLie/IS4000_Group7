import json
import re
from app.core.observability import log_event, timer, span, add_span, current_trace
from app.schemas.chat import Message
from app.services.knowledge import search, source, INDEX, ARTICLES, is_current
from app.services.privacy import redact
from app.services.source_checks import check_sources, numbers
from app.services.llm import llm
from app.services import prompts

NOTICE='General information only, not advice or a contract. Demo records are synthetic. Only the bank can confirm fees or approve a loan.'
URL=re.compile(r'https?://',re.I)
ACTION_CLAIM=re.compile(r'\b(?:I have|I\x27ve|we have|successfully)\s+(?:blocked|transferred|approved|submitted|refunded)',re.I)

def clean_history(messages):
    history=[Message(role=m.role,content=redact(m.content)[0]) for m in messages]
    while len(history)>29 or sum(len(m.content) for m in history)>16000:history.pop(0)
    while len(history)>1 and history[0].role!='user':history.pop(0)
    return history

def decide(info,route):
    info['route']=route
    add_span('route','agent',route=route)

def retrieve(question,history,language):
    with span('retrieve','tool') as a:
        articles=search(question)
        # A greeting or insult must not be forced into an earlier banking topic.
        # The library is small: give the model current guidance for ambiguous
        # follow-ups and let it respond to the latest message in context.
        a['history_fallback']=False
        a['all_articles']=not articles or language!='English'
        if a['all_articles']:
            articles=[x for x in ARTICLES if is_current(x)]
        a['count']=len(articles)
        a['article_ids']=[x.id for x in articles]
    return articles

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

async def make_answer(req, context=None, *, allow_model=True):
    """Log one summary line per request: route, retrieved ids, prompt version, reject reason, timings. No user text or records."""
    info={}
    with timer() as t:
        result=await _answer(req,context,info,allow_model)
    info.update(source_check=result['source_check'],needs_human=result['needs_human'],privacy_warning=result['privacy_warning'],prompt_version=result['prompt_version'],ms=t.ms)
    log_event('answer',**info)
    trace=current_trace.get()
    if trace:trace.attrs.update(route=info.get('route'),source_check=result['source_check'],needs_human=result['needs_human'],privacy_warning=result['privacy_warning'],prompt_version=result['prompt_version'],reject_reason=info.get('reject_reason'),article_ids=info.get('article_ids',[]),history_trimmed=result['history_trimmed'])
    return result

async def _answer(req, context, info, allow_model=True):
    history=clean_history(req.messages)
    question=history[-1].content
    warning=any(redact(m.content)[1] for m in req.messages)
    result={'answer':'','sources':[],'privacy_warning':warning,'history_trimmed':len(history)<len(req.messages),'disclaimer':NOTICE,'needs_human':False,'source_check':'fixed_guidance','prompt_version':None,'response_origin':'safety'}
    lost_card=bool(re.search(r'\b(lost|stolen|missing)\b.*\bcard\b|\bcard\b.*\b(lost|stolen|missing)\b',question,re.I))
    if warning:
        decide(info,'secrets')
        result.update(answer='Private details were removed. Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. If a real secret was shared, use your bank’s official security or recovery service.',sources=[source(INDEX['security'])],needs_human=True)
        if lost_card:
            result['answer']+=' '+INDEX['lost-card'].content
            result['sources'].append(source(INDEX['lost-card']))
        return result
    if lost_card:
        decide(info,'lost_card')
        result.update(answer=INDEX['lost-card'].content,sources=[source(INDEX['lost-card'])],needs_human=True)
        return result
    if re.search(r'\b(talk|speak|connect)\b.*\b(person|human|staff|agent)\b',question,re.I):
        decide(info,'human_support')
        result.update(answer=INDEX['support'].content,sources=[source(INDEX['support'])],needs_human=True)
        return result
    if req.mode=='public' and re.search(r'\b(my|mine)\b.*\b(balance|transactions|due|limit|account number)\b',question,re.I):
        decide(info,'guest_balance')
        result.update(answer='Sign in and select Personal demo records to ask about your synthetic balances, transactions or payment details. General banking help is available without login.',sources=[source(INDEX['accounts'])])
        return result
    articles=retrieve(question,history,req.language)
    info['article_ids']=[a.id for a in articles]
    evidence=[{'id':a.id,'text':a.content,'publisher':a.publisher,'scope':a.notice} for a in articles]
    sources={a.id:source(a) for a in articles}
    if context:
        evidence.append({'id':'customer-records','text':context})
        sources['customer-records']={'id':'customer-records','title':'Your authenticated synthetic records','url':None,'version':'Current request'}
    decide(info,'llm')
    result['source_check']='rejected'
    result['prompt_version']=prompts.current()['id']
    if not allow_model:raise RuntimeError('Model calls are disabled for this check')
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
