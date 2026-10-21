"""Versioned agenda saves through real customer JWT/RLS, the action ledger and notification worker."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import JsonValue,SecretStr
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand,RequestContext
from tests.data_api_support import NativeAPI
from tests.test_actions_api import headers,propose
from tests.test_consent_actions import action_case,context,recipient
from tests.test_notifications import drain,worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in with both native database/API flags')


def agenda_target(api: NativeAPI) -> dict[str,JsonValue]:
    return {'agenda_id':str(uuid4()),'advisor_id':recipient(api),'expected_revision':0,
        'topics':['Discuss my savings goals'],'profile_fields':['preferred_language','name']}


def save(client: TestClient,ctx: RequestContext,target: dict[str,JsonValue],task: str) -> ActionOutcome:
    return propose(client,ctx,ActionCommand(type='agenda.save',idempotency_key=f'{ctx.case_id}:{task}:agenda.save',target=target))


def test_agenda_snapshot_edit_and_historical_receipt(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    target['content']='Forged content';target['categories']=['accounts'];target['profile_snapshot']={'name':'Another customer'}
    first=save(client,ctx,target,'first')
    assert first.receipt.result.status=='draft' and first.receipt.result.revision==1 and first.command.confirmed_at is None
    assert first.command.target.categories==['agenda','profile'] and first.command.target.profile_snapshot.name=='David Lee (DEMO)'
    assert first.command.target.profile_snapshot.employment_status is None
    assert 'Another customer' not in first.command.target.content
    assert save(client,ctx,target,'first')==first
    updated={**target,'expected_revision':1,'topics':['Discuss retirement','Review language preference'],'profile_fields':[]}
    second=save(client,ctx,updated,'edit')
    assert second.receipt.result.revision==2 and second.command.target.categories==['agenda']
    assert second.command.target.content=='Topics:\n- Discuss retirement\n- Review language preference'
    assert save(client,ctx,target,'first')==first
    stale=save(client,ctx,updated,'stale')
    assert stale.receipt.outcome=='failed'
    response=client.get('/relationship/agendas',headers=headers(ctx));assert response.status_code==200,response.text
    assert response.json()['data']['agendas']==[second.receipt.result.model_dump(mode='json')]
    assert response.json()['data']['agendas_total']==1
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select revision from rm.agendas').fetchone()==(2,)
        assert db.execute('select count(*) from rm.shared_summaries').fetchone()==(0,)
        assert db.execute('select count(*) from consent.grants').fetchone()==(0,)
        assert db.execute('select count(*) from conversation.turns').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_competing_edits_and_idempotent_retries(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    first=save(client,ctx,target,'original')
    def edit(index: int) -> ActionOutcome:
        return save(client,ctx,{**target,'expected_revision':1,'topics':[f'Edited topic {index}']},f'edit{index}')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(edit,range(2)))
    assert sorted(item.receipt.outcome for item in results)==['failed','succeeded']
    def retry(_: int) -> ActionOutcome:return save(client,ctx,target,'original')
    with ThreadPoolExecutor(max_workers=3) as pool:assert all(item==first for item in pool.map(retry,range(3)))
    with psycopg.connect(api.database_uri) as db:assert db.execute('select revision from rm.agendas').fetchone()==(2,)


def test_scope_redaction_and_direct_write_restrictions(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    target['topics']=['Contact me at agenda@example.invalid, OTP 123456'];saved=save(client,ctx,target,'private')
    assert 'agenda@example.invalid' not in saved.command.target.content and '123456' not in saved.command.target.content
    assert '[private detail removed]' in saved.command.target.content
    alex=action_case(client,api,1001)
    response=client.post('/commands',headers=headers(alex),json={'command_id':str(uuid4()),'case_id':str(alex.case_id),
        'task_id':'foreign','type':'agenda.save','target':target})
    assert response.status_code==403,response.text
    assert client.get('/relationship/agendas').status_code==401
    assert client.get('/relationship/agendas',headers=headers(alex)).json()['data']['agendas']==[]
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError):db.schema('rm').table('agendas').update({'topics':[]}).eq('customer_id',1003).execute()
        with pytest.raises(APIError):db.rpc('customer_read_agendas',{'p_customer_id':1001}).execute()
        with pytest.raises(APIError):db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),'p_type':'agenda.save',
            'p_target':{**target,'agenda_id':str(uuid4())},'p_idempotency_key':f'{ctx.case_id}:raw:agenda.save'}).execute()
    with psycopg.connect(api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where details::text like '%agenda@example.invalid%'").fetchone()==(0,)


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_agenda_save_rolls_back_atomically(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    with psycopg.connect(api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.fail_agenda() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'agenda audit failure'; end if; return new; end $$")
            db.execute('create trigger fail_agenda before insert on audit.events for each row execute function public.fail_agenda()')
        else:db.execute("alter table events.outbox add constraint fail_agenda check(event_type<>'agenda.saved')")
    body={'command_id':str(uuid4()),'case_id':str(ctx.case_id),'task_id':'rollback','type':'agenda.save','target':target}
    response=client.post('/commands',headers=headers(ctx),json=body);assert response.status_code==503,response.text
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.agendas').fetchone()==(0,)
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(0,)
        if failure=='audit':db.execute('drop trigger fail_agenda on audit.events')
        else:db.execute('alter table events.outbox drop constraint fail_agenda')
    assert client.post('/commands',headers=headers(ctx),json=body).json()['receipt']['outcome']=='succeeded'


def test_draft_notification_is_historical_and_scoped(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    with psycopg.connect(api.database_uri) as db:db.execute('update notify.preferences set quiet_start=null,quiet_end=null where customer_id=1003')
    save(client,ctx,target,'notify');save(client,ctx,{**target,'expected_revision':1,'topics':['A later draft']},'later')
    results=asyncio.run(drain(worker_uri));assert all(item.status=='done' for item in results),results
    assert asyncio.run(drain(worker_uri))==[]
    items=client.get('/notifications',headers=headers(ctx)).json()['items']
    assert len([item for item in items if item['template_key']=='agenda.saved'])==2
    assert not any(item['template_key']=='agenda.saved' for item in client.get('/notifications',headers=headers(context(api,1001))).json()['items'])


def test_invalid_topics_and_fields_fail_before_writes(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    for invalid in [{'topics':[]},{'topics':[' ']},{'topics':['x'*301]},{'topics':['x']*13},{'topics':[None]},
        {'profile_fields':['accounts']},{'profile_fields':['name','name']},{'expected_revision':True},{'expected_revision':-1}]:
        response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
            'task_id':'invalid','type':'agenda.save','target':{**target,**invalid}})
        assert response.status_code==422,response.text
        with SyncPostgrestClient(api.url) as db:
            db.auth(ctx.principal.jwt.get_secret_value())
            with pytest.raises(APIError) as failure:
                db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),'p_type':'agenda.save',
                    'p_target':{**target,**invalid},'p_idempotency_key':f'{ctx.case_id}:invalid:agenda.save'}).execute()
            assert failure.value.code=='22023'
    assert client.get('/relationship/agendas',headers=headers(ctx)).json()['data']['agendas']==[]


def test_assignment_changes_and_profile_bounds_never_save(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);target=agenda_target(api)
    with psycopg.connect(api.database_uri) as db:db.execute('update rm.advisors set is_active=false where advisor_id=%s',(target['advisor_id'],))
    assert save(client,ctx,target,'inactive').receipt.outcome=='failed'
    with psycopg.connect(api.database_uri) as db:
        db.execute('update rm.advisors set is_active=true where advisor_id=%s',(target['advisor_id'],))
        db.execute('update banking.customer_profiles set full_name=%s where customer_id=1003',('x'*2001,))
    body={'command_id':str(uuid4()),'case_id':str(ctx.case_id),'task_id':'oversized','type':'agenda.save','target':target}
    assert client.post('/commands',headers=headers(ctx),json=body).status_code==422
    with psycopg.connect(api.database_uri) as db:
        db.execute('update rm.assignments set ended_on=current_date where customer_id=1003 and ended_on is null')
        assert db.execute('select count(*) from rm.agendas').fetchone()==(0,)
    assert client.post('/commands',headers=headers(ctx),json={**body,'task_id':'ended'}).status_code==403
