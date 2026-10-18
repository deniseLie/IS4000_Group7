"""API Gateway Layer (F4). Tokens are minted with a local ES256 key; only the JWKS download is faked, so the real
PyJWKClient caching and PyJWT checks run. Database calls are replaced by small fakes (no Supabase)."""
import io
import json
import time
import urllib.error
from uuid import UUID, uuid4
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from app.api import chat as chat_api
from app.core import auth, gateway
from app.core.ratelimit import TokenBucket
from app.main import app
from app.services import audit

KEY,OTHER=ec.generate_private_key(ec.SECP256R1()),ec.generate_private_key(ec.SECP256R1())
JWKS={'keys':[{**jwt.algorithms.ECAlgorithm.to_jwk(KEY.public_key(),as_dict=True),'kid':'k1','alg':'ES256','use':'sig'}]}
USER,SESSION=uuid4(),uuid4()
client=TestClient(app)

def token(key=KEY,kid='k1',alg='ES256',exp=3600,**claims):
    body={'sub':str(USER),'aud':'authenticated','role':'authenticated','session_id':str(SESSION),'aal':'aal1','exp':int(time.time())+exp,**claims}
    return jwt.encode({k:v for k,v in body.items() if v is not None},key,algorithm=alg,headers={'kid':kid})
def bearer(t=None,**kw):return {'Authorization':f'Bearer {t or token(**kw)}'}
def chat(q='hello',**kw):return {'messages':[{'role':'user','content':q}],**kw}

class Db:
    def __init__(self):self.rpcs=[]
    def rpc(self,name,params):
        self.rpcs.append((name,params))
        return self
    def execute(self):return None

@pytest.fixture(autouse=True)
def env(monkeypatch):
    fetches=[]
    def urlopen(request,*a,**k):
        fetches.append(request)
        if env.down:raise urllib.error.URLError('down')
        return io.BytesIO(json.dumps(JWKS).encode())
    env.down=False
    monkeypatch.setenv('SUPABASE_URL','https://demo.supabase.test')
    monkeypatch.setattr(jwt.jwks_client.urllib.request,'build_opener',lambda *h:type('Opener',(),{'open':lambda self,r,timeout=None:urlopen(r)})())
    monkeypatch.setattr(auth,'_jwks',None)
    db=Db()
    monkeypatch.setattr(auth,'user_db',lambda t:db)
    monkeypatch.setattr(auth,'resolve_customer_id',lambda d,uid:1001 if uid==str(USER) else None)
    gateway._customers.clear();gateway._sessions.clear()
    env.fetches,env.db=fetches,db
    yield env

def ctx_of(monkeypatch):
    """Capture the RequestContext and customer_id the chat route works with."""
    seen={}
    def context(db,customer_id):
        seen['customer_id']=customer_id
        return 'records'
    monkeypatch.setattr(chat_api,'get_customer_context',context)
    real=chat_api.start_batch
    def batch(db,session_id=None):
        seen['session_id']=session_id
        return real(db,session_id=session_id)
    monkeypatch.setattr(chat_api,'start_batch',batch)
    return seen

def code(r):return r.json()['error']['code']

def test_valid_token_builds_customer_context_and_jwks_is_cached(env,monkeypatch):
    seen=ctx_of(monkeypatch)
    for _ in range(3):assert client.post('/chat',json=chat(mode='personal'),headers=bearer()).status_code==200
    assert seen=={'customer_id':1001,'session_id':SESSION} and len(env.fetches)==1

@pytest.mark.parametrize('kw,expected',[
    ({'exp':-60},'auth_expired'),
    ({'aud':'anon'},'auth_invalid'),
    ({'role':'anon'},'auth_invalid'),
    ({'key':OTHER},'auth_invalid'),          # right kid, wrong signature
    ({'kid':'unknown'},'auth_invalid'),      # refetched once, still unknown
    ({'session_id':None},'auth_invalid'),
    ({'sub':'not-a-uuid'},'auth_invalid'),
])
def test_bad_tokens_get_401_with_stable_codes(kw,expected):
    r=client.post('/chat',json=chat(),headers=bearer(**kw))
    assert r.status_code==401 and code(r)==expected and UUID(r.json()['error']['request_id'])

@pytest.mark.parametrize('header',['Bearer not.a.jwt','Bearer ','Basic abc',
    'Bearer '+jwt.encode({'sub':str(USER),'aud':'authenticated'},None,algorithm='none'),
    'Bearer '+jwt.encode({'sub':str(USER),'aud':'authenticated','exp':int(time.time())+60},'shared-secret-for-test-only-0000',algorithm='HS256')])
def test_malformed_unsigned_and_shared_secret_tokens_are_rejected(header):
    r=client.post('/chat',json=chat(),headers={'Authorization':header})
    assert r.status_code==401 and code(r)=='auth_invalid'

def test_jwks_outage_is_503_not_guest(env):
    env.down=True
    r=client.post('/chat',json=chat(),headers=bearer())
    assert r.status_code==503 and code(r)=='auth_unavailable'

def test_guest_gets_guest_principal_and_personal_mode_needs_sign_in(monkeypatch):
    seen=ctx_of(monkeypatch)
    sid=uuid4()
    assert client.post('/chat',json=chat(),headers={'X-Session-Id':str(sid)}).status_code==200
    assert seen['session_id']==sid
    r=client.post('/chat',json=chat(mode='personal'))
    assert r.status_code==401 and code(r)=='auth_required' and 'customer_id' not in seen

def test_customer_id_never_comes_from_the_body(monkeypatch):
    seen=ctx_of(monkeypatch)
    r=client.post('/chat',json=chat(mode='personal',customer_id=1002,user_id=str(uuid4()),principal={'kind':'customer','customer_id':1003}),headers=bearer())
    assert r.status_code==200 and seen['customer_id']==1001

def test_login_without_profile_cannot_use_personal_mode():
    r=client.post('/chat',json=chat(mode='personal'),headers=bearer(sub=str(uuid4())))
    assert r.status_code==403 and code(r)=='profile_missing'

def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    now=[0.0]
    monkeypatch.setattr(gateway,'customer_bucket',TokenBucket(2,0.5,clock=lambda:now[0]))
    h=bearer()
    assert [client.get('/knowledge/accounts',headers=h).status_code for _ in range(3)]==[200,200,429]
    r=client.get('/knowledge/accounts',headers=h)
    assert code(r)=='rate_limited' and r.headers['retry-after']=='2'
    now[0]+=2
    assert client.get('/knowledge/accounts',headers=h).status_code==200

def test_rate_limit_is_per_user_and_guests_are_separate(monkeypatch):
    monkeypatch.setattr(gateway,'customer_bucket',TokenBucket(1,0.001))
    monkeypatch.setattr(gateway,'guest_bucket',TokenBucket(1,0.001))
    assert client.get('/knowledge/accounts',headers=bearer()).status_code==200
    assert client.get('/knowledge/accounts',headers=bearer()).status_code==429
    assert client.get('/knowledge/accounts').status_code==200
    assert client.get('/knowledge/accounts').status_code==429

def test_payload_limit_with_and_without_content_length():
    big=json.dumps({'messages':[{'role':'user','content':'x'*3999}]*20}).encode()
    r=client.post('/chat',content=big,headers={'Content-Type':'application/json'})
    assert r.status_code==413 and code(r)=='payload_too_large'
    r=client.post('/chat',content=iter([big[:40000],big[40000:]]),headers={'Content-Type':'application/json'})
    assert r.status_code==413 and code(r)=='payload_too_large'

def test_session_started_recorded_once_per_session(env,monkeypatch):
    monkeypatch.setattr(audit,'enabled',lambda:True)
    rid=str(uuid4())
    for _ in range(3):client.get('/knowledge/accounts',headers={**bearer(),'X-Request-Id':rid})
    assert env.db.rpcs==[('audit_session_started',{'p_request_id':rid})]
    client.get('/knowledge/accounts',headers=bearer(session_id=str(uuid4())))
    assert len(env.db.rpcs)==2

def test_failed_session_audit_fails_the_request(env,monkeypatch):
    monkeypatch.setattr(audit,'enabled',lambda:True)
    def broken(*a):raise RuntimeError('down')
    monkeypatch.setattr(env.db,'rpc',broken)
    r=client.get('/knowledge/accounts',headers=bearer())
    assert r.status_code==503 and code(r)=='audit_unavailable' and not gateway._sessions

def test_session_end_records_event_with_request_and_session_ids(monkeypatch):
    writes=[]
    monkeypatch.setattr(audit,'enabled',lambda:True)
    monkeypatch.setattr(audit,'_write',lambda db,payload:writes.append(payload))
    rid=str(uuid4())
    r=client.post('/auth/session-end',headers={**bearer(),'X-Request-Id':rid})
    assert r.status_code==204 and r.headers['x-request-id']==rid
    assert writes==[[{'category':'auth','action':'auth.session_ended','outcome':'success','details':{'reason':'sign_out'},'session_id':str(SESSION),'request_id':rid}]]

def test_session_end_needs_a_customer():
    r=client.post('/auth/session-end')
    assert r.status_code==401 and code(r)=='auth_required'

def test_non_uuid_request_id_is_replaced():
    r=client.get('/knowledge/accounts',headers={'X-Request-Id':'abc'})
    assert r.headers['x-request-id']!='abc' and UUID(r.headers['x-request-id'])

def test_health_and_preflight_skip_the_gateway(env):
    assert client.get('/health',headers={'Authorization':'Bearer junk'}).status_code==200
    r=client.options('/chat',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST','Authorization':'Bearer junk'})
    assert r.status_code==200 and not env.fetches

def test_errors_keep_cors_headers():
    r=client.post('/chat',json=chat(),headers={'Origin':'http://localhost:5173','Authorization':'Bearer junk'})
    assert r.status_code==401 and r.headers['access-control-allow-origin']=='http://localhost:5173'
