import json
import re
from app.schemas.chat import Message
from app.services.knowledge import search, source, INDEX, ARTICLES, is_current
from app.services.privacy import redact
from app.services.source_checks import check_sources
from app.services.llm import llm

NOTICE='General information only, not advice or a contract. Demo records are synthetic. Only the bank can confirm fees or approve a loan.'

def clean_history(messages):
    history=[Message(role=m.role,content=redact(m.content)[0]) for m in messages]
    while len(history)>29 or sum(len(m.content) for m in history)>16000:history.pop(0)
    while len(history)>1 and history[0].role!='user':history.pop(0)
    return history

async def make_answer(req, context=None):
    history=clean_history(req.messages)
    question=history[-1].content
    warning=any(redact(m.content)[1] for m in req.messages)
    result={'answer':'','sources':[],'privacy_warning':warning,'history_trimmed':len(history)<len(req.messages),'disclaimer':NOTICE,'needs_human':False,'source_check':'fixed_guidance'}
    lost_card=bool(re.search(r'\b(lost|stolen|missing)\b.*\bcard\b|\bcard\b.*\b(lost|stolen|missing)\b',question,re.I))
    if warning:
        result.update(answer='Private details were removed. Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. If a real secret was shared, use your bank’s official security or recovery service.',sources=[source(INDEX['security'])],needs_human=True)
        if lost_card:
            result['answer']+=' '+INDEX['lost-card'].content
            result['sources'].append(source(INDEX['lost-card']))
        return result
    if lost_card:
        result.update(answer=INDEX['lost-card'].content,sources=[source(INDEX['lost-card'])],needs_human=True)
        return result
    if re.search(r'\b(talk|speak|connect)\b.*\b(person|human|staff|agent)\b',question,re.I):
        result.update(answer=INDEX['support'].content,sources=[source(INDEX['support'])],needs_human=True)
        return result
    if req.mode=='public' and re.search(r'\b(my|mine)\b.*\b(balance|transactions|due|limit|account number)\b',question,re.I):
        result.update(answer='Sign in and select Personal demo records to ask about your synthetic balances, transactions or payment details. General banking help is available without login.',sources=[source(INDEX['accounts'])])
        return result
    articles=search(question)
    if not articles:
        articles=search(' '.join(m.content for m in history if m.role=='user')[-8000:])
    if req.language!='English':
        articles=[a for a in ARTICLES if is_current(a)]
    if not articles and not context:
        result.update(answer='I can help with accounts, loans, cards and transaction questions. Which banking service do you mean? I do not have an approved source for this request.',needs_human=True)
        return result
    evidence=[{'id':a.id,'text':a.content} for a in articles]
    sources={a.id:source(a) for a in articles}
    if context:
        evidence.append({'id':'customer-records','text':context})
        sources['customer-records']={'id':'customer-records','title':'Your authenticated synthetic records','url':None,'version':'Current request'}
    result['source_check']='rejected'
    raw=await llm.answer(history,evidence,req.language)
    try:
        parsed=json.loads(raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip())
        answer=parsed['answer']; ids=parsed['source_ids']
        if not isinstance(answer,str) or not answer.strip() or len(answer)>4000:raise ValueError()
        if check_sources(answer,ids,evidence):raise ValueError()
        answer,leaked=redact(answer)
        if leaked or re.search(r'https?://|\b(?:I have|I\x27ve|we have|successfully)\s+(?:blocked|transferred|approved|submitted|refunded)',answer,re.I):raise ValueError()
        result.update(answer=answer,sources=[sources[i] for i in dict.fromkeys(ids)],source_check='basic_checks_passed')
    except (ValueError,KeyError,TypeError):
        result.update(answer='I could not verify this answer against the available sources. Please rephrase your question or use your bank’s official support channel.',needs_human=True)
    return result
