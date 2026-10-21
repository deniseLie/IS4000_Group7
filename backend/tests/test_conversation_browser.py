"""Real React/SSE/FastAPI/JWT/RLS conversation persistence. All messages use no-model safety paths."""
import asyncio
import os
import secrets
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from pydantic import Field
from app.core.config import settings
from app.schemas.contracts import Principal,RequestContext
from app.schemas.conversations import ConversationAccess,TurnInput
from app.services import conversations
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in with TEST_BROWSER_LOCAL=1 and both database/API flags')


class PendingFixture(NativeBrowserSession):
    pending_session_id: UUID


class ExpiredFixture(NativeBrowserSession):
    expired_session_id: UUID
    guest_token: str = Field(repr=False)


@pytest.mark.parametrize('name',['saved customer chats reopen','saved guest chat survives'])
def test_real_saved_chat_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path,name: str) -> None:
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v1')
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),user_id=persona_id(1001))
    run_browser(native_api_client,case,name,tmp_path)


def test_real_pending_chat_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v1')
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    sid=uuid4()
    created=native_api_client.post('/conversations',headers={'Authorization':'Bearer '+token},json={'session_id':str(sid),'mode':'public','language':'English'})
    assert created.status_code==200
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=persona_id(1001),customer_id=1001,jwt=token))
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        asyncio.run(conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text='I need support',priority='P3'),
            ConversationAccess(session_id=sid,guest_token=None),ctx,db))
    case=PendingFixture(data_api_url=local_data_api.url,access_token=token,user_id=persona_id(1001),pending_session_id=sid)
    run_browser(native_api_client,case,'saved pending reply can be cancelled',tmp_path)


def test_real_expired_guest_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path) -> None:
    import psycopg
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v1')
    sid=uuid4()
    guest_token=secrets.token_urlsafe(32)
    created=native_api_client.post('/conversations',headers={'X-Conversation-Token':guest_token},json={'session_id':str(sid),'mode':'public','language':'English'})
    assert created.status_code==200
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update conversation.sessions set expires_at=clock_timestamp()-interval '1 second' where session_id=%s",(sid,))
    case=ExpiredFixture(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),
        user_id=persona_id(1001),expired_session_id=sid,guest_token=guest_token)
    run_browser(native_api_client,case,'expired guest chat requires',tmp_path)


@pytest.mark.parametrize('kind',['customer','guest'])
def test_saved_v2_checkpoint_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path,kind: str) -> None:
    import psycopg
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v2')
    monkeypatch.setattr(settings,'knowledge_store','memory')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.delenv('LLM_MODEL',raising=False)
    case=NativeBrowserSession(data_api_url=local_data_api.url,access_token=mint_token(local_data_api,1001,'aal1',uuid4()),user_id=persona_id(1001))
    run_browser(native_api_client,case,f'saved {kind} v2 unavailable section',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from conversation.checkpoints').fetchone()[0]>=5
        assert db.execute("select count(*) from conversation.turns where state='processing'").fetchone()[0]==0
        assert db.execute('select count(*) from support.handoffs').fetchone()==(1 if kind=='customer' else 0,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1 if kind=='customer' else 0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
