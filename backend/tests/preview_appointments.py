"""Computer Use preview of real local appointment actions with David's synthetic session.

From backend/: TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1
NATIVE_PREVIEW_FILE=/private/tmp/<fresh-file>.json ../.venv/bin/python -m pytest
tests/preview_appointments.py -q -s. Start a separate Vite server using the metadata's
backend port and Data API URL, then open /api/__native_preview__/session and click
Start synthetic preview. Review appointments through Data sharing. No hosted login,
MFA, scheduling provider or AI response is simulated. Remove metadata to clean up;
the disposable preview otherwise expires after twenty minutes.
"""
import json
import os
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import jwt
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from pydantic import StrictInt,TypeAdapter
from tests.data_api_support import NativeAPI,available_port,mint_token
from tests.database_support import persona_id


def test_appointment_preview(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    destination=os.environ.get('NATIVE_PREVIEW_FILE')
    if not destination:raise ValueError('Set NATIVE_PREVIEW_FILE to a fresh temporary metadata path')
    metadata=Path(destination)
    token=mint_token(local_data_api,1003,'aal1',uuid4())
    claims=jwt.decode(token,local_data_api.signing_key.public_key(),algorithms=['ES256'],audience='authenticated')
    session={'access_token':token,'refresh_token':'native-refresh-token-not-used','token_type':'bearer','expires_in':3600,
        'expires_at':TypeAdapter(StrictInt).validate_python(claims['exp']),
        'user':{'id':str(persona_id(1003)),'aud':'authenticated','role':'authenticated','email':'native.user@example.invalid',
            'app_metadata':{'provider':'email'},'user_metadata':{},'created_at':datetime.now(timezone.utc).isoformat()}}
    bootstrap=json.dumps({'key':'sb-127-auth-token','session':session}).replace('<','\\u003c')
    preview=FastAPI()

    @preview.get('/__native_preview__/session',response_class=HTMLResponse)
    def entry() -> str:
        return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Native appointment preview</title><main><h1>Local synthetic appointment preview</h1><p>Review real local appointment holds, confirmation and cancellation. This does not test hosted password login or a real scheduling provider.</p><button id="start-preview-session" aria-label="Start synthetic preview">Start synthetic preview</button></main><script>const fixture='+bootstrap+''';
document.getElementById('start-preview-session').addEventListener('click',()=>{
  localStorage.removeItem('demobank.active.'+fixture.session.user.id);
  localStorage.setItem(fixture.key,JSON.stringify(fixture.session));location.assign('/');
});</script></html>'''

    preview.mount('/',native_api_client.app)
    port=available_port()
    server=uvicorn.Server(uvicorn.Config(preview,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,name='native-appointment-preview',daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started:
            if not thread.is_alive():raise RuntimeError('Native appointment preview stopped during startup')
            if time.monotonic()>deadline:raise TimeoutError('Native appointment preview did not start within ten seconds')
            time.sleep(0.05)
        with metadata.open('x') as output:json.dump({'backend_port':port,'data_api_url':local_data_api.url},output)
        print('Native appointment preview ready:',str(metadata),flush=True)
        deadline=time.monotonic()+1200
        while metadata.exists():
            if not thread.is_alive():raise RuntimeError('Native appointment preview stopped unexpectedly')
            if time.monotonic()>deadline:raise TimeoutError('Native appointment preview exceeded twenty minutes; remove metadata to stop it')
            time.sleep(0.1)
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('Native appointment preview did not stop within ten seconds')
