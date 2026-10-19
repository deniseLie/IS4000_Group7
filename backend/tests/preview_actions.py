"""Manual Computer Use of the normal action UI with a pre-issued local synthetic-customer session.

Run like preview_native.py, using a fresh NATIVE_PREVIEW_FILE and the database/API opt-ins.
Start a separate Vite process with its backend port and native Data API URL. Open
http://127.0.0.1:<vite-port>/api/__native_preview__/session and click Start synthetic preview.
That test-only page installs a native JWT session, then opens the normal frontend. This tests
real JWT verification, RLS and action transactions; it does not test hosted password login.
The start button creates a real paused graph with a fresh five-minute proposal. The fixture
starts after dispatch with safety-origin text; it tests workflow mechanics, not AI accuracy.
Metadata contains URLs only. Remove its file to stop and delete the temporary database.
"""
import json
import os
import secrets
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
import jwt
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from pydantic import StrictInt,TypeAdapter
from tests.data_api_support import NativeAPI,available_port
from app.core.config import settings
from tests.test_actions_api import context
from tests.test_workflows import paused


def test_action_preview(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    destination=os.environ.get('NATIVE_PREVIEW_FILE')
    if not destination:raise ValueError('Set NATIVE_PREVIEW_FILE to a new temporary metadata file')
    metadata=Path(destination)
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    ctx=context(local_data_api,1001)
    token=ctx.principal.jwt.get_secret_value()
    claims=jwt.decode(token,local_data_api.signing_key.public_key(),algorithms=['ES256'],audience='authenticated')
    session={'access_token':token,'refresh_token':'native-refresh-token-not-used','token_type':'bearer','expires_in':3600,
        'expires_at':TypeAdapter(StrictInt).validate_python(claims['exp']),
        'user':{'id':str(ctx.principal.user_id),'aud':'authenticated','role':'authenticated','email':'native.user@example.invalid',
            'app_metadata':{'provider':'email'},'user_metadata':{},'created_at':datetime.now(timezone.utc).isoformat()}}
    bootstrap=json.dumps({'key':'sb-127-auth-token','session':session}).replace('<','\\u003c')
    preview=FastAPI()

    @preview.get('/__native_preview__/session',response_class=HTMLResponse)
    def entry() -> str:
        return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Native action preview</title><main><h1>Local synthetic customer preview</h1><p>This pre-issued local test session verifies JWT/RLS and real action transactions. Hosted password login is not being tested. No real customer records or bank cards are used.</p><button id="start-preview-session" aria-label="Start synthetic preview">Start synthetic preview</button><p id="status" role="status"></p></main><script>const fixture='+bootstrap+''';
document.getElementById('start-preview-session').addEventListener('click',async()=>{
  const button=document.getElementById('start-preview-session');button.disabled=true;
  const status=document.getElementById('status');status.textContent='Creating native action proposals…';
  try {
    const response=await fetch('/api/__native_preview__/start',{method:'POST'});
    if(!response.ok)throw new Error('Native proposal setup failed: HTTP '+response.status);
    const access=await response.json();
    localStorage.setItem('demobank.active.'+fixture.session.user.id,JSON.stringify({session_id:access.session_id,guest_token:null}));
    localStorage.setItem(fixture.key,JSON.stringify(fixture.session));location.assign('/');
  } catch(error) {status.textContent=error.message;button.disabled=false;}
});</script></html>'''

    @preview.post('/__native_preview__/start')
    def start() -> dict[str,str]:
        owned,_=paused(local_data_api,ctx)
        return {'session_id':str(owned.access.session_id)}

    preview.mount('/',native_api_client.app)
    port=available_port()
    server=uvicorn.Server(uvicorn.Config(preview,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,name='native-action-preview',daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started:
            if not thread.is_alive():raise RuntimeError('The native action preview stopped during startup')
            if time.monotonic()>=deadline:raise TimeoutError('The native action preview did not start within ten seconds')
            time.sleep(0.05)
        with metadata.open('x') as output:json.dump({'backend_port':port,'data_api_url':local_data_api.url},output)
        print('Native action preview ready:',str(metadata),flush=True)
        deadline=time.monotonic()+900
        while metadata.exists():
            if not thread.is_alive():raise RuntimeError('The native action preview stopped unexpectedly')
            if time.monotonic()>=deadline:raise TimeoutError('Remove the preview metadata file; the fifteen-minute preview has expired')
            time.sleep(0.1)
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('The native action preview did not stop within ten seconds')
