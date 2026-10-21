"""Native SQL catalogue/action tests, not a replacement Storage server or blob-presence test."""
import os
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from app.schemas.contracts import ActionCommand
from tests.data_api_support import NativeAPI
from tests.database_support import persona_id,set_customer
from tests.test_actions_api import context,headers,owned_case,propose

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')


def upload_target(api: NativeAPI,cid: int) -> dict[str,str|int|None]:
    import psycopg
    with psycopg.connect(api.database_uri) as db:
        case_id=db.execute("""insert into compliance.cases(customer_id,case_type,title,notice_text,notice_date,status)
            values(%s,'additional_review','Upload DEMO','Synthetic file test','2030-01-01','review_opened') returning case_id""",(cid,)).fetchone()[0]
        item_id=db.execute("insert into compliance.requested_items(customer_id,case_id,label) values(%s,%s,'Synthetic evidence') returning item_id",(cid,case_id)).fetchone()[0]
    return {'document_id':str(uuid4()),'case_id':str(case_id),'item_id':str(item_id),
        'file_name':'Synthetic DEMO.png','mime_type':'image/png','size_bytes':123}


def object_metadata(api: NativeAPI,cid: int,target: dict[str,str|int|None]) -> UUID:
    """Insert platform metadata only; hosted tests separately prove real Storage bytes."""
    import psycopg
    with psycopg.connect(api.database_uri) as db:
        return db.execute("""insert into storage.objects(bucket_id,name,owner_id,metadata) values
            ('demobank-compliance-private',%s,%s,%s) returning id""",(f"{cid}/{target['case_id']}/{target['document_id']}",
                str(persona_id(cid)),Jsonb({'size':target['size_bytes'],'mimetype':target['mime_type']}))).fetchone()[0]


@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_prepare_register_replay_location_and_notification(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,cid)); target=upload_target(local_data_api,cid)
    prepared=native_api_client.post('/documents/uploads/prepare',headers=headers(ctx),json=target)
    assert prepared.status_code==200,prepared.text
    assert prepared.json()['data']['storage_path']==f"{cid}/{target['case_id']}/{target['document_id']}"
    command=ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:upload:document.register',target=target)
    missing=native_api_client.post('/commands',headers=headers(ctx),json={'command_id':str(command.command_id),'case_id':str(ctx.case_id),
        'task_id':'upload','type':command.type,'target':target})
    assert missing.status_code==403,missing.text
    object_id=object_metadata(local_data_api,cid,target)
    saved=propose(native_api_client,ctx,command)
    assert saved.command.state=='succeeded' and saved.command.confirmed_at is None
    assert saved.receipt.result.object_id==object_id
    assert propose(native_api_client,ctx,command)==saved
    records=native_api_client.get('/customer/documents',headers=headers(ctx)).json()['data']
    assert len(records)==1 and records[0]['precheck_status']=='not_checked'
    location=native_api_client.get(f"/documents/{target['document_id']}/location",headers=headers(ctx))
    assert location.status_code==200 and location.json()['data']['storage_path']==prepared.json()['data']['storage_path']
    other=context(local_data_api,1003 if cid!=1003 else 1001)
    assert native_api_client.get(f"/documents/{target['document_id']}/location",headers=headers(other)).status_code==404
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from compliance.documents').fetchone()==(1,)
        assert db.execute('select submission_status from compliance.requested_items where item_id=%s',(target['item_id'],)).fetchone()==('not_submitted',)
        delivered=db.execute('select notify.deliver(e) from events.outbox e where command_id=%s',(command.command_id,)).fetchone()[0]
        assert delivered['disposition']=='delivered'
        assert db.execute('select notify.deliver(e) from events.outbox e where command_id=%s',(command.command_id,)).fetchone()[0]['disposition']=='duplicate'
        assert db.execute('select template_key from notify.inbox where notification_id=%s',(delivered['notification_id'],)).fetchone()==('document.uploaded',)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        db.execute('update storage.objects set id=%s where id=%s',(uuid4(),object_id))
    assert native_api_client.get(f"/documents/{target['document_id']}/location",headers=headers(ctx)).status_code==404


@pytest.mark.parametrize('failure',['audit','outbox'])
def test_registration_rolls_back_and_retry_uses_same_file(local_data_api: NativeAPI,native_api_client: TestClient,failure: str) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001)); target=upload_target(local_data_api,1001)
    object_metadata(local_data_api,1001,target)
    command=ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:rollback:document.register',target=target)
    with psycopg.connect(local_data_api.database_uri) as db:
        if failure=='audit':
            db.execute("create function public.reject_upload() returns trigger language plpgsql as $$ begin if new.action='document.uploaded' then raise exception 'native audit unavailable'; end if; return new; end $$")
            db.execute('create trigger reject_upload before insert on audit.events for each row execute function public.reject_upload()')
        else:db.execute("alter table events.outbox add constraint reject_upload check(event_type<>'document.uploaded')")
    response=native_api_client.post('/commands',headers=headers(ctx),json={'command_id':str(command.command_id),'case_id':str(ctx.case_id),
        'task_id':'rollback','type':command.type,'target':target})
    assert response.status_code==503,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        for table in ['compliance.documents','actions.commands','actions.receipts']:
            assert db.execute(f'select count(*) from {table}').fetchone()==(0,)
        assert db.execute('select count(*) from storage.objects').fetchone()==(1,)
        if failure=='audit':db.execute('drop trigger reject_upload on audit.events')
        else:db.execute('alter table events.outbox drop constraint reject_upload')
    assert propose(native_api_client,ctx,command).receipt.outcome=='succeeded'


def test_scope_closed_case_and_actual_catalogue_descriptor(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=owned_case(local_data_api,context(local_data_api,1001)); target=upload_target(local_data_api,1001)
    foreign=upload_target(local_data_api,1003)
    for invalid in [foreign,{**target,'item_id':foreign['item_id']}]:
        assert native_api_client.post('/documents/uploads/prepare',headers=headers(ctx),json=invalid).status_code==403
    object_id=object_metadata(local_data_api,1001,target)
    body={'command_id':str(uuid4()),'case_id':str(ctx.case_id),'task_id':'descriptor','type':'document.register','target':target}
    for field,value in [('size_bytes',124),('mime_type','application/pdf')]:
        changed={**target,field:value}
        if field=='mime_type':changed['file_name']='Synthetic.pdf'
        response=native_api_client.post('/commands',headers=headers(ctx),json={**body,'target':changed})
        assert response.status_code==422,response.text
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("update compliance.cases set status='closed' where case_id=%s",(target['case_id'],))
    assert native_api_client.post('/documents/uploads/prepare',headers=headers(ctx),json=target).status_code==403
    failed=native_api_client.post('/commands',headers=headers(ctx),json=body)
    assert failed.status_code==200 and failed.json()['receipt']['outcome']=='failed',failed.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from compliance.documents').fetchone()==(0,)
        assert db.execute('select id from storage.objects').fetchone()==(object_id,)


@pytest.mark.parametrize('field,value',[('size_bytes',True),('size_bytes',0),('size_bytes',10485761),('file_name','../x.png'),
    ('file_name',' x.png'),('file_name','.png'),('mime_type','text/plain'),('item_id','bad')])
def test_invalid_descriptors_do_not_prepare(local_data_api: NativeAPI,native_api_client: TestClient,field: str,value: object) -> None:
    target=upload_target(local_data_api,1001)
    response=native_api_client.post('/documents/uploads/prepare',headers=headers(context(local_data_api,1001)),json={**target,field:value})
    assert response.status_code==422,response.text


def test_storage_restrictive_scope_survives_leaky_policies(local_data_api: NativeAPI) -> None:
    import psycopg
    target=upload_target(local_data_api,1001); foreign=upload_target(local_data_api,1003)
    own=object_metadata(local_data_api,1001,target); other=object_metadata(local_data_api,1003,foreign)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('create policy native_leaky_all on storage.objects for all to authenticated using(true) with check(true)')
        set_customer(db,1001)
        assert db.execute('select id from storage.objects').fetchall()==[(own,)]
        assert db.execute('update storage.objects set metadata=metadata returning id').fetchall()==[]
        assert db.execute('delete from storage.objects returning id').fetchall()==[]
        assert db.execute('select id from storage.objects where id=%s',(other,)).fetchall()==[]
    for path,owner in [(f"1003/{foreign['case_id']}/{uuid4()}",str(persona_id(1001))),
        (f"1001/{target['case_id']}/{uuid4()}",str(persona_id(1003))),('1001/bad/path',str(persona_id(1001)))]:
        with psycopg.connect(local_data_api.database_uri) as db:
            set_customer(db,1001)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute("insert into storage.objects(bucket_id,name,owner_id) values('demobank-compliance-private',%s,%s)",(path,owner))


def test_customer_can_insert_only_new_owned_open_case_paths(local_data_api: NativeAPI) -> None:
    import psycopg
    target=upload_target(local_data_api,1001)
    path=f"1001/{target['case_id']}/{target['document_id']}"
    with psycopg.connect(local_data_api.database_uri) as db:
        set_customer(db,1001)
        db.execute("insert into storage.objects(bucket_id,name,owner_id) values('demobank-compliance-private',%s,%s)",(path,str(persona_id(1001))))
        assert db.execute('select name from storage.objects').fetchall()==[(path,)]
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("update compliance.cases set status='closed' where case_id=%s",(target['case_id'],))
    with psycopg.connect(local_data_api.database_uri) as db:
        set_customer(db,1001)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("insert into storage.objects(bucket_id,name,owner_id) values('demobank-compliance-private',%s,%s)",
                (f"1001/{target['case_id']}/{uuid4()}",str(persona_id(1001))))


def test_private_bucket_denies_anon_even_with_a_broad_public_policy(local_data_api: NativeAPI) -> None:
    import psycopg
    target=upload_target(local_data_api,1001);object_metadata(local_data_api,1001,target)
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('grant usage on schema storage to anon')
        db.execute('grant select,insert,update,delete on storage.objects to anon')
        db.execute('create policy native_leaky_anon on storage.objects for all to anon using(true) with check(true)')
        db.execute('set local role anon')
        assert db.execute('select id from storage.objects').fetchall()==[]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("insert into storage.objects(bucket_id,name,owner_id) values('demobank-compliance-private',%s,%s)",
                (f"1001/{target['case_id']}/{uuid4()}",str(persona_id(1001))))
