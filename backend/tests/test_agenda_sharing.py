"""Confirmed sharing against real SQL/JWT services, including consent races and immutable receipts."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue,SecretStr
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import ActionCommand,RequestContext
from tests.data_api_support import NativeAPI
from tests.test_actions_api import headers,propose
from tests.test_agendas import agenda_target,save
from tests.test_consent_actions import action_case,confirm,grant_proposal,request_consent
from tests.test_notifications import drain,worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in with native database/API flags')


def grant(client: TestClient,api: NativeAPI,ctx: RequestContext,categories: list[str],task: str) -> ActionOutcome:
    requested=request_consent(client,api,ctx,categories,task)
    pending=grant_proposal(client,ctx,requested.receipt.result.request_id,task)
    return confirm(client,ctx,pending.command.command_id)


def share(client: TestClient,ctx: RequestContext,target: dict[str,JsonValue],task: str) -> ActionOutcome:
    return propose(client,ctx,ActionCommand(type='rm.share_summary',idempotency_key=f'{ctx.case_id}:{task}:rm.share_summary',target=target))


def target_for(saved: ActionOutcome,granted: ActionOutcome) -> dict[str,JsonValue]:
    result=saved.receipt.result
    return {'agenda_id':str(result.agenda_id),'agenda_revision':result.revision,'advisor_id':str(result.advisor_id),
        'consent_grant_id':str(granted.receipt.result.grant_id)}


def test_reviewed_content_confirm_retry_edit_and_revoke(local_data_api: NativeAPI,native_api_client: TestClient,worker_uri: SecretStr) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);draft=agenda_target(api)
    saved=save(client,ctx,draft,'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    target=target_for(saved,granted)
    pending=share(client,ctx,{**target,'content':'Forged text','categories':['accounts']},'share')
    assert pending.command.state=='proposed' and pending.receipt is None
    assert pending.command.target.content==saved.command.target.content and pending.command.target.categories==['agenda','profile']
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:confirm(client,ctx,pending.command.command_id),range(3)))
    assert all(item==results[0] for item in results)
    shared=results[0];assert shared.receipt.result.status=='shared'
    save(client,ctx,{**draft,'expected_revision':1,'topics':['Changed later'],'profile_fields':[]},'edit')
    revoked=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:revoke:consent.revoke',
        target={'grant_id':str(granted.receipt.result.grant_id)}))
    assert confirm(client,ctx,revoked.command.command_id).receipt.outcome=='succeeded'
    assert share(client,ctx,target,'share')==shared
    assert confirm(client,ctx,pending.command.command_id)==shared
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select content,agenda_revision,command_id from rm.shared_summaries where command_id is not null').fetchone()==(saved.command.target.content,1,pending.command.command_id)
        assert db.execute('select revision,status from rm.agendas').fetchone()==(2,'draft')
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    assert client.get('/commands',headers=headers(ctx)).status_code==200
    assert client.get('/relationship/agendas',headers=headers(ctx)).status_code==200
    assert client.get(f'/commands/{pending.command.command_id}',headers=headers(action_case(client,api,1001))).status_code==404
    asyncio.run(drain(worker_uri))
    inbox=client.get('/notifications',headers=headers(ctx)).json()
    assert len([item for item in inbox['items'] if item['template_key']=='rm.summary_shared'])==1


@pytest.mark.parametrize('change',['revoked','expired','future','agenda','assignment','inactive_advisor','recipient_name','grant_scope','grant_purpose','grant_recipient'])
def test_changes_after_review_fail_without_sharing(local_data_api: NativeAPI,native_api_client: TestClient,change: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003);draft=agenda_target(api)
    saved=save(client,ctx,draft,'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    pending=share(client,ctx,target_for(saved,granted),'share')
    with psycopg.connect(api.database_uri) as db:
        gid=granted.receipt.result.grant_id
        if change=='revoked':db.execute('update consent.grants set revoked_at=clock_timestamp() where grant_id=%s',(gid,))
        elif change=='expired':db.execute("update consent.grants set expires_at=clock_timestamp()-interval '1 second' where grant_id=%s",(gid,))
        elif change=='future':db.execute("update consent.grants set granted_at=clock_timestamp()+interval '1 day' where grant_id=%s",(gid,))
        elif change=='assignment':db.execute('update rm.assignments set ended_on=current_date where customer_id=1003 and ended_on is null')
        elif change=='inactive_advisor':db.execute('update rm.advisors set is_active=false where advisor_id=%s',(saved.command.target.advisor_id,))
        elif change=='recipient_name':db.execute("update rm.advisors set full_name='Changed advisor (DEMO)' where advisor_id=%s",(saved.command.target.advisor_id,))
        elif change=='grant_scope':db.execute("update consent.grants set categories=array['agenda'] where grant_id=%s",(gid,))
        elif change=='grant_purpose':db.execute("update consent.grants set purpose='other_brief' where grant_id=%s",(gid,))
        elif change=='grant_recipient':db.execute('update consent.grants set recipient=%s where grant_id=%s',(str(uuid4()),gid))
    if change=='agenda':save(client,ctx,{**draft,'expected_revision':1,'topics':['Edited after proposal']},'edit')
    result=confirm(client,ctx,pending.command.command_id)
    assert result.receipt.outcome=='failed' and result.receipt.result.error_code=='target_changed'
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)
        assert db.execute("select count(*) from events.outbox where event_type='rm.summary_shared'").fetchone()==(0,)


def test_partial_grants_never_combine_and_foreign_targets_are_denied(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,agenda_target(api),'draft');partial=grant(client,api,ctx,['agenda'],'agenda')
    grant(client,api,ctx,['profile'],'profile')
    pending=share(client,ctx,target_for(saved,partial),'share')
    assert confirm(client,ctx,pending.command.command_id).receipt.outcome=='failed'
    target=target_for(saved,partial)
    for owner,payload in [(action_case(client,api,1001),target),(ctx,{**target,'consent_grant_id':str(uuid4())}),
        (ctx,{**target,'advisor_id':str(uuid4())}),(ctx,{**target,'agenda_id':str(uuid4())})]:
        response=client.post('/commands',headers=headers(owner),json={'command_id':str(uuid4()),'case_id':str(owner.case_id),
            'task_id':'foreign','type':'rm.share_summary','target':payload})
        assert response.status_code==403,response.text
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)


def test_agenda_only_grant_shares_without_adding_profile_fields(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,{**agenda_target(api),'profile_fields':[]},'draft')
    granted=grant(client,api,ctx,['agenda'],'consent')
    pending=share(client,ctx,target_for(saved,granted),'share')
    result=confirm(client,ctx,pending.command.command_id)
    assert result.receipt.outcome=='succeeded' and result.receipt.result.profile_fields==[]
    assert result.receipt.result.categories==['agenda'] and 'David' not in result.receipt.result.content


@pytest.mark.parametrize('categories',[[],['agenda','agenda'],['agenda','profile','unsupported']])
def test_invalid_legacy_grant_cannot_persist_an_unreadable_proposal(local_data_api: NativeAPI,native_api_client: TestClient,categories: list[str]) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,agenda_target(api),'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    with psycopg.connect(api.database_uri) as db:
        db.execute('update consent.grants set categories=%s where grant_id=%s',(categories,granted.receipt.result.grant_id))
    response=client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':str(ctx.case_id),
        'task_id':'invalid','type':'rm.share_summary','target':target_for(saved,granted)})
    assert response.status_code==422,response.text
    with psycopg.connect(api.database_uri) as db:
        assert db.execute("select count(*) from actions.commands where type='rm.share_summary'").fetchone()==(0,)


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_sharing_failure_rolls_back_before_a_retry(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,agenda_target(api),'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    pending=share(client,ctx,target_for(saved,granted),'share')
    with psycopg.connect(api.database_uri) as db:
        if failure=='outbox':db.execute("alter table events.outbox add constraint sharing_fail check(event_type<>'rm.summary_shared')")
        else:
            db.execute("create function public.sharing_fail() returns trigger language plpgsql as $$ begin if new.action='action.summary_shared' then raise exception 'synthetic sharing audit failure'; end if; return new; end $$")
            db.execute('create trigger sharing_fail before insert on audit.events for each row execute function public.sharing_fail()')
    response=client.post(f'/commands/{pending.command.command_id}/confirm',headers=headers(ctx))
    assert response.status_code==503,response.text
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)
        assert db.execute('select state,confirmed_at from actions.commands where command_id=%s',(pending.command.command_id,)).fetchone()==('proposed',None)
        assert db.execute('select count(*) from actions.receipts where command_id=%s',(pending.command.command_id,)).fetchone()==(0,)
        if failure=='outbox':db.execute('alter table events.outbox drop constraint sharing_fail')
        else:db.execute('drop trigger sharing_fail on audit.events')
    assert confirm(client,ctx,pending.command.command_id).receipt.outcome=='succeeded'


def test_consent_expiry_is_rechecked_after_waiting_for_the_customer_lock(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,agenda_target(api),'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    with psycopg.connect(api.database_uri) as db:
        db.execute("update consent.grants set expires_at=clock_timestamp()+interval '2 seconds' where grant_id=%s",(granted.receipt.result.grant_id,))
    pending=share(client,ctx,target_for(saved,granted),'share')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(api.database_uri) as db:
            db.execute("select pg_advisory_xact_lock(hashtext('consent.customer'),hashtext('1003'))")
            future=pool.submit(confirm,client,ctx,pending.command.command_id)
            db.execute('select pg_sleep(2.2)')
            assert not future.done()
        result=future.result(timeout=10)
    assert result.receipt.outcome=='failed'
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)


def test_concurrent_revocation_cannot_authorise_later_sharing(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,agenda_target(api),'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    pending=share(client,ctx,target_for(saved,granted),'share')
    revocation=propose(client,ctx,ActionCommand(type='consent.revoke',idempotency_key=f'{ctx.case_id}:revoke:consent.revoke',
        target={'grant_id':str(granted.receipt.result.grant_id)}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        share_future=pool.submit(confirm,client,ctx,pending.command.command_id)
        revoke_future=pool.submit(confirm,client,ctx,revocation.command.command_id)
        shared=share_future.result(timeout=10);revoked=revoke_future.result(timeout=10)
    assert revoked.receipt.outcome=='succeeded'
    if shared.receipt.outcome=='succeeded':assert shared.receipt.result.shared_at<=revoked.receipt.result.revoked_at
    with psycopg.connect(api.database_uri) as db:
        assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(int(shared.receipt.outcome=='succeeded'),)


@pytest.mark.parametrize('decision',['reject','expire'])
def test_rejected_or_expired_sharing_never_executes(local_data_api: NativeAPI,native_api_client: TestClient,decision: str) -> None:
    import psycopg
    api=local_data_api;client=native_api_client;ctx=action_case(client,api,1003)
    saved=save(client,ctx,agenda_target(api),'draft');granted=grant(client,api,ctx,['agenda','profile'],'consent')
    pending=share(client,ctx,target_for(saved,granted),'share')
    if decision=='expire':
        with psycopg.connect(api.database_uri) as db:
            db.execute("update actions.commands set expires_at=clock_timestamp()-interval '1 second',proposed_at=clock_timestamp()-interval '1 day' where command_id=%s",(pending.command.command_id,))
        response=client.post(f'/commands/{pending.command.command_id}/confirm',headers=headers(ctx))
        assert response.status_code==409 and response.json()['error']['code']=='action_expired'
        result=ActionOutcome.model_validate(client.get(f'/commands/{pending.command.command_id}',headers=headers(ctx)).json())
        assert result.command.state=='expired'
    else:
        response=client.post(f'/commands/{pending.command.command_id}/reject',headers=headers(ctx))
        assert response.status_code==200,response.text
        result=ActionOutcome.model_validate(response.json());assert result.command.state=='rejected'
    assert result.receipt is None
    with psycopg.connect(api.database_uri) as db:assert db.execute('select count(*) from rm.shared_summaries where command_id is not null').fetchone()==(0,)
