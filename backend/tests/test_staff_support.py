"""Staff JWT/MFA boundaries, real action transactions and customer-visible receipts over native services."""
import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import jwt
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import SecretStr
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand,RequestContext
from app.services.actions import staff
from tests.data_api_support import NativeAPI,mint_token
from tests.test_actions_api import card_command,context,headers,owned_case,propose
from tests.test_notifications import worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST integrations')


def staff_token(api: NativeAPI,user_id: UUID,aal: str,metadata: dict[str,str]) -> str:
    return jwt.encode({'sub':str(user_id),'role':'authenticated','aud':'authenticated','session_id':str(uuid4()),'aal':aal,
        'exp':int(time.time())+3600,'app_metadata':metadata},api.signing_key,algorithm='ES256',headers={'kid':'native-test-key'})


def staff_user(api: NativeAPI) -> UUID:
    import psycopg
    user=uuid4()
    with psycopg.connect(api.database_uri) as db:db.execute('insert into auth.users(id,email) values(%s,%s)',(user,str(user)+'@example.invalid'))
    return user


def handoff(api: NativeAPI,client: TestClient,priority: str) -> tuple[RequestContext,UUID]:
    ctx=owned_case(api,context(api,1001))
    outcome=propose(client,ctx,ActionCommand(type='handoff.create',idempotency_key=f'{ctx.case_id}:staff-test:handoff.create',
        target={'reason':'card_verification','priority':priority,'summary':'Please review this synthetic support case.'}))
    return ctx,outcome.receipt.result.handoff_id


def block(client: TestClient,ctx: RequestContext,task: str) -> None:
    command=card_command(ctx,task)
    propose(client,ctx,command)
    response=client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert response.status_code==200 and response.json()['receipt']['outcome']=='succeeded',response.text


def test_staff_claim_unblock_cancel_close_replay_and_customer_receipts(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx,hid=handoff(local_data_api,native_api_client,'P0')
    user=staff_user(local_data_api)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        assert asyncio.run(staff.queue(20,db))[0].handoff_id==hid
        claim=uuid4()
        claimed=asyncio.run(staff.claim(claim,hid,db))
        assert claimed.receipt.result.status=='claimed'
        assert asyncio.run(staff.claim(claim,hid,db))==claimed
        # Invalid transitions produce immutable failed receipts rather than claiming a card changed.
        invalid=asyncio.run(staff.unblock(uuid4(),hid,'card_10011',db))
        assert invalid.receipt.outcome=='failed'
        block(native_api_client,ctx,'before-unblock')
        unblock_id=uuid4()
        unblocked=asyncio.run(staff.unblock(unblock_id,hid,'card_10011',db))
        assert unblocked.receipt.result.status=='active'
        block(native_api_client,ctx,'before-cancel')
        cancelled=asyncio.run(staff.cancel(uuid4(),hid,'card_10011',db))
        assert cancelled.receipt.result.status=='cancelled'
        assert asyncio.run(staff.unblock(uuid4(),hid,'card_10011',db)).receipt.outcome=='failed'
        close_id=uuid4()
        closed=asyncio.run(staff.close(close_id,hid,db))
        assert closed.receipt.result.status=='closed' and asyncio.run(staff.queue(20,db))==[]
        assert asyncio.run(staff.close(close_id,hid,db))==closed
        assert asyncio.run(staff.unblock(unblock_id,hid,'card_10011',db))==unblocked
    response=native_api_client.get('/commands',headers=headers(ctx))
    assert response.status_code==200,response.text
    outcomes=[ActionOutcome.model_validate(row) for row in response.json()]
    assert {row.command.type for row in outcomes}>={'handoff.claim','handoff.close','card.unblock','card.cancel_permanently'}
    assert native_api_client.get('/customer/cards',headers=headers(ctx)).json()['data'][0]['status']=='cancelled'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status,claimed_by from support.handoffs where handoff_id=%s',(hid,)).fetchone()==('closed',str(user))
        assert db.execute('select count(*) from actions.staff_commands').fetchone()==(6,)
        event=db.execute('select customer_id,actor_type,actor_id from audit.events where event_id=%s',(cancelled.receipt.audit_event_id,)).fetchone()
        assert event==(1001,'staff',str(user))
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_staff_role_mfa_signature_and_customer_entry_points_are_enforced(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx,hid=handoff(local_data_api,native_api_client,'P1')
    user=staff_user(local_data_api)
    tokens=[None,mint_token(local_data_api,1001,'aal2',uuid4()),
        staff_token(local_data_api,user,'aal1',{'demobank_role':'support'}),staff_token(local_data_api,user,'aal2',{'demobank_role':'customer'})]
    payload={'sub':str(user),'role':'authenticated','aud':'authenticated','aal':'aal2','exp':int(time.time())+3600,
        'user_metadata':{'demobank_role':'support'}}
    tokens.append(jwt.encode(payload,local_data_api.signing_key,algorithm='ES256',headers={'kid':'native-test-key'}))
    for token in tokens:
        with SyncPostgrestClient(local_data_api.url) as db:
            if token:db.auth(token)
            for rpc,params in [('staff_handoff_queue',{'p_limit':20}),('staff_claim_handoff',{'p_command_id':str(uuid4()),'p_handoff_id':str(hid)}),
                ('staff_unblock_card',{'p_command_id':str(uuid4()),'p_handoff_id':str(hid),'p_card_id':'card_10011'})]:
                with pytest.raises(APIError) as denied:db.rpc(rpc,params).execute()
                assert denied.value.code in ('42501','PGRST301')
    from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1,generate_private_key
    staff_claims={**payload,'app_metadata':{'demobank_role':'support'}}
    invalid_tokens=[jwt.encode(staff_claims,generate_private_key(SECP256R1()),algorithm='ES256',headers={'kid':'native-test-key'}),
        jwt.encode({**staff_claims,'exp':int(time.time())-3600},local_data_api.signing_key,algorithm='ES256',headers={'kid':'native-test-key'})]
    for token in invalid_tokens:
        with SyncPostgrestClient(local_data_api.url) as db:
            db.auth(token)
            with pytest.raises(APIError) as denied:db.rpc('staff_handoff_queue',{'p_limit':20}).execute()
            assert denied.value.code.startswith('PGRST30')
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError) as denied:db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),
            'p_type':'card.unblock','p_target':{'handoff_id':str(hid),'card_id':'card_10011'},
            'p_idempotency_key':f'{ctx.case_id}:direct:card.unblock'}).execute()
        assert denied.value.code=='42501'
    assert native_api_client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
        'task_id':'staff','type':'card.unblock','target':{'handoff_id':str(hid),'card_id':'card_10011'}}).status_code==422
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select count(*) from actions.staff_commands').fetchone()==(0,)


def test_competing_staff_and_changed_targets_cannot_steal_or_retarget(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx,hid=handoff(local_data_api,native_api_client,'P0')
    users=[staff_user(local_data_api),staff_user(local_data_api)]
    def claim(user: UUID) -> ActionOutcome:
        with SyncPostgrestClient(local_data_api.url) as db:
            db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
            return asyncio.run(staff.claim(uuid4(),hid,db))
    with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(claim,users))
    assert sorted(row.receipt.outcome for row in claims)==['failed','succeeded']
    winner=next(index for index,row in enumerate(claims) if row.receipt.outcome=='succeeded')
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,users[1-winner],'aal2',{'demobank_role':'support'}))
        assert asyncio.run(staff.close(uuid4(),hid,db)).receipt.outcome=='failed'
        assert asyncio.run(staff.cancel(uuid4(),hid,'card_10011',db)).receipt.outcome=='failed'
        with pytest.raises(APIError) as denied:asyncio.run(staff.claim(claims[winner].command.command_id,hid,db))
        assert denied.value.code=='42501'
        with pytest.raises(APIError):db.schema('actions').table('staff_commands').select('*').execute()
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,users[winner],'aal2',{'demobank_role':'support'}))
        with pytest.raises(APIError) as conflict:asyncio.run(staff.close(claims[winner].command.command_id,hid,db))
        assert conflict.value.code=='23505'
        with pytest.raises(APIError):asyncio.run(staff.unblock(uuid4(),hid,'card_99999',db))
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)


def test_queue_priority_age_limits_and_denial_audit(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    for priority in ['P3','P1','P0','P0']:handoff(local_data_api,native_api_client,priority)
    user=staff_user(local_data_api)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        rows=asyncio.run(staff.queue(3,db))
        assert len(rows)==3 and [row.priority for row in rows]==['P0','P0','P1'] and rows[0].created_at<rows[1].created_at
        for limit in [0,101,True]:
            with pytest.raises(ValueError):asyncio.run(staff.queue(limit,db))
        for limit in [0,101]:
            with pytest.raises(APIError) as invalid:db.rpc('staff_handoff_queue',{'p_limit':limit}).execute()
            assert invalid.value.code=='22023'
        db.auth(mint_token(local_data_api,1001,'aal2',uuid4()))
        with pytest.raises(APIError):asyncio.run(staff.queue(3,db))
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where action='policy.decided' and outcome='denied' and details->>'tool'='handoff.queue'").fetchone()==(1,)


def test_general_support_handoff_from_chat_is_queued_after_urgent_cases(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    handoff(local_data_api,native_api_client,'P0')
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    propose(native_api_client,ctx,ActionCommand(type='handoff.create',idempotency_key=f'{ctx.case_id}:handoff:handoff.create',
        target={'reason':'general_support','priority':'P2','summary':'Customer asked for a person. Topics: Everyday banking.'}))
    user=staff_user(local_data_api)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        rows=asyncio.run(staff.queue(10,db))
    assert [(row.reason,row.priority) for row in rows]==[('card_verification','P0'),('general_support','P2')]
    assert rows[1].case_id==ctx.case_id and rows[1].summary.startswith('Customer asked for a person.')


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_staff_change_and_receipt_roll_back_with_audit_or_outbox_failure(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    ctx,hid=handoff(local_data_api,native_api_client,'P0')
    user=staff_user(local_data_api)
    command_id=uuid4()
    with psycopg.connect(local_data_api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.native_staff_audit_failure() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'native audit failure'; end if; return new; end $$")
            db.execute('create trigger native_staff_audit_failure before insert on audit.events for each row execute function public.native_staff_audit_failure()')
        else:
            db.execute("alter table events.outbox add constraint native_staff_outbox_failure check(event_type<>'handoff.claimed')")
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        with pytest.raises(APIError):asyncio.run(staff.claim(command_id,hid,db))
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status,claimed_by from support.handoffs where handoff_id=%s',(hid,)).fetchone()==('open',None)
        assert db.execute('select count(*) from actions.commands where command_id=%s',(command_id,)).fetchone()==(0,)
        assert db.execute('select count(*) from actions.staff_commands where command_id=%s',(command_id,)).fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_staff_effects_deliver_canonical_customer_notifications(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    from tests.test_notifications import drain
    ctx,hid=handoff(local_data_api,native_api_client,'P0')
    user=staff_user(local_data_api)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(staff_token(local_data_api,user,'aal2',{'demobank_role':'support'}))
        asyncio.run(staff.claim(uuid4(),hid,db))
        block(native_api_client,ctx,'notify-unblock')
        asyncio.run(staff.unblock(uuid4(),hid,'card_10011',db))
        block(native_api_client,ctx,'notify-cancel')
        cancelled=asyncio.run(staff.cancel(uuid4(),hid,'card_10011',db))
        asyncio.run(staff.close(uuid4(),hid,db))
    results=asyncio.run(drain(worker_uri))
    assert all(item.status=='done' for item in results),results
    response=native_api_client.get('/notifications',headers=headers(ctx))
    assert response.status_code==200,response.text
    messages=response.json()['items']
    keys={item['template_key'] for item in messages}
    assert keys>={'card.unblocked','card.cancelled','handoff.claimed','handoff.closed'}
    assert all(item['customer_id']==1001 for item in messages)
    for message in messages:
        if message['template_key'] in ('card.unblocked','card.cancelled'):assert '4821' in message['body'] and message['is_service']
    foreign=context(local_data_api,1003)
    assert native_api_client.get(f'/commands/{cancelled.command.command_id}',headers=headers(foreign)).status_code==404
