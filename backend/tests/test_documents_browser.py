"""Normal records UI over real owned SQL metadata; does not fabricate a Storage object or invoke AI."""
import os
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id
from tests.test_documents import seed_metadata

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser integration')


class DocumentFixture(NativeBrowserSession):
    document_ids: list[UUID]


def test_owned_document_metadata_ui(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    import psycopg
    ids=seed_metadata(local_data_api)
    case=DocumentFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),
        user_id=persona_id(1002),document_ids=ids[1002])
    run_browser(native_api_client,case,'owned document metadata stays scoped',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where customer_id=1002 and action='data.read' and details->>'tool'='documents.list'").fetchone()[0]>=2
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
