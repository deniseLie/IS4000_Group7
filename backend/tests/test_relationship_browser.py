"""Real React/FastAPI/JWT/PostgREST relationship UI checks. No model or password login is simulated."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in with TEST_BROWSER_LOCAL=1 and both database/API flags')


class RelationshipFixture(NativeBrowserSession):
    request_count: int
    expects_slots: bool


@pytest.mark.parametrize('cid',[1001,1003])
def test_real_relationship_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path,cid: int) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        count=db.execute('select count(*) from rm.requests where customer_id=%s',(cid,)).fetchone()[0]
    case=RelationshipFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,cid,'aal1',uuid4()),
        user_id=persona_id(cid),request_count=count,expects_slots=cid==1003)
    run_browser(native_api_client,case,'relationship records use real',tmp_path)


def test_real_relationship_registry_error_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.appointments_find_slots(bigint,date,date,int)')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'unavailable appointment registry',tmp_path)
