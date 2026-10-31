"""Real consent actions, confirmation, scope, expiry, bounded reads and atomic rollback."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import SecretStr
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand,RequestContext
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers,propose
from tests.test_notifications import drain,worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in with both native database/API flags')


def action_case(client: TestClient,api: NativeAPI,cid: int) -> RequestContext:
    ctx=context(api,cid)
    response=client.post('/actions/cases',headers=headers(ctx),json={'session_id':str(uuid4()),'case_id':str(uuid4())})
    assert response.status_code==200,response.text
    return ctx.model_copy(update={'case_id':UUID(response.json()['case_id'])})


def recipient(api: NativeAPI) -> str:
    import psycopg
    with psycopg.connect(api.database_uri) as db:
        return str(db.execute('select advisor_id from rm.assignments where customer_id=1003 and ended_on is null').fetchone()[0])


def request_consent(client: TestClient,api: NativeAPI,ctx: RequestContext,categories: list[str],task: str) -> ActionOutcome:
    return propose(client,ctx,ActionCommand(type='consent.request',idempotency_key=f'{ctx.case_id}:{task}:consent.request',
        target={'purpose':'rm_brief','recipient':recipient(api),'categories':categories,'duration_days':30}))


def grant_proposal(client: TestClient,ctx: RequestContext,request_id: UUID,task: str) -> ActionOutcome:
    return propose(client,ctx,ActionCommand(type='consent.grant',idempotency_key=f'{ctx.case_id}:{task}:consent.grant',target={'request_id':str(request_id)}))


def confirm(client: TestClient,ctx: RequestContext,command_id: UUID) -> ActionOutcome:
    response=client.post(f'/commands/{command_id}/confirm',headers=headers(ctx),json={'target':{'categories':['accounts']},'confirmed':False})
    assert response.status_code==200,response.text
    return ActionOutcome.model_validate(response.json())


def check(client: TestClient,ctx: RequestContext,rm: str,categories: list[str]) -> bool:
    response=client.post('/consent/check',headers=headers(ctx),json={'purpose':'rm_brief','recipient':rm,'categories':categories})
    assert response.status_code==200,response.text
    return response.json()['granted']


def test_direct_case_is_scoped_idempotent_and_has_no_chat_turn(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=context(api,1003)
    body={'session_id':str(uuid4()),'case_id':str(uuid4())}
    first=client.post('/actions/cases',headers=headers(ctx),json=body)
    assert first.status_code==200,first.text
    assert client.post('/actions/cases',headers=headers(ctx),json=body).json()==first.json()
    assert client.post('/actions/cases',json=body).status_code==401
    assert client.post('/actions/cases',headers=headers(context(api,1001)),json=body).status_code in (403,404)
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from conversation.messages').fetchone()==(0,)
        assert db.execute('select count(*) from conversation.turns').fetchone()==(0,)
        db.execute("update conversation.cases set status='closed'")
    assert client.post('/actions/cases',headers=headers(ctx),json=body).status_code==403


def test_confirmed_receipts_and_exact_scope(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003); rm=recipient(api)
    requested=request_consent(client,api,ctx,['profile','agenda'],'request')
    assert requested.receipt.result.status=='pending' and requested.command.confirmed_at is None
    assert not check(client,ctx,rm,['profile']) and requested.command.target.categories==['agenda','profile']
    assert request_consent(client,api,ctx,['agenda','profile'],'request')==requested
    pending=grant_proposal(client,ctx,requested.receipt.result.request_id,'grant')
    assert pending.command.state=='proposed' and not check(client,ctx,rm,['profile'])
    assert pending.command.target.expires_at==requested.receipt.result.expires_at
    def execute(_: int) -> ActionOutcome:return confirm(client,ctx,pending.command.command_id)
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(execute,range(3)))
    assert all(item==results[0] for item in results)
    granted=results[0]; gid=granted.receipt.result.grant_id
    assert granted.receipt.result.status=='active' and check(client,ctx,rm,['profile','agenda'])
    assert not check(client,ctx,rm,['accounts']) and not check(client,ctx,str(uuid4()),['profile'])
    another=grant_proposal(client,ctx,requested.receipt.result.request_id,'second-grant')
    assert confirm(client,ctx,another.command.command_id).receipt.outcome=='failed'
    revoke=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:revoke:consent.revoke',target={'grant_id':str(gid)}))
    assert revoke.command.state=='proposed' and check(client,ctx,rm,['profile'])
    revoked=confirm(client,ctx,revoke.command.command_id)
    assert revoked.receipt.result.status=='revoked' and not check(client,ctx,rm,['profile'])
    assert confirm(client,ctx,revoke.command.command_id)==revoked
    assert confirm(client,ctx,pending.command.command_id)==granted
    records=client.get('/consent/records',headers=headers(ctx))
    assert records.status_code==200,records.text
    data=records.json()['data']
    assert data['requests_total']==1 and data['grants_total']==1
    assert data['grants'][0]['active'] is False and data['grants'][0]['revoked_at'] is not None
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select command_id,revocation_command_id from consent.grants where grant_id=%s',(gid,)).fetchone()==(pending.command.command_id,revoke.command.command_id)
        assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_other_grants_remain_visible_and_old_recipient_can_be_revoked(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003); rm=recipient(api); grants=[]
    for task in ['first','second']:
        request=request_consent(client,api,ctx,['profile'],task)
        pending=grant_proposal(client,ctx,request.receipt.result.request_id,task)
        grants.append(confirm(client,ctx,pending.command.command_id).receipt.result.grant_id)
    with psycopg.connect(api.database_uri) as db:db.execute('update rm.assignments set ended_on=current_date where customer_id=1003 and ended_on is null')
    for index,gid in enumerate(grants):
        pending=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:revoke{index}:consent.revoke',target={'grant_id':str(gid)}))
        assert confirm(client,ctx,pending.command.command_id).receipt.outcome=='succeeded'
        assert check(client,ctx,rm,['profile'])==(index==0)
        records=client.get('/consent/records',headers=headers(ctx)).json()['data']
        assert sum(row['active'] for row in records['grants'])==1-index


@pytest.mark.parametrize('change',['expiry','assignment','scope'])
def test_grant_rechecks_request_and_recipient(local_data_api: NativeAPI,native_api_client: TestClient,change: str) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003)
    request=request_consent(client,api,ctx,['profile'],'change');pending=grant_proposal(client,ctx,request.receipt.result.request_id,'change')
    with psycopg.connect(api.database_uri) as db:
        if change=='expiry':db.execute("update consent.requests set grant_expires_at=clock_timestamp()-interval '1 second'")
        elif change=='assignment':db.execute('update rm.assignments set ended_on=current_date where customer_id=1003 and ended_on is null')
        else:db.execute("update consent.requests set categories=array['accounts']")
    assert confirm(client,ctx,pending.command.command_id).receipt.outcome=='failed'
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from consent.grants where command_id is not null').fetchone()==(0,)
    if change=='expiry':assert client.get('/consent/records',headers=headers(ctx)).json()['data']['requests'][0]['status']=='expired'


def test_foreign_scope_guest_and_direct_writes_are_denied(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api; client=native_api_client; david=action_case(client,api,1003); alex=action_case(client,api,1001)
    request=request_consent(client,api,david,['profile'],'own')
    for typ,target in [('consent.request',{'purpose':'rm_brief','recipient':recipient(api),'categories':['profile'],'duration_days':30}),
        ('consent.grant',{'request_id':str(request.receipt.result.request_id)})]:
        response=client.post('/commands',headers=headers(alex),json={'command_id':str(uuid4()),'case_id':str(alex.case_id),'task_id':'foreign','type':typ,'target':target})
        assert response.status_code==403,response.text
    assert client.get('/consent/records').status_code==401
    assert client.get('/consent/records',headers=headers(alex)).json()['data']['requests']==[]
    with SyncPostgrestClient(api.url) as db:
        db.auth(david.principal.jwt.get_secret_value())
        for table in ['requests','grants']:
            with pytest.raises(APIError):db.schema('consent').table(table).update({'customer_id':1001}).eq('customer_id',1003).execute()
        with pytest.raises(APIError):db.rpc('customer_read_consent_records',{'p_customer_id':1001}).execute()


@pytest.mark.parametrize('failure',['audit','outbox'])
@pytest.mark.parametrize('operation',['grant','revoke'])
def test_effect_receipt_audit_and_outbox_roll_back(local_data_api: NativeAPI,native_api_client: TestClient,failure: str,operation: str) -> None:
    import psycopg
    from psycopg import sql
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003)
    request=request_consent(client,api,ctx,['profile'],'rollback');pending=grant_proposal(client,ctx,request.receipt.result.request_id,'rollback')
    if operation=='revoke':
        granted=confirm(client,ctx,pending.command.command_id)
        pending=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:rollback:consent.revoke',target={'grant_id':str(granted.receipt.result.grant_id)}))
    with psycopg.connect(api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.native_consent_audit() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'native audit failure'; end if; return new; end $$")
            db.execute('create trigger native_consent_audit before insert on audit.events for each row execute function public.native_consent_audit()')
        else:db.execute(sql.SQL('alter table events.outbox add constraint native_consent_outbox check(event_type<>{})').format(sql.Literal('consent.granted' if operation=='grant' else 'consent.revoked')))
    response=client.post(f'/commands/{pending.command.command_id}/confirm',headers=headers(ctx))
    assert response.status_code==503,response.text
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select state from actions.commands where command_id=%s',(pending.command.command_id,)).fetchone()==('proposed',)
        assert db.execute('select count(*) from actions.receipts where command_id=%s',(pending.command.command_id,)).fetchone()==(0,)
        if operation=='grant':assert db.execute('select count(*) from consent.grants where command_id is not null').fetchone()==(0,)
        else:assert db.execute('select revoked_at from consent.grants where command_id is not null').fetchone()==(None,)
        if failure=='audit':db.execute('drop trigger native_consent_audit on audit.events')
        else:db.execute('alter table events.outbox drop constraint native_consent_outbox')
    assert confirm(client,ctx,pending.command.command_id).receipt.outcome=='succeeded'


def test_notifications_follow_receipts_and_customer_scope(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003)
    with psycopg.connect(api.database_uri) as db:db.execute('update notify.preferences set quiet_start=null,quiet_end=null where customer_id=1003')
    request=request_consent(client,api,ctx,['profile'],'notify')
    assert all(item.status=='done' for item in asyncio.run(drain(worker_uri)))
    pending=grant_proposal(client,ctx,request.receipt.result.request_id,'notify');granted=confirm(client,ctx,pending.command.command_id)
    revoke=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:notify:consent.revoke',target={'grant_id':str(granted.receipt.result.grant_id)}))
    confirm(client,ctx,revoke.command.command_id)
    results=asyncio.run(drain(worker_uri));assert all(item.status=='done' for item in results),results
    assert asyncio.run(drain(worker_uri))==[]
    inbox=client.get('/notifications',headers=headers(ctx)).json()['items']
    assert sorted(item['template_key'] for item in inbox if item['template_key'].startswith('consent.'))==['consent.granted','consent.requested','consent.revoked']
    assert not any(item['template_key'].startswith('consent.') for item in client.get('/notifications',headers=headers(context(api,1001))).json()['items'])


def test_rejection_and_registry_failure_do_not_grant_or_assume_denial(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003)
    request=request_consent(client,api,ctx,['profile'],'reject');pending=grant_proposal(client,ctx,request.receipt.result.request_id,'reject')
    response=client.post(f'/commands/{pending.command.command_id}/reject',headers=headers(ctx))
    assert response.status_code==200 and response.json()['command']['state']=='rejected'
    assert not check(client,ctx,recipient(api),['profile'])
    with psycopg.connect(api.database_uri) as db:db.execute('drop function public.customer_read_consent_records(bigint)')
    response=client.get('/consent/records',headers=headers(ctx))
    assert response.status_code==503 and 'data' not in response.json()


def test_registry_prioritises_active_grants_and_reports_bounds(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003); rm=recipient(api)
    with psycopg.connect(api.database_uri) as db:
        db.execute("insert into consent.grants(customer_id,purpose,recipient,categories,revoked_at) select 1003,'rm_brief',%s,array['profile'],clock_timestamp() from generate_series(1,205)",(rm,))
        active=db.execute("insert into consent.grants(customer_id,purpose,recipient,categories,granted_at) values(1003,'rm_brief',%s,array['profile'],clock_timestamp()-interval '1 day') returning grant_id",(rm,)).fetchone()[0]
    response=client.get('/consent/records',headers=headers(ctx))
    assert response.status_code==200,response.text
    data=response.json()['data']
    assert data['grants_total']==206 and len(data['grants'])==200 and data['grants'][0]['grant_id']==str(active) and data['grants'][0]['active']


def test_invalid_request_scope_is_rejected_by_api_and_database(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003)
    valid={'purpose':'rm_brief','recipient':recipient(api),'categories':['profile'],'duration_days':30}
    changes=[{'duration_days':0},{'duration_days':366},{'duration_days':True},{'categories':[]},
        {'categories':['profile','profile']},{'categories':['all_data']},{'purpose':'other_purpose'}]
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        for index,change in enumerate(changes):
            target={**valid,**change}
            response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
                'task_id':f'invalid{index}','type':'consent.request','target':target})
            assert response.status_code==422,response.text
            with pytest.raises(APIError) as error:
                db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),
                    'p_type':'consent.request','p_target':target,'p_idempotency_key':f'{ctx.case_id}:invalid{index}:consent.request'}).execute()
            assert error.value.code=='22023'


def test_competing_grants_and_foreign_revocation_cannot_duplicate_or_cross_scope(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1003)
    request=request_consent(client,api,ctx,['profile'],'competing')
    pending=[grant_proposal(client,ctx,request.receipt.result.request_id,task) for task in ['one','two']]
    def execute(item: ActionOutcome) -> ActionOutcome:return confirm(client,ctx,item.command.command_id)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(execute,pending))
    assert sorted(item.receipt.outcome for item in results)==['failed','succeeded']
    granted=next(item for item in results if item.receipt.outcome=='succeeded')
    foreign=action_case(client,api,1001)
    response=client.post('/commands',headers=headers(foreign),json={'command_id':str(uuid4()),'case_id':str(foreign.case_id),
        'task_id':'foreign','type':'consent.revoke','target':{'grant_id':str(granted.receipt.result.grant_id)}})
    assert response.status_code==403,response.text
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        assert len(db.schema('consent').table('grants').select('grant_id').eq('customer_id',1003).execute().data)==1


def test_legacy_grant_revocation_does_not_reveal_unassigned_advisor_metadata(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; ctx=action_case(client,api,1001)
    with psycopg.connect(api.database_uri) as db:
        gid=db.execute("insert into consent.grants(customer_id,purpose,recipient,categories) values(1001,'rm_brief',%s,array['profile']) returning grant_id",(recipient(api),)).fetchone()[0]
        db.execute('create policy native_all_consent_requests on consent.requests for select to authenticated using(true)')
        db.execute('create policy native_all_consent_grants on consent.grants for select to authenticated using(true)')
    pending=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:legacy:consent.revoke',target={'grant_id':str(gid)}))
    assert pending.command.target.recipient_name is None
    assert confirm(client,ctx,pending.command.command_id).receipt.result.status=='revoked'
    foreign=client.get('/consent/records',headers=headers(context(api,1003)))
    assert foreign.status_code==200 and foreign.json()['data']['grants']==[]
