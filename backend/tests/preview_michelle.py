"""Computer Use preview with real native JWT/RLS and disposable file storage.

TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 TEST_MICHELLE_TRANSPORT=codex NATIVE_PREVIEW_FILE=/tmp/fresh.json
../.venv/bin/python -m pytest tests/preview_michelle.py -q -s
Open the printed local URL and click Start synthetic preview. Hosted Auth/Storage are not exercised; the local file adapter tests our JWT/RLS
and real-byte boundary. No AI is substituted. Remove metadata to stop; expires in 20 min.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import date,datetime,timezone
from pathlib import Path
from uuid import uuid4
import jwt
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import SecretStr
from app.core.config import settings
from tests.data_api_support import available_port,mint_token
from tests.database_support import persona_id
from tests.storage_support import local_storage,template_pdf


def test_michelle_preview(local_data_api,native_api_client,tmp_path,monkeypatch):
    import psycopg
    metadata=Path(os.environ['NATIVE_PREVIEW_FILE'])
    if os.getenv('TEST_MICHELLE_TRANSPORT')=='codex':monkeypatch.setenv('CHAT_TRANSPORT','codex')
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-preview-michelle-private-checkpoint-key')
    with psycopg.connect(local_data_api.database_uri) as db:
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key))
    for name,extra in [('Missing address',[]),('Complete address',['Address: Fictional Lane'])]:
        (tmp_path/(name+' DEMO.pdf')).write_bytes(template_pdf(['Utility bill','Full name: Synthetic Demo','Document date: '+date.today().isoformat(),*extra]))
    with local_storage(local_data_api,tmp_path/'blobs') as storage:
        monkeypatch.setenv('SUPABASE_URL',storage);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        if os.getenv('NATIVE_PREVIEW_EXPIRED')=='1':
            import httpx
            from app.schemas.contracts import ActionCommand
            from tests.test_actions_api import context,headers,owned_case,propose
            ctx=owned_case(local_data_api,context(local_data_api,1002))
            payload=(tmp_path/'Complete address DEMO.pdf').read_bytes();document_id=str(uuid4())
            target={'document_id':document_id,'case_id':'00000000-0000-4000-8000-100200000001',
                'item_id':'00000000-0000-4000-8000-100200000011','file_name':'Freshness DEMO.pdf',
                'mime_type':'application/pdf','size_bytes':len(payload)}
            upload=httpx.post(storage+f"/storage/v1/object/demobank-compliance-private/1002/{target['case_id']}/{document_id}",headers={**headers(ctx),'Content-Type':'application/pdf'},content=payload)
            assert upload.status_code==200
            propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:preview:document.register',target=target))
            check=native_api_client.post(f'/documents/{document_id}/precheck',headers=headers(ctx));assert check.status_code==200 and check.json()['data']['status']=='no_issues'
            with psycopg.connect(local_data_api.database_uri) as db:
                db.execute("update compliance.prechecks set report=jsonb_set(jsonb_set(report,'{checked_at}',to_jsonb(clock_timestamp()-interval '2 days')),'{expires_at}',to_jsonb(clock_timestamp()-interval '1 day')) where document_id=%s",(document_id,))
        token=mint_token(local_data_api,1002,'aal1',uuid4());claims=jwt.decode(token,local_data_api.signing_key.public_key(),algorithms=['ES256'],audience='authenticated')
        session={'access_token':token,'refresh_token':'unused-native-refresh','token_type':'bearer','expires_in':3600,'expires_at':claims['exp'],
          'user':{'id':str(persona_id(1002)),'aud':'authenticated','role':'authenticated','email':'michelle@example.invalid','app_metadata':{'provider':'email'},'user_metadata':{},'created_at':datetime.now(timezone.utc).isoformat()}}
        bootstrap=json.dumps({'key':'sb-127-auth-token','session':session}).replace('<','\\u003c')
        preview=FastAPI()
        @preview.get('/__native_preview__/session',response_class=HTMLResponse)
        def entry():
            return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Michelle preview</title><h1>Local synthetic Michelle preview</h1><p>Disposable native services with actual file bytes. Hosted password login is not tested here.</p><button id="start">Start synthetic preview</button><script>const fixture='+bootstrap+";document.getElementById('start').onclick=()=>{localStorage.setItem(fixture.key,JSON.stringify(fixture.session));location.assign('/');};</script></html>"
        preview.mount('/',native_api_client.app)
        port=available_port();front_port=available_port()
        server=uvicorn.Server(uvicorn.Config(preview,host='127.0.0.1',port=port,log_level='warning',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','TMPDIR'}}
        env.update({'BACKEND_PORT':str(port),'VITE_API_URL':'/api','VITE_SUPABASE_URL':storage,'VITE_SUPABASE_ANON_KEY':'native-public-test-key'})
        frontend=Path(__file__).parents[2]/'frontend'
        with (tmp_path/'vite.log').open('w') as log:
            process=subprocess.Popen([shutil.which('node'),str(frontend/'node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',str(front_port),'--strictPort'],cwd=frontend,env=env,stdout=log,stderr=log)
            try:
                with metadata.open('x') as output:json.dump({'url':f'http://127.0.0.1:{front_port}/api/__native_preview__/session','fixtures':str(tmp_path)},output)
                print('Michelle preview ready:',str(metadata),flush=True)
                deadline=time.monotonic()+1200
                while metadata.exists() and time.monotonic()<deadline:
                    if process.poll() is not None or not thread.is_alive():raise RuntimeError('Preview service stopped')
                    time.sleep(.1)
            finally:
                process.terminate();process.wait(timeout=10);server.should_exit=True;thread.join(timeout=10)
