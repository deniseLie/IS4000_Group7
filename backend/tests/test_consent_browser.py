"""Real React -> FastAPI -> PostgREST -> PostgreSQL consent UI, without model calls.

From backend/ with Node and frontend dependencies installed:
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 TEST_BROWSER_LOCAL=1 ../.venv/bin/python -m pytest tests/test_consent_browser.py -q
Uses a local signing key and an already-issued browser session; does not test hosted password login.
"""
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import UUID,uuid4
import pytest
import uvicorn
from fastapi.testclient import TestClient
from pydantic import BaseModel,Field
from tests.data_api_support import NativeAPI,available_port,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser integration with TEST_BROWSER_LOCAL=1')


class BrowserFixture(BaseModel):
    data_api_url: str
    access_token: str = Field(repr=False)
    user_id: UUID
    manager: str
    granted: bool


def run_browser(client: TestClient,case: BrowserFixture,test_name: str,folder: Path) -> None:
    node=shutil.which('node')
    if node is None:raise FileNotFoundError('Native browser checks require Node on PATH and frontend/npm ci dependencies')
    frontend=Path(__file__).parents[2]/'frontend'
    cli=frontend/'node_modules'/'@playwright'/'test'/'cli.js'
    if not cli.is_file():raise FileNotFoundError('Run npm ci in frontend/ before native browser checks')
    fixture=folder/'browser-session.json'
    with os.fdopen(os.open(fixture,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:
        output.write(case.model_dump_json())
    port=available_port()
    server=uvicorn.Server(uvicorn.Config(client.app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,name='native-consent-api',daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started:
            if not thread.is_alive():raise RuntimeError('The native test API stopped during startup')
            if time.monotonic()>=deadline:raise TimeoutError('The native test API did not start within 10 seconds')
            time.sleep(0.05)
        environment={name:value for name,value in os.environ.items() if name in {'PATH','LANG','LC_ALL','TMPDIR'}}
        environment.update({'NATIVE_BROWSER_FIXTURE':str(fixture),'NATIVE_FRONTEND_PORT':str(available_port()),
            'NATIVE_BACKEND_PORT':str(port),'NATIVE_DATA_API_URL':case.data_api_url,'NATIVE_SCREENSHOT_DIR':str(folder)})
        result=subprocess.run([node,str(cli),'test','--config','playwright.native.config.js','--grep',test_name],
            cwd=frontend,env=environment,capture_output=True,text=True,timeout=120)
        assert result.returncode==0,f'{result.stdout}\n{result.stderr}'
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('The native test API did not stop within 10 seconds')


@pytest.mark.parametrize('expiry,revoked_at,expected',[(timedelta(days=1),None,True),
    (timedelta(seconds=-1),None,False),(timedelta(days=1),datetime(2026,1,1,tzinfo=timezone.utc),False)])
def test_real_consent_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path,
    expiry: timedelta,revoked_at: datetime | None,expected: bool) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        advisor,name=db.execute("""select a.advisor_id,a.full_name from rm.advisors a join rm.assignments r using(advisor_id)
            where r.customer_id=1003 and r.ended_on is null""").fetchone()
        db.execute("""insert into consent.grants(customer_id,purpose,recipient,categories,expires_at,revoked_at)
            values (1003,'rm_brief',%s,array['profile'],clock_timestamp()+%s,%s)""",(str(advisor),expiry,revoked_at))
    case=BrowserFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),
        user_id=persona_id(1003),manager=name,granted=expected)
    run_browser(native_api_client,case,'consent status uses real',tmp_path)


def test_real_registry_failure_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('drop function public.consent_check(bigint,text,uuid,text[])')
    case=BrowserFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),
        user_id=persona_id(1003),manager='unused',granted=False)
    run_browser(native_api_client,case,'unavailable registry is an explicit',tmp_path)
