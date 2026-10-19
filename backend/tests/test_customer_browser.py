"""Normal frontend/API with native JWT, RLS and customer records; no model or password login is simulated."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser integrations')


class CustomerFixture(NativeBrowserSession):
    name: str
    account_count: int
    card_count: int
    loan_count: int
    transaction_count: int


@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_real_customer_records_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path,cid: int) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(local_data_api.database_uri) as db:
        name=db.execute('select full_name from banking.customer_profiles where customer_id=%s',(cid,)).fetchone()[0]
        counts=[db.execute(sql.SQL('select count(*) from banking.{} where customer_id=%s').format(sql.Identifier(table)),(cid,)).fetchone()[0]
            for table in ['bank_accounts','credit_cards','loans','transactions']]
    case=CustomerFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,cid,'aal1',uuid4()),
        user_id=persona_id(cid),name=name,account_count=counts[0],card_count=counts[1],loan_count=counts[2],transaction_count=counts[3])
    run_browser(native_api_client,case,'customer records use real',tmp_path)


def test_real_customer_registry_failure_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.customer_read_accounts(bigint)')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),user_id=persona_id(1001))
    run_browser(native_api_client,case,'unavailable customer records',tmp_path)
