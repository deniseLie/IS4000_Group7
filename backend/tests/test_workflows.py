"""Native workflow mechanics using real graph/action nodes, JWT/RLS, receipts and cancellation.

The fixture starts after dispatch with explicitly safety-origin text and a synthetic user-selected
card. It does not replace an AI client or claim to test model accuracy.
"""
import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentResult,RequestContext,Task,TaskPlan
from app.schemas.conversations import ConversationAccess,CreateConversation,TurnInput,TurnResponse
from app.schemas.fraud import DisputeProposal,FraudProposal
from app.schemas.workflows import OwnedWorkflow,PipelineContext,ResponseMeta
from app.services import conversations,workflow_store
from app.services.checkpoints import PostgrestCheckpointSaver,signing_key
from app.services.orchestrator import build_graph
from app.services.guardrail import assess_request
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')


async def pause(db: SyncPostgrestClient,ctx: RequestContext,dispute: bool=False) -> tuple[OwnedWorkflow,UUID]:
    access=ConversationAccess(session_id=uuid4(),guest_token=None)
    await conversations.create(CreateConversation(session_id=access.session_id,mode='personal',language='English'),access,ctx,db)
    query='I do not recognise txn_100111 on my synthetic card ending 4821.'
    turn=await conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text=query,priority='P0'),access,ctx,db)
    owned=OwnedWorkflow(access=access,turn=turn.turn)
    current=ctx.model_copy(update={'case_id':turn.turn.case_id})
    req=ChatRequest(messages=[{'role':'user','content':query}],mode='personal')
    saver=PostgrestCheckpointSaver(db,access,current,turn.turn,signing_key())
    graph=build_graph(saver)
    config={'configurable':{'thread_id':str(turn.turn.case_id)}}
    task=Task(task_id='t1',domain='fraud',intent='answer_question',priority='P0',entities={'question':query})
    await graph.aupdate_state(config,{'request':req,'history':req.messages,'history_count':1,'risk':assess_request(req),
        'workflow_version':'v2-actions-v1','plan':TaskPlan(tasks=[task]),
        'results':[AgentResult(task_id='t1',agent='fraud',status='needs_human',text='Review the synthetic card action you selected.',answer_kind='conversation')],
        'evidence':[[]],'metadata':[ResponseMeta(origin='safety',prompt_id=None)],
        'proposals':[FraudProposal(card_id='card_10011',transaction_id='txn_100111')],
        'disputes':[DisputeProposal(card_id='card_10011',transaction_ids=['txn_100111','txn_100116']) if dispute else None]},as_node='dispatch')
    result=await graph.ainvoke(None,config,context=PipelineContext(request=current,workflow=owned),durability='sync')
    assert result['__interrupt__'] and 'answer' not in result
    snapshot=await graph.aget_state(config)
    await workflow_store.waiting(turn.turn.turn_id,turn.turn.run_id,snapshot.config['configurable']['checkpoint_id'],current,db)
    return owned,result['pending_reviews'][0].command_id


def paused(api: NativeAPI,ctx: RequestContext,dispute: bool=False) -> tuple[OwnedWorkflow,UUID]:
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        return asyncio.run(pause(db,ctx,dispute))


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.setattr(settings,'conversation_store','database')


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
@pytest.mark.parametrize('decision',['confirm','reject'])
def test_review_resumes_persisted_graph_once(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None,decision: str) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    owned,command_id=paused(local_data_api,ctx)
    history=native_api_client.get(f'/conversations/{owned.access.session_id}',headers=headers(ctx)).json()
    assert history['workflow']['ready'] and history['workflow']['pending_commands']==[str(command_id)]
    response=native_api_client.post(f'/commands/{command_id}/{decision}',headers=headers(ctx))
    assert response.status_code==200,response.text
    assert response.json()['workflow']['state']=='completed',response.text
    history=native_api_client.get(f'/conversations/{owned.access.session_id}',headers=headers(ctx)).json()
    assert history['active_turn'] is None and history['workflow'] is None
    answer=history['messages'][-1]['payload']
    assert answer['response_origin']=='safety' and all(section['response_origin']=='safety' for section in answer['sections'])
    assert len(answer['sections'])==1 and answer['sections'][0]['task_id']=='t1' and answer['sections'][0]['pending_actions']==[]
    assert answer['sections'][0]['decision'].startswith('Your card ending 4821 is temporarily blocked.' if decision=='confirm' else 'You chose not to block the card ending 4821; it stays active.')
    assert ('handoff for identity verification' in answer['answer'])==(decision=='confirm')
    again=native_api_client.post(f'/workflows/{owned.turn.turn_id}/resume',headers=headers(ctx))
    assert again.status_code==200 and again.json()['state']=='completed'
    with psycopg.connect(local_data_api.database_uri) as db:
        # Only a blocked card is handed to staff; declining leaves the card active and records no handoff.
        assert db.execute('select count(*) from support.handoffs').fetchone()==((1 if decision=='confirm' else 0),)
        assert db.execute('select count(*) from actions.receipts').fetchone()==((2 if decision=='confirm' else 0),)
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==(('temporarily_blocked' if decision=='confirm' else 'active'),)
        assert db.execute('select count(*) from conversation.messages where role=\'assistant\'').fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
@pytest.mark.parametrize('block,dispute',[('confirm','confirm'),('reject','confirm'),('confirm','reject')])
def test_block_and_dispute_are_two_decisions_in_one_paused_turn_with_one_case_summary(local_data_api: NativeAPI,
        native_api_client: TestClient,enabled: None,block: str,dispute: str) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    owned,block_id=paused(local_data_api,ctx,dispute=True)
    preview=native_api_client.get(f'/workflows/{owned.turn.turn_id}/preview',headers=headers(ctx)).json()
    offers=preview['preview']['sections'][0]['pending_actions']
    assert [item['type'] for item in offers]==['card.temporary_block','card.dispute'] and offers[0]['command_id']==str(block_id)
    assert offers[1]['summary']=='Review a dispute draft of 2 charges on synthetic card ending 4821'
    dispute_id=offers[1]['command_id']
    assert native_api_client.post(f'/commands/{dispute_id}/confirm',headers=headers(ctx)).status_code==422
    assert native_api_client.post(f'/commands/{block_id}/{block}',headers=headers(ctx)).json()['workflow']['state']=='awaiting_confirmation'
    body={'answers':{'card_in_possession':'yes'}} if dispute=='confirm' else None
    response=native_api_client.post(f'/commands/{dispute_id}/{dispute}',headers=headers(ctx),json=body)
    assert response.status_code==200 and response.json()['workflow']['state']=='completed',response.text
    answer=native_api_client.get(f'/conversations/{owned.access.session_id}',headers=headers(ctx)).json()['messages'][-1]['payload']
    decision=answer['sections'][0]['decision']
    assert len(answer['sections'])==1 and decision.startswith('Your card ending 4821 is temporarily blocked.' if block=='confirm' else 'You chose not to block')
    assert ('Your dispute draft for 2 charges on the card ending 4821 is recorded' in decision)==(dispute=='confirm')
    with psycopg.connect(local_data_api.database_uri) as db:
        handoffs=db.execute('select reason,priority,summary from support.handoffs').fetchall()
        assert [(reason,priority) for reason,priority,_ in handoffs]==[('card_dispute' if dispute=='confirm' else 'card_verification','P0')]
        summary=handoffs[0][2]
        assert 'Card ending 4821.' in summary and 'Reported charge: 2026-10-17 TECHVAULT ELECTRONICS SGD -2400.00.' in summary
        assert ('card in possession: yes' in summary)==(dispute=='confirm') and summary.endswith('Staff decide refunds and cancellation.')
        assert db.execute("select card_in_possession from (select result->>'card_in_possession' card_in_possession from actions.receipts r join actions.commands c using(command_id) where c.type='card.dispute') d").fetchall()==([('yes',)] if dispute=='confirm' else [])
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_waiting_reply_preview_is_owned_and_only_available_before_the_decision(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    ctx=context(local_data_api,1001)
    owned,command=paused(local_data_api,ctx)
    preview=native_api_client.get(f'/workflows/{owned.turn.turn_id}/preview',headers=headers(ctx))
    assert preview.status_code==200,preview.text
    body=preview.json()
    assert body['workflow']['state']=='awaiting_confirmation' and body['workflow']['pending_commands']==[str(command)]
    section=body['preview']['sections'][0]
    assert section['task_id']=='t1' and [item['command_id'] for item in section['pending_actions']]==[str(command)] and section['decision'] is None
    assert section['pending_actions'][0]['summary'].endswith('ending 4821')
    foreign=context(local_data_api,1003)
    assert native_api_client.get(f'/workflows/{owned.turn.turn_id}/preview',headers=headers(foreign)).status_code==404
    assert native_api_client.post(f'/commands/{command}/reject',headers=headers(ctx)).status_code==200
    assert native_api_client.get(f'/workflows/{owned.turn.turn_id}/preview',headers=headers(ctx)).status_code==409


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_cancel_rejects_pending_and_foreign_customer_cannot_resume(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    ctx=context(local_data_api,1001)
    owned,command=paused(local_data_api,ctx)
    foreign=context(local_data_api,1003)
    assert native_api_client.post(f'/workflows/{owned.turn.turn_id}/resume',headers=headers(foreign)).status_code==404
    cancelled=native_api_client.post(f'/conversations/{owned.access.session_id}/turns/{owned.turn.turn_id}/cancel',headers=headers(ctx))
    assert cancelled.status_code==200,cancelled.text
    late=native_api_client.post(f'/commands/{command}/confirm',headers=headers(ctx))
    assert late.status_code==409,late.text
    result=native_api_client.get(f'/commands/{command}',headers=headers(ctx)).json()
    assert result['command']['state']=='rejected' and result['receipt'] is None and result['workflow']['state']=='cancelled'


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_resume_lease_excludes_competitors_and_invalidates_old_saver(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    ctx=context(local_data_api,1001)
    owned,command=paused(local_data_api,ctx)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        db.rpc('action_reject',{'p_command_id':str(command)}).execute()
    def acquire(_: int):
        with SyncPostgrestClient(local_data_api.url) as db:
            db.auth(ctx.principal.jwt.get_secret_value())
            return asyncio.run(workflow_store.acquire(owned.turn.turn_id,uuid4(),ctx,db))
    with ThreadPoolExecutor(max_workers=4) as pool:leases=list(pool.map(acquire,range(4)))
    assert sum(item.acquired for item in leases)==1
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        old=PostgrestCheckpointSaver(db,owned.access,ctx.model_copy(update={'case_id':owned.turn.case_id}),owned.turn,signing_key())
        saved=asyncio.run(old.aget_tuple({'configurable':{'thread_id':str(owned.turn.case_id)}}))
        with pytest.raises(APIError) as error:asyncio.run(old.aput_writes(saved.config,[('late',1)],'late-worker'))
        assert error.value.code=='23505'


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_corrupt_checkpoint_keeps_receipt_and_allows_explicit_retry(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    owned,command=paused(local_data_api,ctx)
    with psycopg.connect(local_data_api.database_uri) as db:
        checkpoint_id,data=db.execute('select checkpoint_id,data from conversation.checkpoints order by checkpoint_id desc limit 1').fetchone()
        db.execute("update conversation.checkpoints set data=jsonb_set(data,'{signature}',to_jsonb(repeat('0',64))) where checkpoint_id=%s",(checkpoint_id,))
    result=native_api_client.post(f'/commands/{command}/confirm',headers=headers(ctx))
    assert result.status_code==200,result.text
    assert result.json()['receipt']['outcome']=='succeeded'
    assert result.json()['workflow']['state']=='failed' and result.json()['workflow']['can_resume']
    with psycopg.connect(local_data_api.database_uri) as db:
        from psycopg.types.json import Jsonb
        db.execute('update conversation.checkpoints set data=%s where checkpoint_id=%s',(Jsonb(data),checkpoint_id))
    retried=native_api_client.post(f'/workflows/{owned.turn.turn_id}/resume',headers=headers(ctx))
    assert retried.status_code==200 and retried.json()['state']=='completed',retried.text


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_review_must_be_saved_and_expiry_still_resumes_without_a_block(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    owned,command=paused(local_data_api,ctx)
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('update conversation.agent_workflows set ready=false where turn_id=%s',(owned.turn.turn_id,))
    for decision in ['confirm','reject']:
        response=native_api_client.post(f'/commands/{command}/{decision}',headers=headers(ctx))
        assert response.status_code==503,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
        db.execute('update conversation.agent_workflows set ready=true where turn_id=%s',(owned.turn.turn_id,))
        db.execute("update actions.commands set proposed_at=clock_timestamp()-interval '6 minutes',expires_at=clock_timestamp()-interval '1 second' where command_id=%s",(command,))
    expired=native_api_client.post(f'/commands/{command}/confirm',headers=headers(ctx))
    assert expired.status_code==409,expired.text
    result=native_api_client.get(f'/commands/{command}',headers=headers(ctx)).json()
    assert result['command']['state']=='expired' and result['receipt'] is None and result['workflow']['state']=='completed'
    history=native_api_client.get(f'/conversations/{owned.access.session_id}',headers=headers(ctx)).json()
    assert 'expired without changing your card' in history['messages'][-1]['redacted_text']


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_handoff_failure_preserves_confirmed_card_and_retry_deduplicates_effects(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    owned,command=paused(local_data_api,ctx)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("alter table support.handoffs add constraint native_handoff_failure check(reason<>'card_verification')")
    response=native_api_client.post(f'/commands/{command}/confirm',headers=headers(ctx))
    assert response.status_code==200,response.text
    assert response.json()['receipt']['outcome']=='succeeded' and response.json()['workflow']['state']=='failed'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked',)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1,)
        db.execute('alter table support.handoffs drop constraint native_handoff_failure')
    response=native_api_client.post(f'/workflows/{owned.turn.turn_id}/resume',headers=headers(ctx))
    assert response.status_code==200 and response.json()['state']=='completed',response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from support.handoffs').fetchone()==(1,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(2,)


def test_personal_chat_missing_provider_reads_owned_records_but_never_proposes(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None,monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2')
    monkeypatch.delenv('LLM_MODEL',raising=False)
    ctx=context(local_data_api,1001)
    sid=uuid4()
    created=native_api_client.post('/conversations',headers=headers(ctx),json={'session_id':str(sid),'mode':'personal','language':'English'})
    assert created.status_code==200,created.text
    response=native_api_client.post('/chat',headers=headers(ctx),json={'mode':'personal',
        'messages':[{'role':'user','content':'Unfamiliar charge txn_100111 on my card ending 4821'}],
        'conversation_id':str(sid),'turn_id':str(uuid4()),'revision':0})
    assert 'event: answer' in response.text and 'event: workflow_paused' not in response.text,response.text
    assert '"response_origin": "validation"' in response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select type,state from actions.commands').fetchall()==[('handoff.create','succeeded')]
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1,)
        assert db.execute('select reason from support.handoffs').fetchall()==[('general_support',)]
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
        assert db.execute('select state from conversation.turns where session_id=%s',(sid,)).fetchone()==('completed',)


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
@pytest.mark.skipif(os.getenv('TEST_FRAUD_LIVE')!='1',reason='Opt in to real personal-fraud model calls')
def test_real_model_proposal_pauses_and_confirms(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None,monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    monkeypatch.setattr(settings,'pipeline','v2')
    ctx=context(local_data_api,1001)
    sid=uuid4()
    created=native_api_client.post('/conversations',headers=headers(ctx),json={'session_id':str(sid),'mode':'personal','language':'English'})
    assert created.status_code==200,created.text
    response=native_api_client.post('/chat',headers=headers(ctx),json={'mode':'personal',
        'messages':[{'role':'user','content':'This txn_100111 is an unfamiliar charge on my card ending 4821. Please propose a temporary block for review.'}],
        'conversation_id':str(sid),'turn_id':str(uuid4()),'revision':0})
    assert 'event: workflow_paused' in response.text,response.text
    payload=response.text.split('event: workflow_paused\r\ndata: ',1)[1].split('\r\n',1)[0]
    command=json.loads(payload)['actions'][0]['command_id']
    confirmed=native_api_client.post(f'/commands/{command}/confirm',headers=headers(ctx))
    assert confirmed.status_code==200 and confirmed.json()['workflow']['state']=='completed',confirmed.text
    history=native_api_client.get(f'/conversations/{sid}',headers=headers(ctx)).json()
    answer=history['messages'][-1]['payload']
    assert answer['sections'][0]['response_origin']=='ai' and answer['sections'][0]['decision'].startswith('Your card ending 4821 is temporarily blocked.')
