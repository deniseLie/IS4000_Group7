"""Customer UI observes real staff Action Service receipts, card state and worker notifications."""
import asyncio
import os
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from pydantic import SecretStr
from app.services.actions import staff
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.test_notifications import drain,worker_uri
from tests.test_staff_support import block,handoff,staff_token,staff_user

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native staff/customer browser integration')


class StaffFixture(NativeBrowserSession):
    staff_command_ids: list[UUID]


def test_customer_sees_staff_receipts_and_updated_card(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr,tmp_path: Path) -> None:
    ctx,hid=handoff(local_data_api,native_api_client,'P0')
    user=staff_user(local_data_api)
    ids=[uuid4() for _ in range(4)]
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        assert (asyncio.run(staff.claim(ids[0],hid,db))).receipt.outcome=='succeeded'
        block(native_api_client,ctx,'staff-ui-unblock')
        assert (asyncio.run(staff.unblock(ids[1],hid,'card_10011',db))).receipt.outcome=='succeeded'
        block(native_api_client,ctx,'staff-ui-cancel')
        assert (asyncio.run(staff.cancel(ids[2],hid,'card_10011',db))).receipt.outcome=='succeeded'
        assert (asyncio.run(staff.close(ids[3],hid,db))).receipt.outcome=='succeeded'
    assert all(result.status=='done' for result in asyncio.run(drain(worker_uri)))
    case=StaffFixture(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id,
        staff_command_ids=ids)
    run_browser(native_api_client,case,'staff outcomes remain recorded',tmp_path)
