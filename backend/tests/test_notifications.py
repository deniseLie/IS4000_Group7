"""Real restricted worker, transactional delivery, user JWT/RLS and inbox API integration."""
import asyncio
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit,urlunsplit
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app import worker
from app.core.config import settings
from app.schemas.notifications import ProcessResult
from tests.data_api_support import NativeAPI
from tests.test_actions_api import card_command,context,headers,owned_case,propose

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native database/API tests')


@pytest.fixture
def worker_uri(local_data_api: NativeAPI) -> Iterator[SecretStr]:
    import psycopg
    from psycopg import sql
    name='worker_'+uuid4().hex
    with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:
        db.execute(sql.SQL('create role {} login inherit').format(sql.Identifier(name)))
        db.execute(sql.SQL('grant demobank_worker to {}').format(sql.Identifier(name)))
        parts=urlsplit(local_data_api.database_uri)
        uri=urlunsplit(parts._replace(netloc=name+'@'+(parts.hostname or '')+((':'+str(parts.port)) if parts.port else '')))
    try:yield SecretStr(uri)
    finally:
        with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:db.execute(sql.SQL('drop role {}').format(sql.Identifier(name)))


async def drain(uri: SecretStr) -> list[ProcessResult]:
    results: list[ProcessResult]=[]
    async with worker.connect(uri) as db:
        for _ in range(20):
            result=await worker.process_once(db)
            if result is None:return results
            results.append(result)
    raise AssertionError('Test worker did not drain its bounded event queue')


async def produce_reminders(uri: SecretStr) -> int:
    async with worker.connect(uri) as db:return await worker.enqueue_reminders(db)


def block(api: NativeAPI,client: TestClient) -> UUID:
    ctx=owned_case(api,context(api,1001))
    command=card_command(ctx,'notify-block')
    propose(client,ctx,command)
    response=client.post(f'/commands/{command.command_id}/confirm',headers=headers(ctx))
    assert response.status_code==200,response.text
    return command.command_id


def test_confirmed_card_delivers_once_and_only_customer_can_read(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    command_id=block(local_data_api,native_api_client)
    results=asyncio.run(drain(worker_uri))
    assert all(item.status=='done' for item in results),results
    assert asyncio.run(drain(worker_uri))==[]
    ctx=context(local_data_api,1001)
    response=native_api_client.get('/notifications',headers=headers(ctx))
    assert response.status_code==200,response.text
    items=response.json()['items']
    assert len(items)==1 and items[0]['template_key']=='card.blocked' and items[0]['template_version']==2
    assert '4821' in items[0]['body'] and 'synthetic' in items[0]['body'] and items[0]['is_service']
    notification=items[0]['notification_id']
    first=native_api_client.post(f'/notifications/{notification}/read',headers=headers(ctx))
    assert first.status_code==200 and first.json()['read_at'] is not None
    assert native_api_client.post(f'/notifications/{notification}/read',headers=headers(ctx)).json()==first.json()
    foreign=context(local_data_api,1003)
    assert native_api_client.get('/notifications',headers=headers(foreign)).json()['items'][0]['customer_id']==1003
    assert native_api_client.post(f'/notifications/{notification}/read',headers=headers(foreign)).status_code==404
    assert native_api_client.get('/notifications').status_code==401
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from notify.inbox where customer_id=1001").fetchone()==(1,)
        assert db.execute("select count(*) from audit.events where action='notification.sent' and customer_id=1001").fetchone()==(1,)
        assert db.execute("select count(*) from audit.events where action='notification.read' and customer_id=1001").fetchone()==(1,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        assert db.execute('select status from events.outbox where command_id=%s',(command_id,)).fetchone()==('done',)


def test_worker_login_has_only_narrow_function_access(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    with psycopg.connect(worker_uri.get_secret_value(),autocommit=True) as db:
        for query in ['select * from events.outbox','select * from notify.inbox','select * from banking.customer_profiles',
            'select * from audit.events','select public.conversation_create(null,null,null,null)']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):db.execute(query)
    async def reject_admin() -> None:
        async with worker.connect(SecretStr(local_data_api.database_uri)):raise AssertionError('Privileged database owner was accepted')
    with pytest.raises(worker.WorkerPermissionError):asyncio.run(reject_admin())


def test_competing_workers_deliver_one_inbox_record_per_event(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    block(local_data_api,native_api_client)
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:asyncio.run(drain(worker_uri)),range(3)))
    assert sum(len(items) for items in results)==2
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from notify.inbox').fetchone()==(2,)
        assert db.execute("select count(*) from events.outbox where status='done'").fetchone()==(2,)


def test_service_delivery_ignores_optout_and_quiet_hours(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update notify.preferences set channels='{}',quiet_start='00:00',quiet_end='23:59:59.999999' where customer_id=1001")
    block(local_data_api,native_api_client)
    assert all(item.status=='done' for item in asyncio.run(drain(worker_uri)))
    ctx=context(local_data_api,1001)
    assert len(native_api_client.get('/notifications',headers=headers(ctx)).json()['items'])==1


@pytest.mark.parametrize('preference',['optout','quiet'])
def test_optional_delivery_honours_preferences(local_data_api: NativeAPI,worker_uri: SecretStr,preference: str) -> None:
    import psycopg
    request_id=uuid4()
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("insert into consent.requests(request_id,customer_id,purpose,recipient) values (%s,1001,'rm_brief','assigned_advisor')",(request_id,))
        event_id=db.execute("insert into events.outbox(event_type,customer_id,payload) values ('consent.requested',1001,jsonb_build_object('request_id',%s::text)) returning event_id",(str(request_id),)).fetchone()[0]
        if preference=='optout':db.execute("update notify.preferences set channels='{}' where customer_id=1001")
        else:db.execute("update notify.preferences set quiet_start=((clock_timestamp() at time zone 'Asia/Singapore')-interval '1 hour')::time,quiet_end=((clock_timestamp() at time zone 'Asia/Singapore')+interval '1 hour')::time where customer_id=1001")
    results=asyncio.run(drain(worker_uri))
    result=next(item for item in results if item.event_id==event_id)
    assert result.disposition==('suppressed' if preference=='optout' else 'deferred')
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from notify.inbox where customer_id=1001').fetchone()==(0,)
        if preference=='quiet':
            assert db.execute('select attempts,available_at>clock_timestamp() from events.outbox where event_id=%s',(event_id,)).fetchone()==(0,True)
            db.execute('update notify.preferences set quiet_start=null,quiet_end=null where customer_id=1001')
            db.execute('update events.outbox set available_at=clock_timestamp() where event_id=%s',(event_id,))
    if preference=='quiet':assert asyncio.run(drain(worker_uri))[0].disposition=='delivered'


def test_failed_template_retries_then_dead_letters_without_losing_action_receipt(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    command=block(local_data_api,native_api_client)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update banking.customer_profiles set preferred_language='unsupported' where customer_id=1001")
    for attempt in range(1,6):
        results=asyncio.run(drain(worker_uri))
        result=next(item for item in results if item.error_code)
        assert result.error_code=='P2001' and result.attempts==attempt
        assert result.status==('failed' if attempt==5 else 'pending')
        with psycopg.connect(local_data_api.database_uri) as db:
            if attempt<5:db.execute('update events.outbox set available_at=clock_timestamp() where command_id=%s',(command,))
    assert asyncio.run(drain(worker_uri))==[]
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select outcome from actions.receipts where command_id=%s',(command,)).fetchone()==('succeeded',)
        assert db.execute('select count(*) from notify.inbox where customer_id=1001').fetchone()==(0,)


def test_worker_audit_failure_rolls_back_inbox_and_event(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("create function public.native_notification_audit_failure() returns trigger language plpgsql as $$ begin if new.action='notification.sent' then raise exception 'native notification audit failure'; end if; return new; end $$")
        db.execute('create trigger native_notification_failure before insert on audit.events for each row execute function public.native_notification_audit_failure()')
    with pytest.raises(psycopg.errors.RaiseException,match='native notification audit failure'):asyncio.run(drain(worker_uri))
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status,attempts from events.outbox').fetchone()==('pending',0)
        assert db.execute('select count(*) from notify.inbox').fetchone()==(0,)
        db.execute('drop trigger native_notification_failure on audit.events')
    assert asyncio.run(drain(worker_uri))[0].disposition=='delivered'


def test_database_session_crash_rolls_back_uncommitted_delivery(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    crashed=psycopg.connect(worker_uri.get_secret_value())
    try:
        pid=crashed.execute('select pg_backend_pid()').fetchone()[0]
        assert crashed.execute('select events.process_next_notification()').fetchone()[0]['disposition']=='delivered'
        with psycopg.connect(local_data_api.database_uri,autocommit=True) as admin:
            assert admin.execute('select pg_terminate_backend(%s)',(pid,)).fetchone()==(True,)
        with pytest.raises(psycopg.OperationalError):crashed.execute('select 1')
    finally:crashed.close()
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status,attempts from events.outbox').fetchone()==('pending',0)
        assert db.execute('select count(*) from notify.inbox').fetchone()==(0,)
    assert asyncio.run(drain(worker_uri))[0].disposition=='delivered'


def test_reminder_producer_uses_recorded_due_dates_and_rechecks_open_items(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.info_requests set due_date=null where customer_id=1002")
        db.execute("update compliance.cases set due_date=(clock_timestamp() at time zone 'Asia/Singapore')::date+2 where customer_id=1002")
    assert asyncio.run(produce_reminders(worker_uri))==1
    assert asyncio.run(produce_reminders(worker_uri))==0
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("update compliance.requested_items set submission_status='submitted_awaiting_review' where customer_id=1002")
    results=asyncio.run(drain(worker_uri))
    assert any(item.disposition=='stale' for item in results)
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select count(*) from notify.inbox where customer_id=1002').fetchone()==(0,)


def test_reused_handoff_is_one_notification(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    from app.schemas.contracts import ActionCommand
    ctx=owned_case(local_data_api,context(local_data_api,1001))
    for task in ['one','two']:
        propose(native_api_client,ctx,ActionCommand(type='handoff.create',idempotency_key=f'{ctx.case_id}:{task}:handoff.create',
            target={'reason':'general_support','priority':'P3','summary':'Please review this synthetic case.'}))
    results=asyncio.run(drain(worker_uri))
    assert sum(item.disposition=='duplicate' for item in results)==1
    with psycopg.connect(local_data_api.database_uri) as db:assert db.execute('select count(*) from notify.inbox where customer_id=1001').fetchone()==(1,)


@pytest.mark.parametrize('days',[0,7,8])
def test_recorded_deadline_window_and_delivered_reminder(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr,days: int) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.info_requests set due_date=null where customer_id=1002")
        due_date=db.execute("update compliance.cases set due_date=(clock_timestamp() at time zone 'Asia/Singapore')::date+%s where customer_id=1002 returning due_date",(days,)).fetchone()[0]
    assert asyncio.run(produce_reminders(worker_uri))==(1 if days<=7 else 0)
    asyncio.run(drain(worker_uri))
    ctx=context(local_data_api,1002)
    response=native_api_client.get('/notifications',headers=headers(ctx))
    assert response.status_code==200,response.text
    items=response.json()['items']
    assert len(items)==(1 if days<=7 else 0)
    if items:
        assert str(due_date) in items[0]['body'] and items[0]['priority']=='P2'
        assert 'Proof of residential address' in items[0]['body']


@pytest.mark.parametrize('change',[None,'answered','changed_deadline','closed'])
def test_information_reminder_has_its_own_action_and_deadline(local_data_api,worker_uri,change):
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.cases set due_date=null where customer_id=1002")
        due=db.execute("update compliance.info_requests set due_date=(clock_timestamp() at time zone 'Asia/Singapore')::date+3 where customer_id=1002 returning due_date").fetchone()[0]
    assert asyncio.run(produce_reminders(worker_uri))==1
    assert asyncio.run(produce_reminders(worker_uri))==0
    with psycopg.connect(local_data_api.database_uri) as db:
        if change=='answered':db.execute("update compliance.info_requests set status='responded' where customer_id=1002")
        elif change=='changed_deadline':db.execute("update compliance.info_requests set due_date=due_date+1 where customer_id=1002")
        elif change=='closed':db.execute("update compliance.cases set status='closed' where customer_id=1002")
    results=asyncio.run(drain(worker_uri))
    with psycopg.connect(local_data_api.database_uri) as db:
        rows=db.execute("select body from notify.inbox where customer_id=1002").fetchall()
        if change:assert not rows and any(result.disposition=='stale' for result in results)
        else:
            assert len(rows)==1 and str(due) in rows[0][0]
            assert 'source' in rows[0][0].lower() and 'instructions' in rows[0][0].lower()
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_inheriting_app_read_privileges_is_refused(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    from psycopg import sql
    name=urlsplit(worker_uri.get_secret_value()).username
    with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:db.execute(sql.SQL('grant authenticated to {}').format(sql.Identifier(name)))
    async def connect() -> None:
        async with worker.connect(worker_uri):raise AssertionError('Worker with app table privileges was accepted')
    with pytest.raises(worker.WorkerPermissionError):asyncio.run(connect())


def test_inheriting_privileged_role_is_refused(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    from psycopg import sql
    name=urlsplit(worker_uri.get_secret_value()).username
    privileged='unsafe_'+uuid4().hex
    with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:
        db.execute(sql.SQL('create role {} nologin createdb').format(sql.Identifier(privileged)))
        db.execute(sql.SQL('grant {} to {}').format(sql.Identifier(privileged),sql.Identifier(name)))
    try:
        async def connect() -> None:
            async with worker.connect(worker_uri):raise AssertionError('Worker with an assumable privileged role was accepted')
        with pytest.raises(worker.WorkerPermissionError):asyncio.run(connect())
    finally:
        with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:db.execute(sql.SQL('drop role {}').format(sql.Identifier(privileged)))


def test_api_worker_failure_is_unhealthy_and_propagates(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr,monkeypatch: pytest.MonkeyPatch) -> None:
    import time
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("alter table events.outbox add constraint native_stop_worker check(status<>'processing')")
    monkeypatch.setattr(settings,'run_worker_in_api',True)
    monkeypatch.setattr(settings,'worker_database_url',worker_uri)
    with pytest.raises(psycopg.errors.CheckViolation):
        with TestClient(native_api_client.app) as client:
            deadline=time.monotonic()+5
            while True:
                response=client.get('/health')
                if response.status_code==503:break
                if time.monotonic()>=deadline:raise TimeoutError('A stopped worker did not degrade API health within five seconds')
                time.sleep(0.05)
            assert response.json()=={'status':'degraded','notification_worker':'stopped'}


def test_invalid_preferences_and_deferred_failure_are_explicit(local_data_api: NativeAPI,worker_uri: SecretStr) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri,autocommit=True) as db:
        for query in ["update notify.preferences set channels=array['email',null] where customer_id=1001",
            "update notify.preferences set quiet_start='10:00',quiet_end=null where customer_id=1001",
            "update notify.preferences set quiet_start='10:00',quiet_end='10:00' where customer_id=1001"]:
            with pytest.raises(psycopg.errors.CheckViolation):db.execute(query)
        db.execute("update banking.customer_profiles set preferred_language='unsupported' where customer_id=1003")
        db.execute("update events.outbox set disposition='deferred'")
    result=asyncio.run(drain(worker_uri))[0]
    assert result.error_code=='P2001' and result.disposition is None and result.status=='pending'
