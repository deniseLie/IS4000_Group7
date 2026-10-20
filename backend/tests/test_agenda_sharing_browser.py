"""The normal sharing UI with real local SQL/JWT/HTTP; no model or service replacement."""
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.database_support import persona_id
from tests.test_agendas import agenda_target,save
from tests.test_agenda_sharing import grant
from tests.test_consent_actions import action_case

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser integration')


@pytest.mark.parametrize('scenario',['snapshots','consent','changed'])
def test_sharing_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path,scenario: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    save(client,ctx,agenda_target(api),'draft')
    if scenario!='consent':grant(client,api,ctx,['agenda','profile'],'consent')
    case=NativeBrowserSession(data_api_url=api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=persona_id(1003))
    names={'snapshots':'agenda sharing reviews exact snapshots','consent':'agenda sharing requires one grant','changed':'agenda edit after review'}
    run_browser(client,case,names[scenario],tmp_path)
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.shared_summaries').fetchone()==(4 if scenario=='snapshots' else 0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
