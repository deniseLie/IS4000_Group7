"""Real LangGraph pause/resume over native PostgreSQL/PostgREST; no model is replaced.

Opt in with TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1. The API integration test deliberately
removes model configuration and checks explicit withholding, not generated answer quality.
"""
import asyncio
import json
import os
import secrets
from typing import TypedDict
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END,START,StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.types import Command,Interrupt,interrupt
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import SecretStr
from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.schemas.contracts import Principal,RequestContext
from app.schemas.conversations import ConversationAccess,CreateConversation,TurnInput,TurnResponse
from app.services import conversations
from app.services.checkpoint_codec import CheckpointCodec,CheckpointIntegrityError
from app.services.checkpoints import PostgrestCheckpointSaver
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id


class ReviewState(TypedDict):
    input: int
    prepared: int
    approved: bool
    result: int


def prepare(state: ReviewState) -> dict[str,int]:
    return {'prepared':state['input']+1}


def review(state: ReviewState) -> dict[str,bool]:
    decision=interrupt({'prepared':state['prepared']})
    if not isinstance(decision,bool):raise ValueError('Review requires an explicit boolean decision')
    return {'approved':decision}


def complete(state: ReviewState) -> dict[str,int]:
    return {'result':state['prepared']*2 if state['approved'] else 0}


def graph(saver: PostgrestCheckpointSaver) -> CompiledStateGraph[ReviewState,None,ReviewState,ReviewState]:
    builder=StateGraph(ReviewState)
    builder.add_node('prepare',prepare)
    builder.add_node('review',review)
    builder.add_node('complete',complete)
    builder.add_edge(START,'prepare')
    builder.add_edge('prepare','review')
    builder.add_edge('review','complete')
    builder.add_edge('complete',END)
    return builder.compile(checkpointer=saver)


async def started(db: SyncPostgrestClient,ctx: RequestContext,access: ConversationAccess) -> TurnResponse:
    await conversations.create(CreateConversation(session_id=access.session_id,mode='public',language='English'),access,ctx,db)
    return await conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text='Please review this workflow',priority='P3'),access,ctx,db)


def customer(api: NativeAPI,cid: int) -> RequestContext:
    return RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=persona_id(cid),customer_id=cid,
        jwt=mint_token(api,cid,'aal1',uuid4())))


def test_codec_roundtrips_only_registered_state_and_rejects_credentials() -> None:
    codec=CheckpointCodec()
    message=ChatRequest(messages=[{'role':'user','content':'How do I reset my password?'}])
    value={'request':message,'tuple':('retail',uuid4()),'id':'12345678-1234-1234-1234-123456789012',
        'interrupt':Interrupt(value={'review':'synthetic'},id='review-1')}
    assert codec.loads_typed(codec.dumps_typed(value))==value
    for unsafe in [SecretStr('synthetic'),Principal(kind='guest'),RequestContext(session_id=uuid4(),principal=Principal(kind='guest')),
        {'jwt':'synthetic'},{'question':'My PIN is 1234'},{'question':'sb_secret_synthetic_test_credential'},float('nan')]:
        with pytest.raises(CheckpointIntegrityError):codec.dumps_typed(unsafe)
    for kind,data in [('pickle',b'never execute'),('demobank-json-v1',b'{"type":"os.system","value":"never execute"}')]:
        with pytest.raises(CheckpointIntegrityError):codec.loads_typed((kind,data))


native=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')


@native
def test_pause_restart_resume_history_and_customer_isolation(local_data_api: NativeAPI) -> None:
    import psycopg
    ctx=customer(local_data_api,1001)
    access=ConversationAccess(session_id=uuid4(),guest_token=None)
    key=SecretStr(secrets.token_urlsafe(32))
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        turn=asyncio.run(started(db,ctx,access))
        ctx=ctx.model_copy(update={'case_id':turn.turn.case_id})
        config={'configurable':{'thread_id':str(ctx.case_id)}}
        first=PostgrestCheckpointSaver(db,access,ctx,turn.turn,key)
        paused=asyncio.run(graph(first).ainvoke({'input':5},config,durability='sync'))
        assert paused['prepared']==6 and paused['__interrupt__'][0].value=={'prepared':6}
    # A new connector and compiled graph restore state without the original Python objects.
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        resumed=PostgrestCheckpointSaver(db,access,ctx,turn.turn,key)
        state=asyncio.run(graph(resumed).aget_state(config))
        assert state.next==('review',) and state.values['prepared']==6
        result=asyncio.run(graph(resumed).ainvoke(Command(resume=True),config,durability='sync'))
        assert result['result']==12 and result['approved'] is True
        async def history():return [item async for item in graph(resumed).aget_state_history(config)]
        snapshots=asyncio.run(history())
        assert len(snapshots)>=5 and snapshots[0].values['result']==12
        assert sum(item.metadata['source']=='input' for item in snapshots)==1
        position=resumed.position(config)
        with pytest.raises(CheckpointIntegrityError):asyncio.run(resumed.aget_tuple({'configurable':{'thread_id':str(uuid4())}}))
        other=customer(local_data_api,1003)
        db.auth(other.principal.jwt.get_secret_value())
        with pytest.raises(APIError) as error:db.rpc('workflow_read_customer',resumed.params(position)).execute()
        assert error.value.code=='P0002'
        assert db.schema('conversation').table('checkpoints').select('*').eq('customer_id',1001).execute().data==[]
        assert db.schema('conversation').table('checkpoint_writes').select('*').eq('customer_id',1001).execute().data==[]
    with psycopg.connect(local_data_api.database_uri) as admin:
        rows=str(admin.execute('select data from conversation.checkpoints').fetchall())
        assert ctx.principal.jwt.get_secret_value() not in rows and key.get_secret_value() not in rows
        assert admin.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@native
def test_guest_capability_tamper_signing_key_and_cancelled_writes(local_data_api: NativeAPI) -> None:
    import psycopg
    access=ConversationAccess(session_id=uuid4(),guest_token=secrets.token_urlsafe(32))
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    key=SecretStr(secrets.token_urlsafe(32))
    with SyncPostgrestClient(local_data_api.url) as db:
        turn=asyncio.run(started(db,ctx,access))
        ctx=ctx.model_copy(update={'case_id':turn.turn.case_id})
        saver=PostgrestCheckpointSaver(db,access,ctx,turn.turn,key)
        config={'configurable':{'thread_id':str(ctx.case_id)}}
        asyncio.run(graph(saver).ainvoke({'input':3},config,durability='sync'))
        saved=asyncio.run(saver.aget_tuple(config))
        assert saved is not None and saved.pending_writes
        wrong=PostgrestCheckpointSaver(db,access,ctx,turn.turn,SecretStr(secrets.token_urlsafe(32)))
        with pytest.raises(CheckpointIntegrityError,match='signature'):asyncio.run(wrong.aget_tuple(config))
        missing=PostgrestCheckpointSaver(db,access.model_copy(update={'guest_token':None}),ctx,turn.turn,key)
        with pytest.raises(APIError) as error:asyncio.run(missing.aget_tuple(config))
        assert error.value.code=='P0002'
        with psycopg.connect(local_data_api.database_uri) as admin:
            admin.execute("update conversation.checkpoints set data=jsonb_set(data,'{payload}','\"forged\"'::jsonb) where checkpoint_id=%s",(saved.checkpoint['id'],))
        with pytest.raises(CheckpointIntegrityError,match='signature'):asyncio.run(saver.aget_tuple(config))
        asyncio.run(conversations.cancel_turn(turn.turn.turn_id,access,ctx,db))
        with pytest.raises(APIError) as error:asyncio.run(saver.aput_writes(saved.config,[('result',8)],'late'))
        assert error.value.code=='23505'
        with psycopg.connect(local_data_api.database_uri) as admin:
            admin.execute("update conversation.sessions set expires_at=clock_timestamp()-interval '1 second' where session_id=%s",(access.session_id,))
        with pytest.raises(APIError) as error:asyncio.run(saver.aget_tuple(config))
        assert error.value.code=='P0002'


@native
def test_idempotent_writes_audit_rollback_and_read_permissions(local_data_api: NativeAPI) -> None:
    import psycopg
    ctx=customer(local_data_api,1001)
    access=ConversationAccess(session_id=uuid4(),guest_token=None)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        turn=asyncio.run(started(db,ctx,access))
        ctx=ctx.model_copy(update={'case_id':turn.turn.case_id})
        saver=PostgrestCheckpointSaver(db,access,ctx,turn.turn,SecretStr(secrets.token_urlsafe(32)))
        config={'configurable':{'thread_id':str(ctx.case_id)}}
        checkpoint=empty_checkpoint()
        metadata={'source':'input','step':-1,'parents':{}}
        with psycopg.connect(local_data_api.database_uri) as admin:
            admin.execute("create function public.native_reject_audit() returns trigger language plpgsql as $$ begin raise exception 'native audit failure'; end $$")
            admin.execute('create trigger native_reject before insert on audit.events for each row execute function public.native_reject_audit()')
        with pytest.raises(APIError):asyncio.run(saver.aput(config,checkpoint,metadata,{}))
        with psycopg.connect(local_data_api.database_uri) as admin:
            assert admin.execute('select count(*) from conversation.checkpoints').fetchone()[0]==0
            admin.execute('drop trigger native_reject on audit.events')
        # Pending writes can precede their snapshot in real LangGraph execution.
        expected=saver.config_for('',checkpoint['id'])
        asyncio.run(saver.aput_writes(expected,[('result',8),('__error__','first error')],'task-1'))
        stored=asyncio.run(saver.aput(config,checkpoint,metadata,{}))
        assert asyncio.run(saver.aput(config,checkpoint,metadata,{}))==stored
        with pytest.raises(APIError) as error:asyncio.run(saver.aput(config,{**checkpoint,'channel_values':{'changed':1}},metadata,{}))
        assert error.value.code=='23505'
        asyncio.run(saver.aput_writes(stored,[('result',9),('__error__','second error')],'task-1'))
        saved=asyncio.run(saver.aget_tuple(stored))
        assert saved.pending_writes==[('task-1','__error__','second error'),('task-1','result',8)]
        with psycopg.connect(local_data_api.database_uri) as admin:
            assert admin.execute("select count(*) from audit.events where action='chat.checkpoint_saved'").fetchone()[0]==1
            admin.execute('revoke select on conversation.checkpoint_writes from authenticated')
        with pytest.raises(APIError) as error:asyncio.run(saver.aget_tuple(stored))
        assert error.value.code=='42501'


@native
@pytest.mark.parametrize('kind',['customer','guest'])
def test_saved_v2_api_checkpoints_without_runtime_credentials(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,kind: str) -> None:
    import psycopg
    monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'pipeline','v2')
    monkeypatch.setattr(settings,'knowledge_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.delenv('LLM_MODEL',raising=False)
    sid=uuid4()
    ctx=customer(local_data_api,1001) if kind=='customer' else RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))
    headers={'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()} if kind=='customer' else {'X-Conversation-Token':secrets.token_urlsafe(32)}
    headers['X-Session-Id']=str(ctx.session_id)
    created=native_api_client.post('/conversations',headers=headers,json={'session_id':str(sid),'mode':'public','language':'English'})
    assert created.status_code==200,created.text
    response=native_api_client.post('/chat',headers=headers,json={'messages':[{'role':'user','content':'Hello!'}],
        'conversation_id':str(sid),'turn_id':str(uuid4()),'revision':0})
    assert response.status_code==200 and 'event: answer' in response.text,response.text
    assert '"response_origin": "validation"' in response.text and 'event: done' in response.text
    with psycopg.connect(local_data_api.database_uri) as admin:
        rows=admin.execute('select data from conversation.checkpoints where session_id=%s',(sid,)).fetchall()
        assert len(rows)>=5
        encoded=json.dumps(rows)
        assert (ctx.principal.jwt is None or ctx.principal.jwt.get_secret_value() not in encoded) and os.environ['CHECKPOINT_SIGNING_KEY'] not in encoded
        assert 'RequestContext' not in encoded and 'Principal' not in encoded and 'jwt' not in encoded
        assert admin.execute("select state from conversation.turns where session_id=%s",(sid,)).fetchone()==('completed',)
