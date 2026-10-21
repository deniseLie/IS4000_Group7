"""Real consent form, scoped action review and persisted grant/revocation at four screen sizes."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id
from tests.test_consent_actions import context,headers,recipient

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native consent-action browser integration')


def test_real_consent_action_review(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'consent form requests grants and revokes',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*),count(revoked_at) from consent.grants').fetchone()==(4,4)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(12,)
        assert db.execute('select count(*) from rm.shared_summaries').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_consent_registry_failure_is_explicit(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("insert into consent.grants(customer_id,purpose,recipient,categories) values(1003,'rm_brief',%s,array['profile'])",(recipient(local_data_api),))
        db.execute('drop function public.customer_read_consent_records(bigint)')
    status=native_api_client.get('/consent/rm',headers=headers(context(local_data_api,1003)))
    assert status.status_code==200 and status.json()['consent']['granted']
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),user_id=persona_id(1003))
    run_browser(native_api_client,case,'failed consent records do not display',tmp_path)
