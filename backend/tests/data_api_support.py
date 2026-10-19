"""Real native PostgREST over a fresh database clone for each HTTP integration test.

Install the project-local binary with python -m scripts.install_postgrest from backend/.
Opt in with TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1. No Auth/password server or model
is simulated: tokens use a local signing key and both PostgREST and the API verify them.
"""
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING,NamedTuple
from urllib.parse import urlsplit,urlunsplit
from uuid import UUID,uuid4
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey,SECP256R1,generate_private_key
from pydantic import JsonValue,TypeAdapter
from scripts.install_postgrest import binary_path
from tests.database_support import persona_id

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

logger=logging.getLogger(__name__)


class NativeAPI(NamedTuple):
    url: str
    database_uri: str
    signing_key: EllipticCurvePrivateKey


def mint_token(api: NativeAPI, customer_id: int, aal: str, session_id: UUID) -> str:
    return jwt.encode({'sub':str(persona_id(customer_id)),'role':'authenticated','aud':'authenticated',
        'session_id':str(session_id),'aal':aal,'exp':int(time.time())+3600},api.signing_key,
        algorithm='ES256',headers={'kid':'native-test-key'})


def available_port() -> int:
    with socket.socket() as channel:
        channel.bind(('127.0.0.1',0))
        return channel.getsockname()[1]


def wait_for_api(process: subprocess.Popen[bytes], url: str, log_path: Path) -> None:
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError(f'Native PostgREST exited during startup: exit code {process.returncode}; log: {log_path.read_text()}')
        try:
            response=httpx.get(url,timeout=0.5)
            if response.status_code==200:return
            logger.warning('native_data_api_starting',extra={'status':response.status_code})
        except (httpx.ConnectError,httpx.ReadTimeout) as error:
            logger.debug('native_data_api_starting',extra={'error_type':type(error).__name__})
        time.sleep(0.1)
    raise TimeoutError(f'Native PostgREST did not become ready within 15 seconds at {url}; log: {log_path.read_text()}')


@pytest.fixture
def local_data_api(local_postgres: str,tmp_path: Path) -> Iterator[NativeAPI]:
    if os.getenv('TEST_DATA_API_LOCAL')!='1':pytest.skip('Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')
    import psycopg
    from psycopg import sql
    executable=binary_path()
    if not executable.is_file():raise FileNotFoundError(f'Install native PostgREST first: ../.venv/bin/python -m scripts.install_postgrest (expected {executable})')
    name='api_'+uuid4().hex
    parts=urlsplit(local_postgres)
    uri=urlunsplit(parts._replace(path='/'+name))
    key=generate_private_key(SECP256R1())
    public=TypeAdapter(dict[str,JsonValue]).validate_python(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(),as_dict=True))
    jwks={'keys':[{**public,'kid':'native-test-key','alg':'ES256','use':'sig'}]}
    with psycopg.connect(local_postgres,autocommit=True) as admin:
        admin.execute(sql.SQL('create database {} with template postgres').format(sql.Identifier(name)))
    try:
        with psycopg.connect(uri,autocommit=True) as db:
            db.execute(sql.SQL("create function public.native_test_jwks() returns jsonb language sql stable security invoker set search_path='' as $$ select {}::jsonb $$")
                .format(sql.Literal(json.dumps(jwks))))
            db.execute('revoke all on function public.native_test_jwks() from public')
            db.execute('grant execute on function public.native_test_jwks() to anon,authenticated')
        port=available_port()
        url=f'http://127.0.0.1:{port}'
        environment={name:value for name,value in os.environ.items() if name in {'PATH','LANG','LC_ALL','TMPDIR'}}
        if sys.platform=='darwin':
            import psycopg_binary
            # The upstream macOS binary links libpq dynamically; use the project wheel's bundled library.
            environment['DYLD_LIBRARY_PATH']=str(Path(psycopg_binary.__file__).parent/'.dylibs')
        environment.update({'PGRST_DB_URI':uri,'PGRST_DB_ANON_ROLE':'anon','PGRST_DB_SCHEMAS':'public,banking,compliance,rm,consent,conversation,actions,support,notify,kb',
            'PGRST_JWT_SECRET':json.dumps(jwks),'PGRST_JWT_AUD':'authenticated','PGRST_SERVER_HOST':'127.0.0.1',
            'PGRST_SERVER_PORT':str(port),'PGRST_DB_POOL':'2','PGRST_LOG_LEVEL':'crit','PGRST_DB_CHANNEL_ENABLED':'false'})
        log_path=tmp_path/'postgrest.log'
        with log_path.open('w') as log:
            process=subprocess.Popen([str(executable)],env=environment,stdout=log,stderr=subprocess.STDOUT)
            try:
                wait_for_api(process,url,log_path)
                yield NativeAPI(url,uri,key)
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait(timeout=5)
                    raise RuntimeError('Native PostgREST required forced shutdown; inspect the test process lifecycle') from error
    finally:
        with psycopg.connect(local_postgres,autocommit=True) as admin:
            admin.execute(sql.SQL('drop database {}').format(sql.Identifier(name)))


@pytest.fixture
def native_api_client(local_data_api: NativeAPI,monkeypatch: pytest.MonkeyPatch) -> Iterator['TestClient']:
    """Normal FastAPI routes and real SDK calls, configured for the temporary native API/JWKS."""
    from fastapi.testclient import TestClient
    from supabase import Client,create_client
    from app.core import auth,gateway,knowledge_db
    from app.schemas.contracts import RequestContext
    from postgrest import SyncPostgrestClient
    from app.main import app
    from app.services import audit
    def native_user_db(token: str) -> Client:
        db=create_client(local_data_api.url,'native-public-test-key')
        # Supabase adds /rest/v1; native PostgREST serves the same API at /.
        db.rest_url=db.supabase_url
        db.postgrest.auth(token)
        return db
    monkeypatch.setattr(auth,'_jwks',jwt.PyJWKClient(local_data_api.url+'/rpc/native_test_jwks'))
    monkeypatch.setattr(auth,'user_db',native_user_db)
    def native_knowledge_db(ctx: RequestContext) -> SyncPostgrestClient:
        db=SyncPostgrestClient(local_data_api.url)
        if ctx.principal.jwt is not None:db.auth(ctx.principal.jwt.get_secret_value())
        return db
    monkeypatch.setattr(knowledge_db,'connect',native_knowledge_db)
    monkeypatch.setattr(audit,'enabled',lambda:True)
    gateway._customers.clear()
    with SyncPostgrestClient(local_data_api.url) as public_db:
        monkeypatch.setattr(audit,'_anon',public_db)
        with TestClient(app) as client:yield client
