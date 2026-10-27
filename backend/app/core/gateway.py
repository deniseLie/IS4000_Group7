"""API Gateway Layer (architecture §5.3, F4). Pure ASGI so SSE responses are not buffered. For every request except
/health and CORS preflight: payload size limit, JWT check (cached JWKS), rate limit, RequestContext, and
auth.session_started the first time a signed-in session_id is seen. No domain logic.
customer_id comes only from the verified token's sub (through the customer's own RLS-scoped profile row), never from
the request body. A bad token is a 401, never downgraded to guest. Routes read the context with get_ctx() and the
customer's database client from request.state.db; its connections close once the response has been sent.
Limit: tokens are checked locally, so a token revoked by sign-out stays usable until it expires."""
import asyncio
import logging
import time
from collections import OrderedDict
from contextvars import ContextVar
from uuid import UUID, uuid4
from fastapi import Request
from starlette.concurrency import run_in_threadpool
from app.core import auth
from app.core.config import settings
from app.core.errors import ApiError, error_response
from app.core.observability import log_event, request_id
from app.core.ratelimit import TokenBucket
from app.schemas.contracts import Principal, RequestContext
from app.services import audit

SKIP={'/health'}
CUSTOMER_TTL_S=300
current_ctx=ContextVar('current_ctx',default=None)
customer_bucket=TokenBucket(settings.rate_capacity,settings.rate_refill_per_s)
guest_bucket=TokenBucket(settings.guest_rate_capacity,settings.guest_rate_refill_per_s)
_customers={}  # auth user id -> (customer_id, expires)
_sessions=OrderedDict()  # auth session ids already recorded by this process


def _uuid(value):
    try:return UUID(value.decode('latin-1') if isinstance(value,bytes) else value)
    except (TypeError,ValueError,AttributeError):return None


def _limit(bucket,key):
    wait=bucket.take(key)
    if wait:raise ApiError(429,'rate_limited','Too many requests. Please wait a moment and retry.',{'Retry-After':str(wait)})


def _customer_id(db,user_id):
    hit=_customers.get(user_id)
    if hit and hit[1]>time.monotonic():return hit[0]
    try:cid=auth.resolve_customer_id(db,str(user_id))
    except Exception:
        log_event('customer_resolve_failed',logging.WARNING)
        raise ApiError(503,'unavailable','Banking service unavailable')
    if cid is not None:_customers[user_id]=(cid,time.monotonic()+CUSTOMER_TTL_S)
    return cid


async def _session_started(db,session_id):
    """public.audit_session_started (migration 0007) records once per auth session, taking session_id from the JWT itself."""
    if session_id in _sessions:return
    if audit.enabled():
        try:await asyncio.wait_for(run_in_threadpool(lambda:db.rpc('audit_session_started',{'p_request_id':request_id.get()}).execute()),timeout=10)
        except Exception as e:
            log_event('audit_write_failed',logging.WARNING,events=1,error=type(e).__name__)
            raise ApiError(503,'audit_unavailable','The assistant is temporarily unavailable. Please retry.')
    _sessions[session_id]=True
    if len(_sessions)>10000:_sessions.popitem(last=False)


async def build_context(scope,headers):
    """RequestContext plus the customer's database client (None for guests)."""
    rid=UUID(request_id.get())  # RequestIdMiddleware runs first and always sets a uuid
    authorization=headers.get(b'authorization')
    if authorization is None:
        _limit(guest_bucket,'ip:'+(scope.get('client') or ('unknown',))[0])
        session_id=_uuid(headers.get(b'x-session-id')) or uuid4()  # client correlation id only; no auth event for guests
        return RequestContext(request_id=rid,session_id=session_id,principal=Principal(kind='guest')),None
    scheme,_,token=authorization.decode('latin-1').partition(' ')
    token=token.strip()
    if scheme.lower()!='bearer' or not token:raise ApiError(401,'auth_invalid','Invalid session. Please sign in again.')
    claims=await run_in_threadpool(auth.verify_token,token)
    user_id,session_id=_uuid(claims['sub']),_uuid(claims['session_id'])
    if not user_id or not session_id:raise ApiError(401,'auth_invalid','Invalid session. Please sign in again.')
    _limit(customer_bucket,f'user:{user_id}')
    db=await run_in_threadpool(auth.user_db,token)
    customer_id=await run_in_threadpool(_customer_id,db,user_id)
    principal=Principal(kind='customer',user_id=user_id,customer_id=customer_id,auth_session_id=session_id,
                        aal='aal2' if claims.get('aal')=='aal2' else 'aal1',jwt=token)
    await _session_started(db,session_id)
    return RequestContext(request_id=rid,session_id=session_id,principal=principal),db


class GatewayMiddleware:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method']=='OPTIONS' or scope['path'] in SKIP:return await self.app(scope,receive,send)
        headers=dict(scope['headers'])
        try:
            size=headers.get(b'content-length')
            if size is not None and (not size.isdigit() or int(size)>settings.max_body_bytes):raise ApiError(413,'payload_too_large','The request is too large.')
            ctx,db=await build_context(scope,headers)
        except ApiError as e:
            if e.status_code>=500 or e.status_code==429:log_event('gateway_rejected',status=e.status_code,code=e.code)
            return await error_response(e)(scope,receive,send)
        state=scope.setdefault('state',{})
        state['ctx'],state['db']=ctx,db
        seen=0

        async def limited_receive():  # bodies without (or with a false) Content-Length
            nonlocal seen
            message=await receive()
            if message['type']=='http.request':
                seen+=len(message.get('body',b''))
                if seen>settings.max_body_bytes:raise ApiError(413,'payload_too_large','The request is too large.')
            return message
        token=current_ctx.set(ctx)
        try:await self.app(scope,limited_receive,send)
        finally:
            current_ctx.reset(token)
            if db is not None:auth.close_db(db)


def get_ctx(request:Request)->RequestContext:
    return request.state.ctx
