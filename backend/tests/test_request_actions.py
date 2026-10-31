"""Confirmed synthetic request intake through real JWT/RLS, receipt and outbox transactions."""
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
from tests.test_consent_actions import action_case,confirm,recipient
from tests.test_notifications import drain,worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native database/API integrations')


def target(api: NativeAPI) -> dict[str,JsonValue]:
    return {'advisor_id':recipient(api),'what':'Discuss my synthetic overseas planning request','urgency':'normal','deadline':None,'country':'SG','currency':'SGD'}


def request(client: TestClient,ctx: RequestContext,values: dict[str,JsonValue],task: str) -> ActionOutcome:
    return propose(client,ctx,ActionCommand(type='requests.create',target=values,idempotency_key=f'{ctx.case_id}:{task}:requests.create'))


def test_confirmed_intake_and_historical_worker_receipt(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);values=target(api)
    values.update({'who':'Forged customer','recipient_name':'Forged advisor','status':'answered'})
    pending=request(client,ctx,values,'first');assert pending.receipt is None and pending.command.state=='proposed'
    assert pending.command.target.who=='David Lee (DEMO)' and pending.command.target.recipient_name=='Sophie Lim (DEMO)'
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(0,)
    def execute(_: int) -> ActionOutcome:return confirm(client,ctx,pending.command.command_id)
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(execute,range(3)))
    assert all(item==results[0] for item in results);saved=results[0]
    assert saved.receipt.result.status=='sent' and saved.command.confirmed_at is not None
    assert request(client,ctx,values,'first')==saved
    listed=client.get('/relationship/requests',headers=headers(ctx));assert listed.status_code==200,listed.text
    assert any(item['request_id']==str(saved.receipt.result.request_id) and item['country']=='SG' for item in listed.json()['data'])
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(1,)
        db.execute("update rm.requests set status='seen' where request_id=%s",(saved.receipt.result.request_id,))
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    assert confirm(client,ctx,pending.command.command_id)==saved
    results=asyncio.run(drain(worker_uri));assert all(item.status=='done' for item in results),results
    assert asyncio.run(drain(worker_uri))==[]
    assert len([item for item in client.get('/notifications',headers=headers(ctx)).json()['items'] if item['template_key']=='rm.request_recorded'])==1


@pytest.mark.parametrize('change',['assignment','advisor','name','profile','deadline'])
def test_changed_target_never_submits(local_data_api: NativeAPI,native_api_client: TestClient,change: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);values=target(api)
    if change=='deadline':values['deadline']='2020-01-01'
    pending=request(client,ctx,values,'changed')
    with psycopg.connect(api.database_uri) as db:
        if change=='assignment':db.execute('update rm.assignments set ended_on=current_date where customer_id=1003 and ended_on is null')
        elif change=='advisor':db.execute('update rm.advisors set is_active=false where advisor_id=%s',(values['advisor_id'],))
        elif change=='name':db.execute("update rm.advisors set full_name='Changed (DEMO)' where advisor_id=%s",(values['advisor_id'],))
        elif change=='profile':db.execute("update banking.customer_profiles set full_name='Changed (DEMO)' where customer_id=1003")
    failed=confirm(client,ctx,pending.command.command_id);assert failed.receipt.outcome=='failed'
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(0,)


def test_scope_redaction_rejection_and_expiry(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);alex=action_case(client,api,1001);values=target(api)
    values['what']='Contact me at request@example.invalid, OTP 123456'
    pending=request(client,ctx,values,'private');assert 'request@example.invalid' not in pending.command.target.what and '123456' not in pending.command.target.what
    for suffix in ['', '/confirm','/reject']:
        response=client.get(f'/commands/{pending.command.command_id}',headers=headers(alex)) if not suffix else client.post(f'/commands/{pending.command.command_id}{suffix}',headers=headers(alex))
        assert response.status_code==404,response.text
    denied=client.post('/commands',headers=headers(alex),json={'command_id':str(uuid4()),'case_id':str(alex.case_id),'task_id':'foreign','type':'requests.create','target':values})
    assert denied.status_code==403,denied.text
    assert client.post(f'/commands/{pending.command.command_id}/reject',headers=headers(ctx)).json()['command']['state']=='rejected'
    expired=request(client,ctx,target(api),'expired')
    with psycopg.connect(api.database_uri) as db:
        db.execute("update actions.commands set proposed_at=clock_timestamp()-interval '6 minutes',expires_at=clock_timestamp()-interval '1 second' where command_id=%s",(expired.command.command_id,))
    assert client.post(f'/commands/{expired.command.command_id}/confirm',headers=headers(ctx)).status_code==409
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError):db.schema('rm').table('requests').insert({'customer_id':1003,'who':'David','what':'Direct write','urgency':'normal'}).execute()
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(0,)
        assert db.execute("select count(*) from audit.events where details::text like '%request@example.invalid%'").fetchone()==(0,)


@pytest.mark.parametrize('invalid',[{'what':' '},{'what':'x'*1001},{'urgency':'urgent'},{'country':'sg'},{'currency':'US'},
    {'deadline':'2026-02-30'},{'deadline':True}])
def test_invalid_intake_fails_before_proposal(local_data_api: NativeAPI,native_api_client: TestClient,invalid: dict[str,JsonValue]) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);values={**target(api),**invalid}
    response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),'task_id':'invalid','type':'requests.create','target':values})
    assert response.status_code==422,response.text
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError) as failure:db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),'p_type':'requests.create',
            'p_target':values,'p_idempotency_key':f'{ctx.case_id}:invalid:requests.create'}).execute()
        assert failure.value.code=='22023'
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from actions.commands').fetchone()==(0,)


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_confirmation_rolls_back_with_receipt(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);pending=request(client,ctx,target(api),'rollback')
    with psycopg.connect(api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.fail_request() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'request audit failure'; end if; return new; end $$")
            db.execute('create trigger fail_request before insert on audit.events for each row execute function public.fail_request()')
        else:db.execute("alter table events.outbox add constraint fail_request check(event_type<>'rm.request_recorded')")
    response=client.post(f'/commands/{pending.command.command_id}/confirm',headers=headers(ctx));assert response.status_code==503,response.text
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.requests where command_id is not null').fetchone()==(0,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(0,)
        assert db.execute('select state,confirmed_at from actions.commands').fetchone()==('proposed',None)
