"""Real booking actions, JWT scope, concurrent reservations, expiry and transactional rollback."""
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import SecretStr
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers,owned_case,propose
from tests.test_relationship_api import monday,query
from tests.test_notifications import drain,worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in with both native database/API flags')


def available_slot(client: TestClient,api: NativeAPI) -> dict[str,str]:
    response=client.post('/relationship/slots',headers=headers(context(api,1003)),json=query(monday()))
    assert response.status_code==200,response.text
    return {**response.json()['data'][0],'format':'video'}


def hold(client: TestClient,api: NativeAPI,target: dict[str,str]) -> ActionOutcome:
    response=client.post('/relationship/holds',headers=headers(context(api,1003)),json={
        'command_id':str(uuid4()),'session_id':str(uuid4()),'case_id':str(uuid4()),'target':target})
    assert response.status_code==200,response.text
    return ActionOutcome.model_validate(response.json())


def test_hold_confirm_cancel_replay_and_current_records(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    client=native_api_client; api=local_data_api; ctx=context(api,1003)
    slot=available_slot(client,api)
    body={'command_id':str(uuid4()),'session_id':str(uuid4()),'case_id':str(uuid4()),'target':slot}
    response=client.post('/relationship/holds',headers=headers(ctx),json=body)
    assert response.status_code==200,response.text
    held=ActionOutcome.model_validate(response.json())
    assert held.command.state=='succeeded' and held.command.confirmed_at is None
    assert held.receipt.result.status=='held'
    remaining=held.receipt.result.hold_expires_at-held.receipt.created_at
    assert timedelta(minutes=10)<=remaining<timedelta(minutes=10,seconds=5)
    replay=client.post('/relationship/holds',headers=headers(ctx),json=body)
    assert replay.json()==response.json()
    assert client.post('/relationship/holds',headers=headers(ctx),json={**body,'target':{**slot,'format':'phone'}}).status_code==409
    ctx=ctx.model_copy(update={'case_id':held.command.case_id})
    target={'appointment_id':str(held.receipt.result.appointment_id)}
    command=ActionCommand(type='appointment.confirm',target=target,idempotency_key=f'{ctx.case_id}:confirm:appointment.confirm')
    pending=propose(client,ctx,command)
    assert pending.command.state=='proposed' and pending.receipt is None
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select status from rm.appointments').fetchall()==[('held',)]
    def confirm(_: int) -> ActionOutcome:
        result=client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
        assert result.status_code==200,result.text
        return ActionOutcome.model_validate(result.json())
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(confirm,range(3)))
    assert all(result==results[0] for result in results)
    assert results[0].receipt.result.status=='confirmed' and results[0].command.confirmed_at is not None
    cancel=ActionCommand(type='appointment.cancel',target=target,idempotency_key=f'{ctx.case_id}:cancel:appointment.cancel')
    assert propose(client,ctx,cancel).command.state=='proposed'
    cancelled=client.post(f'/commands/{cancel.command_id}/confirm',headers=headers(ctx))
    assert cancelled.status_code==200 and cancelled.json()['receipt']['result']['status']=='cancelled',cancelled.text
    current=client.get('/relationship/appointments',headers=headers(ctx))
    assert current.status_code==200 and current.json()['data'][0]['status']=='cancelled',current.text
    assert current.json()['data'][0]['case_id']==str(ctx.case_id)
    assert hold(client,api,slot).receipt.result.status=='held'
    assert client.post('/relationship/holds',headers=headers(ctx),json=body).json()==response.json()
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from actions.receipts where command_id=%s',(command.command_id,)).fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        assert db.execute('select count(*) from conversation.messages').fetchone()==(0,)


def test_overlapping_holds_are_excluded_across_customers_and_constraint_is_real(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    from psycopg.errors import ExclusionViolation
    api=local_data_api; client=native_api_client; slot=available_slot(client,api)
    with psycopg.connect(api.database_uri) as db:
        db.execute("delete from rm.assignments where customer_id=1001")  # Alex's own seeded advisor (0063) is replaced for this cross-customer check
        db.execute('insert into rm.assignments(customer_id,advisor_id,started_on) values(1001,%s,current_date)',(slot['advisor_id'],))
    def reserve(cid: int) -> ActionOutcome:
        response=client.post('/relationship/holds',headers=headers(context(api,cid)),json={
            'command_id':str(uuid4()),'session_id':str(uuid4()),'case_id':str(uuid4()),'target':slot})
        assert response.status_code==200,response.text
        return ActionOutcome.model_validate(response.json())
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,[1001,1003]))
    assert sorted(result.command.state for result in results)==['failed','succeeded']
    failed=next(result for result in results if result.command.state=='failed')
    assert failed.receipt.result.error_code=='target_changed'
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.appointments').fetchone()==(1,)
        with pytest.raises(ExclusionViolation):
            with db.transaction():
                db.execute("insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status) values(1002,%s,%s,%s,'phone','confirmed')",
                    (slot['advisor_id'],slot['starts_at'],slot['ends_at']))
    for cid in [1001,1003]:
        response=client.get('/relationship/appointments',headers=headers(context(api,cid)))
        assert response.status_code==200
        assert len(response.json()['data'])==int(any(result.command.customer_id==cid and result.command.state=='succeeded' for result in results))


def test_expired_hold_cannot_confirm_and_slot_can_be_held_again(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; slot=available_slot(client,api)
    held=hold(client,api,slot); ctx=context(api,1003).model_copy(update={'case_id':held.command.case_id})
    command=ActionCommand(type='appointment.confirm',target={'appointment_id':str(held.receipt.result.appointment_id)},
        idempotency_key=f'{ctx.case_id}:expired:appointment.confirm')
    propose(client,ctx,command)
    with psycopg.connect(api.database_uri) as db:
        db.execute("update rm.appointments set hold_expires_at=clock_timestamp()-interval '1 second'")
    listed=client.get('/relationship/appointments',headers=headers(ctx))
    assert listed.json()['data'][0]['status']=='expired'
    result=client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert result.status_code==200 and result.json()['receipt']['outcome']=='failed',result.text
    assert hold(client,api,slot).receipt.result.status=='held'
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select status,count(*) from rm.appointments group by status order by status').fetchall()==[('expired',1),('held',1)]


@pytest.mark.parametrize('change',['assignment','block','hours'])
def test_confirmation_rechecks_calendar_and_assignment(local_data_api: NativeAPI,native_api_client: TestClient,change: str) -> None:
    import psycopg
    api=local_data_api; client=native_api_client; slot=available_slot(client,api); held=hold(client,api,slot)
    ctx=context(api,1003).model_copy(update={'case_id':held.command.case_id})
    command=ActionCommand(type='appointment.confirm',target={'appointment_id':str(held.receipt.result.appointment_id)},
        idempotency_key=f'{ctx.case_id}:changed:appointment.confirm')
    propose(client,ctx,command)
    with psycopg.connect(api.database_uri) as db:
        if change=='assignment':db.execute('update rm.assignments set ended_on=current_date where customer_id=1003 and ended_on is null')
        elif change=='block':db.execute('insert into rm.advisor_blocks(advisor_id,starts_at,ends_at) values(%s,%s,%s)',(slot['advisor_id'],slot['starts_at'],slot['ends_at']))
        else:db.execute('delete from rm.working_hours where advisor_id=%s',(slot['advisor_id'],))
    result=client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert result.status_code==200 and result.json()['receipt']['outcome']=='failed',result.text


def test_scope_invalid_grid_rejection_and_legacy_records(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api; client=native_api_client; slot=available_slot(client,api); held=hold(client,api,slot)
    assert client.get('/relationship/appointments').status_code==401
    assert client.get('/relationship/appointments',headers=headers(context(api,1001))).json()['data']==[]
    ctx=owned_case(api,context(api,1001))
    assert client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
        'task_id':'foreign','type':'appointment.cancel','target':{'appointment_id':str(held.receipt.result.appointment_id)}}).status_code==403
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError):db.schema('rm').table('appointments').update({'status':'confirmed'}).eq('customer_id',1003).execute()
    shifted={**slot,'starts_at':(datetime.fromisoformat(slot['starts_at'])+timedelta(minutes=1)).isoformat(),
        'ends_at':(datetime.fromisoformat(slot['ends_at'])+timedelta(minutes=1)).isoformat()}
    assert hold(client,api,shifted).receipt.outcome=='failed'
    for target in [{**slot,'starts_at':'2026-01-01T09:00:00'},{**slot,'format':'invalid'},{**slot,'ends_at':slot['starts_at']}]:
        assert client.post('/relationship/holds',headers=headers(context(api,1003)),json={'command_id':str(uuid4()),
            'session_id':str(uuid4()),'case_id':str(uuid4()),'target':target}).status_code==422
    ctx=context(api,1003).model_copy(update={'case_id':held.command.case_id})
    rejected=ActionCommand(type='appointment.confirm',target={'appointment_id':str(held.receipt.result.appointment_id)},
        idempotency_key=f'{ctx.case_id}:reject:appointment.confirm')
    propose(client,ctx,rejected)
    response=client.post(f'/commands/{rejected.command_id}/reject',headers=headers(ctx))
    assert response.status_code==200 and response.json()['command']['state']=='rejected'
    assert client.get('/relationship/appointments',headers=headers(ctx)).json()['data'][0]['status']=='held'


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_hold_rolls_back_with_its_case_receipt_and_audit(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    slot=available_slot(native_api_client,local_data_api)
    with psycopg.connect(local_data_api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.native_booking_audit() returns trigger language plpgsql as $$ begin if new.action='action.executed' then raise exception 'native audit failure'; end if; return new; end $$")
            db.execute('create trigger native_booking_audit before insert on audit.events for each row execute function public.native_booking_audit()')
        else:db.execute("alter table events.outbox add constraint native_booking_outbox check(event_type<>'booking.held')")
    response=native_api_client.post('/relationship/holds',headers=headers(context(local_data_api,1003)),json={
        'command_id':str(uuid4()),'session_id':str(uuid4()),'case_id':str(uuid4()),'target':slot})
    assert response.status_code==503,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        for table in ['rm.appointments','actions.commands','actions.receipts','conversation.cases','conversation.sessions']:
            assert db.execute(f'select count(*) from {table}').fetchone()==(0,)


def test_worker_delivers_verified_booking_receipts_once(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    api=local_data_api; client=native_api_client; slot=available_slot(client,api); held=hold(client,api,slot)
    ctx=context(api,1003).model_copy(update={'case_id':held.command.case_id})
    for action_type in ['appointment.confirm','appointment.cancel']:
        command=ActionCommand(type=action_type,target={'appointment_id':str(held.receipt.result.appointment_id)},
            idempotency_key=f'{ctx.case_id}:notify:{action_type}')
        propose(client,ctx,command)
        response=client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
        assert response.status_code==200 and response.json()['receipt']['outcome']=='succeeded',response.text
    results=asyncio.run(drain(worker_uri))
    assert all(result.status=='done' for result in results),results
    assert asyncio.run(drain(worker_uri))==[]
    notifications=client.get('/notifications',headers=headers(ctx))
    assert notifications.status_code==200,notifications.text
    items=[item for item in notifications.json()['items'] if item['template_key'].startswith('booking.')]
    assert sorted(item['template_key'] for item in items)==['booking.cancelled','booking.confirmed','booking.held']
    assert all(item['is_service'] and item['customer_id']==1003 for item in items)
    foreign=client.get('/notifications',headers=headers(context(api,1001)))
    assert not any(item['template_key'].startswith('booking.') for item in foreign.json()['items'])
