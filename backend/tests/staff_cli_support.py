"""Loopback /rest/v1 routing adapter to real native PostgREST for the unmodified production staff CLI."""
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
import httpx
import uvicorn
from fastapi import FastAPI,Request,Response
from tests.data_api_support import NativeAPI,available_port


@contextmanager
def cli_gateway(api: NativeAPI) -> Iterator[str]:
    app=FastAPI()

    @app.post('/rest/v1/rpc/{rpc}')
    async def forward(rpc: str,request: Request) -> Response:
        headers={key:request.headers[key] for key in ['authorization','apikey','x-request-id'] if key in request.headers}
        headers['Content-Type']='application/json'
        async with httpx.AsyncClient(timeout=10) as client:
            response=await client.post(api.url+'/rpc/'+rpc,content=await request.body(),headers=headers)
        return Response(response.content,status_code=response.status_code,media_type='application/json')

    port=available_port()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,name='native-staff-cli-routing',daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started:
            if not thread.is_alive():raise RuntimeError('Native CLI routing adapter stopped during startup')
            if time.monotonic()>deadline:raise TimeoutError('Native CLI routing adapter did not start within ten seconds')
            time.sleep(0.05)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('Native CLI routing adapter did not stop within ten seconds')
