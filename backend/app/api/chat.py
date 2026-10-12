import json
import logging
from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool
from sse_starlette.sse import EventSourceResponse
from app.core.auth import get_user_db, get_customer_context
from app.schemas.chat import ChatRequest
from app.services.answers import make_answer
from app.services.knowledge import INDEX, source

logger=logging.getLogger(__name__)
router=APIRouter()

@router.get('/knowledge/{article_id}')
def knowledge(article_id:str):
    article=INDEX.get(article_id)
    if not article:raise HTTPException(404,'Source not found')
    return {**source(article),'content':article.content,'notice':article.notice}

@router.post('/chat')
async def chat(req:ChatRequest,authorization:str|None=Header(default=None)):
    sb=await run_in_threadpool(get_user_db,authorization) if authorization is not None or req.mode=='personal' else None
    context=await run_in_threadpool(get_customer_context,sb) if req.mode=='personal' else None
    async def events():
        yield {'event':'status','data':'Checking the available information…'}
        try:
            result=await make_answer(req,context)
            yield {'event':'answer','data':json.dumps(result,ensure_ascii=False)}
            yield {'event':'done','data':''}
        except Exception:
            logger.warning('Answer generation failed')
            yield {'event':'error','data':'The assistant is temporarily unavailable. Please retry or use your bank’s official support channel.'}
    return EventSourceResponse(events())
