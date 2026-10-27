"""Real action transactions, user-JWT/RLS, concurrency and audit/outbox rollback over native services."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand,Principal,RequestContext
from app.schemas.conversations import ConversationAccess,CreateConversation,TurnInput
from app.services import actions,conversations
from app.services.authorization import PolicyDenied
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in with TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1')


def context(api: NativeAPI,cid: int) -> RequestContext:
    return RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=persona_id(cid),customer_id=cid,
        jwt=mint_token(api,cid,'aal1',uuid4())))


def headers(ctx: RequestContext) -> dict[str,str]:
    return {'Authorization':'Bearer '+ctx.principal.jwt.get_secret_value()}


def owned_case(api: NativeAPI,ctx: RequestContext) -> RequestContext:
    async def start(db: SyncPostgrestClient) -> UUID:
        access=ConversationAccess(session_id=uuid4(),guest_token=None)
        await conversations.create(CreateConversation(session_id=access.session_id,mode='public',language='English'),access,ctx,db)
        turn=await conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text='Please review a synthetic action',priority='P3'),access,ctx,db)
        return turn.turn.case_id
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        return ctx.model_copy(update={'case_id':asyncio.run(start(db))})


def card_command(ctx: RequestContext,task: str) -> ActionCommand:
    return ActionCommand(type='card.temporary_block',idempotency_key=f'{ctx.case_id}:{task}:card.temporary_block',target={'card_id':'card_10011'})


def propose(client: TestClient,ctx: RequestContext,command: ActionCommand) -> ActionOutcome:
    response=client.post('/commands',headers=headers(ctx),json={'command_id':str(command.command_id),'case_id':str(ctx.case_id),
        'task_id':command.idempotency_key.split(':')[1],'type':command.type,'target':command.target})
    assert response.status_code==200,response.text
    return ActionOutcome.model_validate(response.json())


def test_card_proposal_waits_for_confirmation_and_concurrent_retries_share_one_receipt(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    command=card_command(ctx,'block1').model_copy(update={'target':{'card_id':'card_10011',
        'reason':'unrelated','priority':'P0','summary':'Unrelated fields do not select the executor.'}})
    pending=propose(native_api_client,ctx,command)
    assert pending.command.state=='proposed' and pending.receipt is None and pending.command.target.last4=='4821'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
        assert db.execute('select count(*) from events.outbox where command_id is not null').fetchone()==(0,)
    duplicate=propose(native_api_client,ctx,command.model_copy(update={'command_id':uuid4()}))
    assert duplicate==pending
    def confirm(_: int) -> ActionOutcome:
        response=native_api_client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx),json={'target':{'card_id':'card_99999'},'confirmed':False})
        assert response.status_code==200,response.text
        return ActionOutcome.model_validate(response.json())
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(confirm,range(4)))
    assert all(item==results[0] for item in results)
    result=results[0]
    assert result.command.state=='succeeded' and result.receipt.result.card_id=='card_10011' and result.receipt.audit_event_id>0
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('temporarily_blocked',)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1,)
        assert db.execute('select count(*) from events.outbox where command_id=%s',(command.command_id,)).fetchone()==(1,)
        assert db.execute("select count(*) from audit.events where action='action.executed'").fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    assert native_api_client.get('/commands',headers=headers(ctx)).json()==[result.model_dump(mode='json')]
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update banking.credit_cards set status='cancelled' where card_id=10011")
        db.execute("update conversation.cases set status='closed' where case_id=%s",(ctx.case_id,))
    assert propose(native_api_client,ctx,command)==result
    assert native_api_client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx)).json()==result.model_dump(mode='json')


def test_rejection_expiry_and_target_change_never_claim_success(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    command=card_command(ctx,'reject')
    propose(native_api_client,ctx,command)
    rejected=native_api_client.post(f'/commands/{command.command_id}/reject',headers=headers(ctx))
    assert rejected.status_code==200 and rejected.json()['command']['state']=='rejected' and rejected.json()['receipt'] is None
    assert native_api_client.post(f'/commands/{command.command_id}/reject',headers=headers(ctx)).json()==rejected.json()
    late=native_api_client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert late.status_code==409 and late.json()['error']['code']=='action_rejected'
    expired=card_command(ctx,'expire')
    propose(native_api_client,ctx,expired)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update actions.commands set proposed_at=clock_timestamp()-interval '6 minutes',expires_at=clock_timestamp()-interval '1 second' where command_id=%s",(expired.command_id,))
    response=native_api_client.post(f'/commands/{expired.command_id}/confirm',headers=headers(ctx))
    assert response.status_code==409 and response.json()['error']['code']=='action_expired'
    changed=card_command(ctx,'changed')
    propose(native_api_client,ctx,changed)
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("update banking.credit_cards set status='cancelled' where card_id=10011")
    failed=native_api_client.post(f'/commands/{changed.command_id}/confirm',headers=headers(ctx))
    assert failed.status_code==200 and failed.json()['receipt']['outcome']=='failed' and failed.json()['receipt']['result']=={'error_code':'target_changed'}
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from actions.receipts where outcome='succeeded'").fetchone()==(0,)
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('cancelled',)


def test_scope_policy_and_direct_table_writes_are_enforced(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    command=card_command(ctx,'scope')
    propose(native_api_client,ctx,command)
    other=owned_case(local_data_api,context(local_data_api,1003))
    for suffix in ['', '/confirm','/reject']:
        response=native_api_client.get(f'/commands/{command.command_id}',headers=headers(other)) if not suffix else native_api_client.post(f'/commands/{command.command_id}{suffix}',headers=headers(other))
        assert response.status_code==404
    assert native_api_client.get('/commands',headers=headers(other)).json()==[]
    assert native_api_client.get('/commands').status_code==401
    assert native_api_client.get('/commands',headers=headers(context(local_data_api,9999))).status_code==403
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(other.principal.jwt.get_secret_value())
        for name,params in [('action_confirm',{'p_command_id':str(command.command_id)}),
            ('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),'p_type':command.type,'p_target':command.target,'p_idempotency_key':command.idempotency_key})]:
            with pytest.raises(APIError):db.rpc(name,params).execute()
        with pytest.raises(APIError):db.schema('banking').table('credit_cards').update({'status':'temporarily_blocked'}).eq('card_id',10011).execute()
        with pytest.raises(APIError):db.schema('actions').table('commands').update({'state':'succeeded'}).eq('command_id',str(command.command_id)).execute()
    for agent in ['retail','document']:
        with pytest.raises(PolicyDenied):asyncio.run(actions.run_action(command,ctx.model_copy(update={'agent':agent}),False))
    with pytest.raises(PolicyDenied):asyncio.run(actions.run_action(command,ctx.model_copy(update={'agent':'fraud'}),True))


def test_auto_handoff_is_real_redacted_and_deduplicated(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    command=ActionCommand(type='handoff.create',idempotency_key=f'{ctx.case_id}:support:handoff.create',
        target={'reason':'card_verification','priority':'P0','summary':'Please help. My PIN is 1234.'})
    saved=propose(native_api_client,ctx,command)
    assert saved.command.state=='succeeded' and saved.receipt.result.priority=='P0' and saved.command.confirmed_at is None
    assert saved.receipt.result.created
    assert '1234' not in saved.model_dump_json()
    assert propose(native_api_client,ctx,command)==saved
    again=propose(native_api_client,ctx,command.model_copy(update={'command_id':uuid4(),'idempotency_key':f'{ctx.case_id}:support2:handoff.create'}))
    assert again.receipt.result.handoff_id==saved.receipt.result.handoff_id
    assert not again.receipt.result.created
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from support.handoffs').fetchone()==(1,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(2,)
        assert db.execute("select count(*) from audit.events where action='handoff.created'").fetchone()==(1,)
        assert db.execute("select event_type,count(*) from events.outbox where command_id is not null group by event_type order by event_type").fetchall()==[('handoff.created',1),('handoff.reused',1)]


def test_talk_to_a_person_records_a_separate_general_support_handoff(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    urgent=owned_case(local_data_api,context(local_data_api,1001))
    propose(native_api_client,urgent,ActionCommand(type='handoff.create',idempotency_key=f'{urgent.case_id}:t1_handoff:handoff.create',
        target={'reason':'card_verification','priority':'P0','summary':'Customer reported a suspicious synthetic card transaction.'}))
    # The chat summary uses labels only; a fresh direct-form case keeps it apart from the card-verification case.
    direct=owned_case(local_data_api,context(local_data_api,1001))
    summary='Customer asked for a person. Topics: Fraud & security (urgent), Complex banking needs ×2. Card block: confirmed.'
    command=ActionCommand(type='handoff.create',idempotency_key=f'{direct.case_id}:handoff:handoff.create',
        target={'reason':'general_support','priority':'P0','summary':summary})
    saved=propose(native_api_client,direct,command)
    assert saved.command.state=='succeeded' and saved.command.confirmed_at is None and saved.receipt.result.created
    foreign=context(local_data_api,1003)
    assert native_api_client.get(f'/commands/{saved.command.command_id}',headers=headers(foreign)).status_code==404
    with psycopg.connect(local_data_api.database_uri) as db:
        rows=db.execute('select reason,priority,summary from support.handoffs order by created_at').fetchall()
        assert rows[0][:2]==('card_verification','P0') and rows[1]==('general_support','P0',summary) and len(rows)==2


def test_changed_idempotency_parameters_and_direct_credential_writes_are_rejected(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    command=ActionCommand(type='handoff.create',idempotency_key=f'{ctx.case_id}:support:handoff.create',
        target={'reason':'general_support','priority':'P3','summary':'Please review this synthetic case.'})
    propose(native_api_client,ctx,command)
    body={'command_id':str(uuid4()),'case_id':str(ctx.case_id),'task_id':'support','type':command.type,
        'target':{**command.target,'priority':'P0'}}
    response=native_api_client.post('/commands',headers=headers(ctx),json=body)
    assert response.status_code==409 and response.json()['error']['code']=='action_conflict'
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        for summary in ['PIN is 1234','sb_secret_synthetic_test_credential',ctx.principal.jwt.get_secret_value()]:
            with pytest.raises(APIError) as error:db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),
                'p_type':'handoff.create','p_idempotency_key':f'{ctx.case_id}:unsafe:handoff.create',
                'p_target':{'reason':'general_support','priority':'P3','summary':summary}}).execute()
            assert error.value.code=='22023'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from actions.commands').fetchone()==(1,)
        assert db.execute('select count(*) from support.handoffs').fetchone()==(1,)


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_execution_and_ledger_roll_back_together(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    command=card_command(ctx,'rollback')
    propose(native_api_client,ctx,command)
    with psycopg.connect(local_data_api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.native_reject_audit() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'native audit failure'; end if; return new; end $$")
            db.execute('create trigger native_reject before insert on audit.events for each row execute function public.native_reject_audit()')
        else:db.execute("alter table events.outbox add constraint native_reject check(event_type<>'card.blocked')")
    response=native_api_client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert response.status_code==503,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from banking.credit_cards where card_id=10011').fetchone()==('active',)
        assert db.execute('select state,confirmed_at from actions.commands where command_id=%s',(command.command_id,)).fetchone()==('proposed',None)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(0,)
        assert db.execute('select count(*) from events.outbox where command_id is not null').fetchone()==(0,)
        if failure=='audit':db.execute('drop trigger native_reject on audit.events')
        else:db.execute('alter table events.outbox drop constraint native_reject')
    retried=native_api_client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert retried.status_code==200 and retried.json()['receipt']['outcome']=='succeeded'
