import json
import logging
from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool
from sse_starlette.sse import EventSourceResponse
from app.core.auth import get_user_db, get_customer_context
from app.core.observability import start_trace, span
from app.schemas.chat import ChatRequest
from app.services.answers import make_answer
from app.services.knowledge import INDEX, source
from app.services.trace_store import save_trace

logger=logging.getLogger(__name__)
router=APIRouter()

@router.get('/knowledge/{article_id}')
def knowledge(article_id:str):
    article=INDEX.get(article_id)
    if not article:raise HTTPException(404,'Source not found')
    return {**source(article),'content':article.content,'notice':article.notice}

@router.post('/chat')
async def chat(req:ChatRequest,authorization:str|None=Header(default=None)):
    trace=start_trace()
    sb=await run_in_threadpool(get_user_db,authorization) if authorization is not None or req.mode=='personal' else None
    trace.attrs.update(mode=req.mode,language=req.language[:32],authenticated=sb is not None,history_len=len(req.messages))
    if req.mode=='personal':
        with span('customer_context','tool') as a:
            context=await run_in_threadpool(get_customer_context,sb)
            a['chars']=len(context)
    else:context=None
    async def events():
        yield {'event':'status','data':'Checking the available information…'}
        try:
            result=await make_answer(req,context)
            trace.attrs['outcome']='ok'
            yield {'event':'answer','data':json.dumps(result,ensure_ascii=False)}
            yield {'event':'done','data':''}
        except Exception as e:
            logger.warning('Answer generation failed')
            trace.attrs.update(outcome='error',error_type=type(e).__name__[:64],route=trace.attrs.get('route') or 'error')
            yield {'event':'error','data':'The assistant is temporarily unavailable. Please retry or use your bank’s official support channel.'}
        finally:
            trace.attrs.setdefault('outcome','error')
            trace.attrs.setdefault('route','error')
            save_trace(trace)
    return EventSourceResponse(events())
