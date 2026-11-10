"""Client compliance handover → RM reviewed draft → client update over real native JWT/RLS and Codex.

Opt in with TEST_BROWSER_LOCAL=1 TEST_RM_LIVE=1 plus both native database/API flags.
"""
import os
import time
from pathlib import Path
from uuid import UUID,uuid4
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import Field,SecretStr
from app.core.config import settings
from app.services import prompts
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import SOPHIE_LOGIN,persona_id
from tests.test_notifications import worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1' or os.getenv('TEST_RM_LIVE')!='1',reason='Opt in to real RM browser/model integration')

class FollowupSession(NativeBrowserSession):
    rm_token: str=Field(repr=False)
    rm_user_id: UUID
    rm_display_name: str


@pytest.mark.parametrize('advisor_name',['Sophie Lim (DEMO)','Jordan Tan (DEMO)'])
def test_client_compliance_handover_rm_review_and_client_reply(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path,worker_uri: SecretStr,advisor_name: str) -> None:
    import psycopg
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'run_worker_in_api',True);monkeypatch.setattr(settings,'worker_database_url',worker_uri)
    monkeypatch.setattr(settings,'worker_poll_seconds',.2)
    token=jwt.encode({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aud':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'exp':int(time.time())+3600,
        'app_metadata':{'demobank_role':'rm','rm_id':'00000000-0000-4000-8000-100200000002'}},
        local_data_api.signing_key,algorithm='ES256',headers={'kid':'native-test-key'})
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('update rm.advisors set full_name=%s where advisor_id=(select advisor_id from rm.staff_logins where auth_user_id=%s)',(advisor_name,SOPHIE_LOGIN))
    case=FollowupSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),user_id=persona_id(1002),
        rm_token=token,rm_user_id=SOPHIE_LOGIN,rm_display_name=advisor_name.removesuffix(' (DEMO)'))
    run_browser(native_api_client,case,'client compliance handover reaches the assigned RM',tmp_path,timeout=600)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from rm.client_messages where customer_id=1002 and edited and body='I have received your compliance document question and will follow up after checking the requirements.'").fetchone()==(1,)
        assert db.execute("select status from compliance.cases where customer_id=1002").fetchone()==('awaiting_client_documents',)
        assert db.execute("select count(*) from audit.events where action='agent.llm_call' and outcome='success' and details->>'transport'='codex' and details->>'prompt_version'=%s",(prompts.load('rm_assistant',prompts.VERSIONS['rm_assistant'])['id'],)).fetchone()[0]>=1
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
