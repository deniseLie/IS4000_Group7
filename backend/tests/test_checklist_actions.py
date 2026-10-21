"""Real checklist preparation writes preserve bank decisions and commit receipts atomically."""
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
from tests.test_consent_actions import action_case
from tests.test_notifications import drain,worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native database/API integrations')
CASE='00000000-0000-4000-8000-100200000001'
ITEM='00000000-0000-4000-8000-100200000011'


def target() -> dict[str,JsonValue]:
    return {'case_id':CASE,'item_id':ITEM,'client_mark':'prepared','expected_client_mark':'not_prepared','expected_client_marked_at':None}


def save(client: TestClient,ctx: RequestContext,values: dict[str,JsonValue],task: str) -> ActionOutcome:
    return propose(client,ctx,ActionCommand(type='compliance.update_checklist_item',target=values,
        idempotency_key=f'{ctx.case_id}:{task}:compliance.update_checklist_item'))


def test_preparation_history_idempotency_and_worker(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1002);values=target()
    values.update({'submission_status':'accepted','case_status':'completed','item_label':'Forged'})
    first=save(client,ctx,values,'first')
    assert first.receipt.outcome=='succeeded' and first.command.confirmed_at is None
    assert first.receipt.result.submission_status=='not_submitted' and first.command.target.item_label=='Proof of residential address'
    assert save(client,ctx,values,'first')==first
    changed={**target(),'client_mark':'not_prepared','expected_client_mark':'prepared',
        'expected_client_marked_at':first.receipt.result.client_marked_at.isoformat()}
    second=save(client,ctx,changed,'undo');assert second.receipt.result.client_mark=='not_prepared'
    assert save(client,ctx,values,'first')==first
    current=client.get('/customer/compliance',headers=headers(ctx));assert current.status_code==200,current.text
    item=next(i for i in current.json()['data']['requested_items'] if i['item_id']==ITEM)
    assert item['client_mark']=='not_prepared' and item['submission_status']=='not_submitted'
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select status from compliance.cases where case_id=%s',(CASE,)).fetchone()==('awaiting_client_documents',)
        assert db.execute('select count(*) from compliance.documents').fetchone()==(0,)
        assert db.execute('select count(*) from compliance.status_history').fetchone()==(4,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    results=asyncio.run(drain(worker_uri));assert all(item.status=='done' for item in results),results
    assert asyncio.run(drain(worker_uri))==[]
    inbox=client.get('/notifications',headers=headers(ctx)).json()['items']
    assert len([item for item in inbox if item['template_key']=='compliance.checklist_prepared'])==2


def test_competing_marks_and_aba_stale_reads(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1002)
    def write(index: int) -> ActionOutcome:return save(client,ctx,target(),f'compete{index}')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,range(2)))
    assert sorted(item.receipt.outcome for item in results)==['failed','succeeded']
    first=next(item for item in results if item.receipt.outcome=='succeeded')
    save(client,ctx,{**target(),'client_mark':'not_prepared','expected_client_mark':'prepared',
        'expected_client_marked_at':first.receipt.result.client_marked_at.isoformat()},'undo')
    assert save(client,ctx,target(),'aba').receipt.outcome=='failed'


@pytest.mark.parametrize('status',['completed','closed'])
def test_closed_reviews_do_not_change_preparation(local_data_api: NativeAPI,native_api_client: TestClient,status: str) -> None:
    import psycopg
    ctx=action_case(native_api_client,local_data_api,1002)
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('update compliance.cases set status=%s where case_id=%s',(status,CASE))
    assert save(native_api_client,ctx,target(),'closed').receipt.outcome=='failed'


def test_scope_and_direct_write_denials(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api;client=native_api_client;alex=action_case(client,api,1001);michelle=action_case(client,api,1002)
    for ctx,values in [(alex,target()),(michelle,{**target(),'case_id':str(uuid4())}),(michelle,{**target(),'item_id':str(uuid4())})]:
        response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
            'task_id':'foreign','type':'compliance.update_checklist_item','target':values})
        assert response.status_code==403,response.text
    saved=save(client,michelle,target(),'owned')
    assert client.get(f'/commands/{saved.command.command_id}',headers=headers(alex)).status_code==404
    with SyncPostgrestClient(api.url) as db:
        db.auth(michelle.principal.jwt.get_secret_value())
        with pytest.raises(APIError):db.schema('compliance').table('requested_items').update({'client_mark':'prepared'}).eq('item_id',ITEM).execute()


@pytest.mark.parametrize('invalid',[{'client_mark':'accepted'},{'expected_client_marked_at':'yesterday'},
    {'expected_client_marked_at':'2026-10-20T01:00:00'},{'expected_client_mark':True},{'client_mark':'not_prepared'}])
def test_invalid_marks_fail_before_writes(local_data_api: NativeAPI,native_api_client: TestClient,invalid: dict[str,JsonValue]) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1002);values={**target(),**invalid}
    response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
        'task_id':'invalid','type':'compliance.update_checklist_item','target':values})
    assert response.status_code==422,response.text
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError) as failure:db.rpc('action_propose',{'p_command_id':str(uuid4()),'p_case_id':str(ctx.case_id),
            'p_type':'compliance.update_checklist_item','p_target':values,'p_idempotency_key':f'{ctx.case_id}:invalid:compliance.update_checklist_item'}).execute()
        assert failure.value.code=='22023'
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from actions.commands').fetchone()==(0,)


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_preparation_rolls_back_with_receipt_and_event(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1002)
    with psycopg.connect(api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.fail_checklist() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'checklist audit failure'; end if; return new; end $$")
            db.execute('create trigger fail_checklist before insert on audit.events for each row execute function public.fail_checklist()')
        else:db.execute("alter table events.outbox add constraint fail_checklist check(event_type<>'compliance.checklist_prepared')")
    response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
        'task_id':'rollback','type':'compliance.update_checklist_item','target':target()})
    assert response.status_code==503,response.text
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select client_mark,client_marked_at from compliance.requested_items where item_id=%s',(ITEM,)).fetchone()==('not_prepared',None)
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute('select count(*) from actions.receipts').fetchone()==(0,)
