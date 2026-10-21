"""Real React -> FastAPI -> PostgREST -> PostgreSQL consent UI, without model calls.

From backend/ with Node and frontend dependencies installed:
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 TEST_BROWSER_LOCAL=1 ../.venv/bin/python -m pytest tests/test_consent_browser.py -q
Uses a local signing key and an already-issued browser session; does not test hosted password login.
"""
import os
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from tests.data_api_support import NativeAPI,mint_token
from tests.browser_support import NativeBrowserSession,run_browser
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser integration with TEST_BROWSER_LOCAL=1')


class BrowserFixture(NativeBrowserSession):
    manager: str
    granted: bool


@pytest.mark.parametrize('expiry,revoked_at,expected',[(timedelta(days=1),None,True),
    (timedelta(seconds=-1),None,False),(timedelta(days=1),datetime(2026,1,1,tzinfo=timezone.utc),False)])
def test_real_consent_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path,
    expiry: timedelta,revoked_at: datetime | None,expected: bool) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        advisor,name=db.execute("""select a.advisor_id,a.full_name from rm.advisors a join rm.assignments r using(advisor_id)
            where r.customer_id=1003 and r.ended_on is null""").fetchone()
        db.execute("""insert into consent.grants(customer_id,purpose,recipient,categories,expires_at,revoked_at)
            values (1003,'rm_brief',%s,array['profile'],clock_timestamp()+%s,%s)""",(str(advisor),expiry,revoked_at))
    case=BrowserFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),
        user_id=persona_id(1003),manager=name,granted=expected)
    run_browser(native_api_client,case,'consent status uses real',tmp_path)


def test_real_registry_failure_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('drop function public.consent_check(bigint,text,uuid,text[])')
    case=BrowserFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),
        user_id=persona_id(1003),manager='unused',granted=False)
    run_browser(native_api_client,case,'unavailable registry is an explicit',tmp_path)
