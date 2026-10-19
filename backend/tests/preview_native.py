"""Manual Computer Use preview of the normal app against temporary native services.

From backend/: TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 NATIVE_PREVIEW_FILE=/private/tmp/<new-file>.json
../.venv/bin/python -m pytest tests/preview_native.py -q -s
The metadata file contains only local URLs. Remove it to stop and clean up the temporary database.
Use a separate Vite instance with VITE_API_URL=/api, BACKEND_PORT from the file and its native Data API URL.
No Auth/password service or model response is simulated. Model configuration is intentionally absent;
manual checks use fixed safety paths. The preview ends with an error after fifteen minutes.
"""
import json
import os
import threading
import time
from pathlib import Path
import pytest
import uvicorn
from fastapi.testclient import TestClient
from app.core.config import settings
from tests.data_api_support import NativeAPI,available_port


def test_native_preview(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    destination=os.environ.get('NATIVE_PREVIEW_FILE')
    if not destination:raise ValueError('Set NATIVE_PREVIEW_FILE to a new temporary metadata file before starting the manual preview')
    metadata=Path(destination)
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v1')
    monkeypatch.delenv('LLM_MODEL',raising=False)
    port=available_port()
    server=uvicorn.Server(uvicorn.Config(native_api_client.app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,name='native-manual-preview',daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started:
            if not thread.is_alive():raise RuntimeError('The native preview API stopped during startup')
            if time.monotonic()>=deadline:raise TimeoutError('The native preview API did not start within ten seconds')
            time.sleep(0.05)
        with metadata.open('x') as output:
            json.dump({'backend_port':port,'backend_url':f'http://127.0.0.1:{port}','data_api_url':local_data_api.url},output)
        print('Native preview ready:',str(metadata),flush=True)
        deadline=time.monotonic()+900
        while metadata.exists():
            if not thread.is_alive():raise RuntimeError('The native preview API stopped unexpectedly')
            if time.monotonic()>=deadline:raise TimeoutError('The manual native preview exceeded fifteen minutes; remove its metadata file to stop')
            time.sleep(0.1)
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        if thread.is_alive():raise TimeoutError('The native preview API did not stop within ten seconds')
