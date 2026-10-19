"""Real native ownership, redaction, concurrency and transactional audit checks for saved chats."""
import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from app.schemas.contracts import Principal,RequestContext
from app.schemas.conversations import ConversationAccess,TurnInput,TurnResponse
from app.schemas.events import AnswerEvent
from app.services import conversations
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')


def customer_headers(api: NativeAPI,cid: int) -> dict[str,str]:
    return {'Authorization':'Bearer '+mint_token(api,cid,'aal1',uuid4())}


def create(client: TestClient,headers: dict[str,str]) -> UUID:
    sid=uuid4()
    response=client.post('/conversations',headers=headers,json={'session_id':str(sid),'mode':'public','language':'English'})
    assert response.status_code==200,response.text
    return sid


def fixed_answer() -> AnswerEvent:
    return AnswerEvent(answer='Please use your bank’s official support channel.',disclaimer='Synthetic coursework demo.',
        source_check='fixed_guidance',response_origin='safety')


async def save_turn(query: TurnInput,access: ConversationAccess,ctx: RequestContext,db: SyncPostgrestClient) -> TurnResponse:
    turn=await conversations.start_turn(query,access,ctx,db)
    return await conversations.finish_turn(turn,fixed_answer(),access,ctx,db)


def context(api: NativeAPI,cid: int) -> RequestContext:
    return RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=persona_id(cid),customer_id=cid,
        jwt=mint_token(api,cid,'aal1',uuid4())))


def test_owned_create_read_list_and_unlinked_scope(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    headers=customer_headers(local_data_api,1001)
    sid=create(native_api_client,headers)
    again=native_api_client.post('/conversations',headers=headers,json={'session_id':str(sid),'mode':'public','language':'English'})
    assert again.status_code==200 and again.json()['revision']==0
    own=native_api_client.get(f'/conversations/{sid}',headers=headers)
    assert own.status_code==200 and own.json()['messages']==[] and not own.json()['history_trimmed']
    other=customer_headers(local_data_api,1003)
    assert native_api_client.get(f'/conversations/{sid}',headers=other).status_code==404
    assert native_api_client.get('/conversations',headers=other).json()==[]
    listed=native_api_client.get('/conversations',headers=headers)
    assert listed.status_code==200 and [row['session_id'] for row in listed.json()]==[str(sid)]
    assert native_api_client.get('/conversations',headers=customer_headers(local_data_api,9999)).status_code==403
    assert native_api_client.get('/conversations').status_code==403


def test_guest_capability_is_required_hashed_and_expires(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    token=secrets.token_urlsafe(32)
    headers={'X-Conversation-Token':token}
    sid=create(native_api_client,headers)
    response=native_api_client.get(f'/conversations/{sid}',headers=headers)
    assert response.status_code==200 and response.json()['session']['expires_at'] is not None
    assert token not in response.text
    for wrong in [{},{'X-Session-Id':str(sid)},{'X-Conversation-Token':secrets.token_urlsafe(32)},customer_headers(local_data_api,1001)]:
        assert native_api_client.get(f'/conversations/{sid}',headers=wrong).status_code==404
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select key_hash from conversation.guest_access where session_id=%s',(sid,)).fetchone()[0]!=token
        db.execute("update conversation.sessions set expires_at=clock_timestamp()-interval '1 second' where session_id=%s",(sid,))
    assert native_api_client.get(f'/conversations/{sid}',headers=headers).status_code==404
    with SyncPostgrestClient(local_data_api.url) as db:
        with pytest.raises(APIError):db.schema('conversation').table('guest_access').select('*').execute()


def test_completed_turn_redacts_and_is_idempotent(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    sid=create(native_api_client,{'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()})
    access=ConversationAccess(session_id=sid,guest_token=None)
    turn=TurnInput(turn_id=uuid4(),revision=0,user_text='My PIN is 1 2 3 4 and email is demo@example.invalid',priority='P1')
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        saved=asyncio.run(save_turn(turn,access,ctx,db))
        repeated=asyncio.run(save_turn(turn,access,ctx,db))
        assert repeated==saved and saved.turn.revision==1
        history=asyncio.run(conversations.load(access,ctx,db))
    assert [item.role for item in history.messages]==['user','assistant']
    assert '1 2 3 4' not in history.model_dump_json() and 'demo@example.invalid' not in history.model_dump_json()
    assert history.messages[1].payload.response_origin=='safety'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from conversation.messages where session_id=%s',(sid,)).fetchone()[0]==2
        assert db.execute("select count(*) from audit.events where action='chat.turn_saved'").fetchone()[0]==1
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        assert ctx.principal.jwt.get_secret_value() not in str(db.execute('select redacted_text,payload from conversation.messages').fetchall())


def test_database_rejects_direct_unredacted_write(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    sid=create(native_api_client,{'Authorization':'Bearer '+token})
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        for text in ['My PIN is １２３４','OTP: 123-456','password: "synthetic phrase"','demo@example.invalid','My NRIC is S1234567D']:
            with pytest.raises(APIError) as error:
                db.rpc('conversation_start_turn',{'p_session_id':str(sid),'p_turn_id':str(uuid4()),'p_revision':0,
                    'p_user_text':text,'p_run_id':str(uuid4()),'p_priority':'P3','p_guest_token':None}).execute()
            assert error.value.code=='22023'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from conversation.messages where session_id=%s',(sid,)).fetchone()[0]==0


def test_concurrent_turns_cannot_overwrite_history(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    ctx=context(local_data_api,1001)
    sid=create(native_api_client,{'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()})
    access=ConversationAccess(session_id=sid,guest_token=None)
    def save() -> str:
        with SyncPostgrestClient(local_data_api.url) as db:
            db.auth(ctx.principal.jwt.get_secret_value())
            try:
                asyncio.run(save_turn(TurnInput(turn_id=uuid4(),revision=0,user_text='I need support',priority='P3'),access,ctx,db))
                return 'saved'
            except APIError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(lambda _:save(),range(2)))
    assert outcomes.count('saved')==1 and all(outcome in {'saved','23505','55P03'} for outcome in outcomes)


def test_interrupted_turn_is_visible_and_cancellation_wins_over_late_reply(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    from app.schemas.conversations import TurnInput,StoredFailure
    ctx=context(local_data_api,1001)
    sid=create(native_api_client,{'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()})
    access=ConversationAccess(session_id=sid,guest_token=None)
    query=TurnInput(turn_id=uuid4(),revision=0,user_text='I need support',priority='P3')
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        started=asyncio.run(conversations.start_turn(query,access,ctx,db))
        pending=asyncio.run(conversations.load(access,ctx,db))
        assert pending.active_turn.turn_id==query.turn_id and [item.role for item in pending.messages]==['user']
        with pytest.raises(APIError) as error:asyncio.run(conversations.start_turn(query,access,ctx,db))
        assert error.value.code=='55P03'
        cancelled=asyncio.run(conversations.cancel_turn(query.turn_id,access,ctx,db))
        late=asyncio.run(conversations.finish_turn(started,fixed_answer(),access,ctx,db))
        assert cancelled==late and isinstance(late.response,StoredFailure) and late.response.error.code=='cancelled'
        complete=asyncio.run(conversations.load(access,ctx,db))
        assert complete.active_turn is None and len(complete.messages)==2


def test_customer_history_still_requires_rls_table_permissions(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    headers=customer_headers(local_data_api,1001)
    sid=create(native_api_client,headers)
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('revoke select on conversation.messages from authenticated')
    response=native_api_client.get(f'/conversations/{sid}',headers=headers)
    assert response.status_code==403 and 'messages' not in response.json()


def test_failed_finish_rolls_back_answer_state_and_audit_together(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    from app.schemas.conversations import TurnInput
    ctx=context(local_data_api,1001)
    sid=create(native_api_client,{'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()})
    access=ConversationAccess(session_id=sid,guest_token=None)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        turn=asyncio.run(conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text='I need support',priority='P3'),access,ctx,db))
        with psycopg.connect(local_data_api.database_uri) as admin:
            admin.execute("create function public.native_reject_audit() returns trigger language plpgsql as $$ begin raise exception 'native audit failure'; end $$")
            admin.execute('create trigger native_reject before insert on audit.events for each row execute function public.native_reject_audit()')
        with pytest.raises(APIError):asyncio.run(conversations.finish_turn(turn,fixed_answer(),access,ctx,db))
        with psycopg.connect(local_data_api.database_uri) as admin:
            assert admin.execute('select count(*) from conversation.messages where session_id=%s',(sid,)).fetchone()[0]==1
            assert admin.execute('select state,answer_message_id from conversation.turns where turn_id=%s',(turn.turn.turn_id,)).fetchone()==('processing',None)
            assert admin.execute('select active_turn_id from conversation.sessions where session_id=%s',(sid,)).fetchone()[0]==turn.turn.turn_id
            admin.execute('drop trigger native_reject on audit.events')
        finished=asyncio.run(conversations.finish_turn(turn,fixed_answer(),access,ctx,db))
        assert finished.turn.state=='completed'


def test_reopened_history_is_chronological_and_bounded(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    ctx=context(local_data_api,1001)
    headers={'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()}
    sid=create(native_api_client,headers)
    access=ConversationAccess(session_id=sid,guest_token=None)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        for revision in range(31):
            asyncio.run(save_turn(TurnInput(turn_id=uuid4(),revision=revision,user_text=f'Question {revision}',priority='P3'),access,ctx,db))
    reopened=native_api_client.get(f'/conversations/{sid}',headers=customer_headers(local_data_api,1001))
    assert reopened.status_code==200
    history=reopened.json()
    assert history['history_trimmed'] and len(history['messages'])==60
    assert history['messages'][0]['redacted_text']=='Question 1' and history['session']['revision']==31
