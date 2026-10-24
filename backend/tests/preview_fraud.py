"""Manual real-model Fraud review over disposable PostgreSQL/PostgREST and the normal UI.

From backend/: TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1
NATIVE_PREVIEW_FILE=/private/tmp/<fresh-file>.json ../.venv/bin/python -m pytest
tests/preview_fraud.py -q -s. Start a separate Vite instance with the metadata's
backend port and Data API URL, then open /api/__native_preview__/session and click
Start synthetic preview. Ask about unfamiliar txn_100111 on card ending 4821.
The configured real provider receives fictional masked card/transaction evidence;
no model response or workflow is pre-filled. Hosted password login is not tested.
Remove the metadata file to stop and clean up the temporary database.
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
from app.core.config import settings
from tests.data_api_support import NativeAPI,available_port
from tests.test_actions_api import context


def test_fraud_preview(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    destination=os.environ.get('NATIVE_PREVIEW_FILE')
    if not destination:raise ValueError('Set NATIVE_PREVIEW_FILE to a fresh temporary metadata path')
    metadata=Path(destination)
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
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
        return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Native Fraud preview</title><main><h1>Local fictional Fraud preview</h1><p>The real configured model and normal application use disposable customer records. No hosted bank card is affected. Hosted password login is not tested.</p><button id="start-preview-session" aria-label="Start synthetic preview">Start synthetic preview</button></main><script>const fixture='+bootstrap+''';
document.getElementById('start-preview-session').addEventListener('click',()=>{
  localStorage.removeItem('demobank.active.'+fixture.session.user.id);
  localStorage.setItem(fixture.key,JSON.stringify(fixture.session));location.assign('/');
});</script></html>'''

    preview.mount('/',native_api_client.app)
    port=available_port()
    server=uvicorn.Server(uvicorn.Config(preview,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,name='native-fraud-preview',daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started:
            if not thread.is_alive():raise RuntimeError('The native Fraud preview stopped during startup')
            if time.monotonic()>=deadline:raise TimeoutError('The native Fraud preview did not start within ten seconds')
            time.sleep(0.05)
        with metadata.open('x') as output:json.dump({'backend_port':port,'data_api_url':local_data_api.url},output)
        print('Native Fraud preview ready:',str(metadata),flush=True)
        deadline=time.monotonic()+900
        while metadata.exists():
            if not thread.is_alive():raise RuntimeError('The native Fraud preview stopped unexpectedly')
            if time.monotonic()>=deadline:raise TimeoutError('Remove the preview metadata file; the fifteen-minute preview has expired')
            time.sleep(0.1)
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('The native Fraud preview did not stop within ten seconds')
