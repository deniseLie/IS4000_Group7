"""Normal chat/SSE integration with native storage, JWT, RLS and audit. No provider output is simulated."""
import json
import os
from collections.abc import Iterator
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from app.core.config import settings
from tests.data_api_support import NativeAPI,mint_token

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')


@pytest.fixture
def stored_client(native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v1')
    yield native_api_client


def headers(api: NativeAPI) -> dict[str,str]:
    return {'Authorization':'Bearer '+mint_token(api,1001,'aal1',uuid4())}


def create(client: TestClient,auth: dict[str,str]) -> UUID:
    sid=uuid4()
    response=client.post('/conversations',headers=auth,json={'session_id':str(sid),'mode':'public','language':'English'})
    assert response.status_code==200
    return sid


def body(sid: UUID,turn: UUID,revision: int,question: str) -> dict[str,JsonValue]:
    return {'conversation_id':str(sid),'turn_id':str(turn),'revision':revision,'mode':'public','language':'English',
        'messages':[{'role':'user','content':question}]}


def events(text: str,name: str) -> list[JsonValue]:
    return [json.loads(block.split('data: ',1)[1]) for block in text.replace('\r\n','\n').split('\n\n') if block.startswith(f'event: {name}\n')]


def test_safety_turn_is_saved_and_replayed_once(local_data_api: NativeAPI,stored_client: TestClient) -> None:
    import psycopg
    auth=headers(local_data_api)
    sid=create(stored_client,auth)
    payload=body(sid,uuid4(),0,'I lost my card')
    # Client history is untrusted; the server loads the owned history instead.
    payload['messages']=[{'role':'user','content':'My PIN is 1234'}, {'role':'user','content':'I lost my card'}]
    response=stored_client.post('/chat',headers=auth,json=payload)
    assert response.status_code==200 and 'event: error' not in response.text
    assert events(response.text,'answer')[0]['response_origin']=='safety'
    assert 'Private details were removed' not in response.text
    metadata=events(response.text,'conversation')
    assert metadata[0]['revision']==1 and metadata[0]['active_turn_id'] is not None and metadata[-1]['active_turn_id'] is None
    repeated=stored_client.post('/chat',headers=auth,json=payload)
    assert events(repeated.text,'answer')==events(response.text,'answer')
    loaded=stored_client.get(f'/conversations/{sid}',headers=auth).json()
    assert len(loaded['messages'])==2 and loaded['session']['revision']==1
    assert loaded['messages'][0]['redacted_text']=='I lost my card' and loaded['active_turn'] is None
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where action='chat.turn_saved'").fetchone()[0]==1
        assert str(db.execute("select request_id from audit.events where action='chat.turn_saved'").fetchone()[0])==response.headers['x-request-id']
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_missing_provider_is_stored_as_error_without_an_ai_answer(local_data_api: NativeAPI,stored_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('LLM_MODEL',raising=False)
    auth=headers(local_data_api)
    sid=create(stored_client,auth)
    response=stored_client.post('/chat',headers=auth,json=body(sid,uuid4(),0,'Explain fixed deposits'))
    assert 'event: error' in response.text and not events(response.text,'answer')
    history=stored_client.get(f'/conversations/{sid}',headers=auth).json()
    assert history['active_turn'] is None and len(history['messages'])==2
    assert history['messages'][1]['payload']['error']['code']=='assistant_unavailable'
    assert 'answer' not in history['messages'][1]['payload']


def test_conflicts_mode_changes_and_missing_protocol_fail_before_generation(local_data_api: NativeAPI,stored_client: TestClient) -> None:
    auth=headers(local_data_api)
    sid=create(stored_client,auth)
    assert stored_client.post('/chat',headers=auth,json={'messages':[{'role':'user','content':'I lost my card'}]}).status_code==422
    assert stored_client.post('/chat',headers=auth,json={**body(sid,uuid4(),0,'I lost my card'),'language':'Chinese'}).status_code==409
    first=stored_client.post('/chat',headers=auth,json=body(sid,uuid4(),0,'I lost my card'))
    assert events(first.text,'answer')
    stale=stored_client.post('/chat',headers=auth,json=body(sid,uuid4(),0,'Talk to a human'))
    assert stale.status_code==409 and stale.json()['error']['code']=='conversation_conflict'
    assert stored_client.get(f'/conversations/{sid}',headers=auth).json()['session']['revision']==1


def test_failed_storage_never_releases_answer_and_leaves_recoverable_turn(local_data_api: NativeAPI,stored_client: TestClient) -> None:
    import psycopg
    auth=headers(local_data_api)
    sid=create(stored_client,auth)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("""create function public.native_reject_saved_reply() returns trigger language plpgsql as $$
            begin if new.action='chat.turn_saved' then raise exception 'native storage audit failure'; end if; return new; end $$""")
        db.execute('create trigger native_reject before insert on audit.events for each row execute function public.native_reject_saved_reply()')
    response=stored_client.post('/chat',headers=auth,json=body(sid,uuid4(),0,'I lost my card'))
    assert 'event: error' in response.text and not events(response.text,'answer') and 'could not be saved' in response.text
    history=stored_client.get(f'/conversations/{sid}',headers=auth).json()
    assert len(history['messages'])==1 and history['active_turn'] is not None
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop trigger native_reject on audit.events')
    cancelled=stored_client.post(f"/conversations/{sid}/turns/{history['active_turn']['turn_id']}/cancel",headers=auth)
    assert cancelled.status_code==200 and cancelled.json()['response']['error']['code']=='cancelled'


def test_five_full_length_sections_fit_the_saved_reply_contract(local_data_api: NativeAPI,stored_client: TestClient) -> None:
    import asyncio
    from postgrest import SyncPostgrestClient
    from app.schemas.contracts import Principal,RequestContext
    from app.schemas.conversations import ConversationAccess,TurnInput
    from app.schemas.events import AnswerEvent,AnswerSection
    from app.services import conversations
    from tests.database_support import persona_id
    auth=headers(local_data_api)
    sid=create(stored_client,auth)
    token=auth['Authorization'].removeprefix('Bearer ')
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=persona_id(1001),customer_id=1001,jwt=token))
    access=ConversationAccess(session_id=sid,guest_token=None)
    # A structured validation specimen for storage limits, never a simulated model response.
    sections=[AnswerSection(task_id=f't{i}',domain=domain,priority='P3',answer='A'*4000,
        response_origin='validation',needs_human=True) for i,domain in enumerate(['retail','fraud','compliance','document','relationship'])]
    reply=AnswerEvent(answer='\n\n'.join(section.answer for section in sections),sections=sections,
        disclaimer='Validation specimen.',source_check='rejected',response_origin='validation',needs_human=True)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        turn=asyncio.run(conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text='Storage limit check',priority='P3'),access,ctx,db))
        saved=asyncio.run(conversations.finish_turn(turn,reply,access,ctx,db))
        assert len(saved.response.answer)==20008
    history=stored_client.get(f'/conversations/{sid}',headers=auth).json()
    assert len(history['messages'][1]['redacted_text'])==20008 and len(history['messages'][1]['payload']['sections'])==5
