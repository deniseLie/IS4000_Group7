"""Real appointment form, Action Service review and PostgreSQL state at four viewport sizes."""
import os
from datetime import date
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id
from tests.test_relationship_api import monday

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native appointment browser integration')


class AppointmentFixture(NativeBrowserSession):
    appointment_date: date


def test_real_appointment_form_and_action_review(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    case=AppointmentFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1003,'aal1',uuid4()),
        user_id=persona_id(1003),appointment_date=monday())
    run_browser(native_api_client,case,'appointment form holds confirms and cancels',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select status,count(*) from rm.appointments group by status").fetchall()==[('cancelled',4)]
        assert db.execute('select count(*) from actions.receipts').fetchone()==(12,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
