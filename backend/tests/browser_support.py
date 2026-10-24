"""Start the normal frontend/backend for native browser suites, with real local JWT/RLS services."""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from uuid import UUID
import uvicorn
from fastapi.testclient import TestClient
from pydantic import BaseModel,Field
from tests.data_api_support import available_port

class NativeBrowserSession(BaseModel):
    data_api_url: str
    access_token: str = Field(repr=False)
    user_id: UUID


def run_browser(client: TestClient,case: NativeBrowserSession,test_name: str,folder: Path,timeout: int=120) -> None:
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
    thread=threading.Thread(target=server.run,name='native-browser-api',daemon=True)
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
            cwd=frontend,env=environment,capture_output=True,text=True,timeout=timeout)
        assert result.returncode==0,f'{result.stdout}\n{result.stderr}'
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('The native test API did not stop within 10 seconds')
