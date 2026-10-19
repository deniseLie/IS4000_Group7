import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from sse_starlette.sse import EventSourceResponse
from app.core.auth import get_customer_context
from app.core.config import settings
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import start_trace, span
from app.schemas.chat import ChatRequest
from app.schemas.contracts import RequestContext
from app.services.answers import make_answer
from app.services.guardrail import assess_request
from app.services.audit import AuditWriteError, start_batch
from app.services.knowledge import INDEX, source
from app.services.trace_store import save_trace
from app.services.orchestrator import answer_public

logger=logging.getLogger(__name__)
router=APIRouter()
FIXED_ROUTES={'secrets','lost_card','human_support','guest_balance'}

def audit_reply(batch,trace,result=None,error=None):
    """Model calls and the reply outcome from the trace: ids, enums and counts only."""
    for s in trace.spans:
        if s['name']=='llm':
            batch.add('agent','agent.llm_call','success' if s['status']=='ok' else 'failed',details={**{k:s['attrs'].get(k) for k in ('model','prompt_version','transport','input_tokens','output_tokens')},'ms':s.get('ms')})
    route=trace.attrs.get('route')
    if error:
        batch.add('chat','chat.reply_sent','failed',details={'route':route,'error_type':type(error).__name__[:64]})
        return
    batch.add('chat','chat.fixed_response' if route in FIXED_ROUTES else 'chat.reply_sent',details={
        'route':route,'origin':result['response_origin'],'source_check':result['source_check'],'needs_human':result['needs_human'],
        'privacy_warning':result['privacy_warning'],'history_trimmed':result['history_trimmed'],'prompt_version':result['prompt_version'],
        # Audit's released citation format is article@vN#anchor (without the knowledge namespace).
        'reject_reason':trace.attrs.get('reject_reason'),'source_ids':[x['id'].removeprefix('kb:') for x in result['sources']]})

@router.get('/knowledge/{article_id}')
def knowledge(article_id:str):
    article=INDEX.get(article_id)
    if not article:raise HTTPException(404,'Source not found')
    return {**source(article),'content':article.content,'notice':article.notice}

@router.post('/chat')
async def chat(req:ChatRequest,request:Request,ctx:RequestContext=Depends(get_ctx)):
    """The gateway has already checked the token, rate limit and size; ctx is the only source of identity."""
    p,db=ctx.principal,request.state.db
    if req.mode=='personal' and p.kind!='customer':raise ApiError(401,'auth_required','Please sign in to use your account.')
    if req.mode=='personal' and p.customer_id is None:raise ApiError(403,'profile_missing','No demo customer profile is linked to this login.')
    risk=assess_request(req)
    if settings.pipeline=='v2' and req.mode=='personal' and risk.fixed_response is None:
        raise ApiError(503,'personal_unavailable','Personal demo records are unavailable. Select General help to continue.')
    trace=start_trace()
    trace.attrs.update(mode=req.mode,language=req.language[:32],authenticated=p.kind=='customer',history_len=len(req.messages))
    batch=start_batch(db,session_id=ctx.session_id)
    batch.add('chat','chat.message_received',details={'mode':req.mode,'history_len':len(req.messages)})
    if req.mode=='personal' and risk.fixed_response is None:
        with span('customer_context','tool') as a:
            context=await run_in_threadpool(get_customer_context,db,p.customer_id)
            a['chars']=len(context)
        batch.add('data','data.read',resource_type='customer_context',details={'tool':'customer_context'})
    else:context=None
    async def events():
        yield {'event':'status','data':'Checking the available information…'}
        try:
            if settings.pipeline=='v2' and risk.fixed_response is None:
                result=(await answer_public(req,risk,ctx)).model_dump(mode='json')
                trace.attrs.update(route='llm',source_check=result['source_check'],needs_human=result['needs_human'],
                    privacy_warning=False,prompt_version=result['prompt_version'],history_trimmed=result['history_trimmed'])
            else:result=await make_answer(req,context)
            audit_reply(batch,trace,result)
            await batch.flush()  # before the answer: an unaudited answer is never sent
            trace.attrs['outcome']='ok'
            yield {'event':'answer','data':json.dumps(result,ensure_ascii=False)}
            yield {'event':'done','data':''}
        except Exception as e:
            logger.warning('Answer generation failed')
            trace.attrs.update(outcome='error',error_type=type(e).__name__[:64],route=trace.attrs.get('route') or 'error')
            if not isinstance(e,AuditWriteError):
                try:
                    audit_reply(batch,trace,error=e)
                    await batch.flush()
                except Exception:pass  # already failing; flush() logs a failed write
            yield {'event':'error','data':'The assistant is temporarily unavailable. Please retry or use your bank’s official support channel.'}
        finally:
            trace.attrs.setdefault('outcome','error')
            trace.attrs.setdefault('route','error')
            save_trace(trace)
    return EventSourceResponse(events())
