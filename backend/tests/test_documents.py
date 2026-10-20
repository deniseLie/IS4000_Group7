"""Real document metadata RPC/API, ownership, bounds and audit rollback; no Storage objects are simulated."""
import os
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')


def seed_metadata(api: NativeAPI) -> dict[int,list[UUID]]:
    """Populate only real SQL metadata rows, not uploaded files or precheck results."""
    import psycopg
    result: dict[int,list[UUID]]={}
    with psycopg.connect(api.database_uri) as db:
        for cid in [1001,1002,1003]:
            case_id=db.execute("""insert into compliance.cases(customer_id,case_type,title,notice_text,notice_date,status)
                values(%s,'additional_review','Metadata case DEMO','Synthetic metadata fixture','2030-01-01','review_opened') returning case_id""",(cid,)).fetchone()[0]
            item_id=db.execute("""insert into compliance.requested_items(customer_id,case_id,label)
                values(%s,%s,'Proof of address DEMO') returning item_id""",(cid,case_id)).fetchone()[0]
            result[cid]=[]
            for index,mime in enumerate(['application/pdf','image/png']):
                document_id=uuid4()
                db.execute("""insert into compliance.documents(document_id,customer_id,case_id,item_id,storage_path,file_name,
                    mime_type,size_bytes,precheck_status,precheck_notes,uploaded_at)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,'not_checked','PRIVATE PRECHECK NOTES',%s)""",
                    (document_id,cid,case_id,item_id if index==0 else None,f'metadata-fixture/{cid}/{document_id}',
                        f'{cid}-'+('x'*220+'.pdf' if index==0 else 'Synthetic image.png'),mime,123+index,'2030-01-02T00:00:00Z'))
                result[cid].append(document_id)
    return result


@pytest.mark.parametrize('cid',[1001,1002,1003])
@pytest.mark.parametrize('leaky',[False,True])
def test_owned_metadata_uses_rls_and_explicit_predicates(local_data_api: NativeAPI,native_api_client: TestClient,cid: int,leaky: bool) -> None:
    import psycopg
    from psycopg import sql
    ids=seed_metadata(local_data_api)
    if leaky:
        with psycopg.connect(local_data_api.database_uri) as db:
            for table in ['documents','cases','requested_items']:
                db.execute(sql.SQL('create policy native_leaky on compliance.{} for select to authenticated using(true)').format(sql.Identifier(table)))
    response=native_api_client.get('/customer/documents?customer_id=9999',headers=headers(context(local_data_api,cid)))
    assert response.status_code==200,response.text
    data=response.json()
    assert {UUID(row['document_id']) for row in data['data']}==set(ids[cid])
    assert all(row['file_name'].startswith(str(cid)) for row in data['data'])
    assert [row['document_id'] for row in data['data']]==sorted(str(value) for value in ids[cid])
    assert not any(value in response.text for value in ['storage_path','metadata-fixture','precheck_notes','PRIVATE PRECHECK NOTES','customer_id'])
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select resource_id from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchall()==[(data['call_id'],)]
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
    with SyncPostgrestClient(local_data_api.url) as db:
        with pytest.raises(APIError):db.rpc('customer_read_documents',{'p_customer_id':cid}).execute()
        db.auth(context(local_data_api,cid).principal.jwt.get_secret_value())
        with pytest.raises(APIError) as foreign:db.rpc('customer_read_documents',{'p_customer_id':9999}).execute()
        assert foreign.value.code=='42501'
    assert native_api_client.get('/customer/documents').status_code==401
    assert native_api_client.get('/customer/documents',headers=headers(context(local_data_api,9999))).status_code==403


def test_empty_metadata_bounds_and_audit_failure(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    auth=headers(context(local_data_api,1001))
    assert native_api_client.get('/customer/documents',headers=auth).json()['data']==[]
    ids=seed_metadata(local_data_api)
    with psycopg.connect(local_data_api.database_uri) as db:
        case_id=db.execute('select case_id from compliance.documents where document_id=%s',(ids[1001][0],)).fetchone()[0]
        db.execute("""insert into compliance.documents(customer_id,case_id,storage_path,file_name,mime_type,size_bytes,uploaded_at)
            select 1001,%s,'bounded-metadata/'||n,'Bounded DEMO.pdf','application/pdf',1,'2040-01-01'::timestamptz+n*interval '1 second'
            from generate_series(1,205)n""",(case_id,))
    response=native_api_client.get('/customer/documents',headers=auth)
    assert response.status_code==200 and len(response.json()['data'])==200
    assert all(row['file_name']=='Bounded DEMO.pdf' for row in response.json()['data'])
    assert response.json()['data'][0]['uploaded_at']>response.json()['data'][-1]['uploaded_at']
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("create function public.native_reject_document_read() returns trigger language plpgsql as $$ begin if new.action='data.read' and new.outcome='success' then raise exception 'native audit failure'; end if; return new; end $$")
        db.execute('create trigger native_reject_document_read before insert on audit.events for each row execute function public.native_reject_document_read()')
        before=db.execute("select count(*) from audit.events where action='data.read' and outcome='success'").fetchone()
    failed=native_api_client.get('/customer/documents',headers=auth)
    assert failed.status_code==503 and 'data' not in failed.json()
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where action='data.read' and outcome='success'").fetchone()==before


@pytest.mark.parametrize('relation',['foreign_case','foreign_item','different_owned_case'])
@pytest.mark.parametrize('leaky',[False,True])
def test_inconsistent_document_links_fail_instead_of_hiding_rows(local_data_api: NativeAPI,native_api_client: TestClient,relation: str,leaky: bool) -> None:
    import psycopg
    from psycopg import sql
    ids=seed_metadata(local_data_api)
    with psycopg.connect(local_data_api.database_uri) as db:
        if leaky:
            for table in ['documents','cases','requested_items']:
                db.execute(sql.SQL('create policy native_leaky on compliance.{} for select to authenticated using(true)').format(sql.Identifier(table)))
        foreign_case,foreign_item=db.execute('select case_id,item_id from compliance.documents where document_id=%s',(ids[1003][0],)).fetchone()
        if relation=='different_owned_case':
            db.execute('update compliance.cases set customer_id=1001 where case_id=%s',(foreign_case,))
            db.execute('update compliance.requested_items set customer_id=1001 where item_id=%s',(foreign_item,))
        column='case_id' if relation=='foreign_case' else 'item_id'
        db.execute(sql.SQL('update compliance.documents set {}=%s where document_id=%s').format(sql.Identifier(column)),
            (foreign_case if relation=='foreign_case' else foreign_item,ids[1001][0]))
    response=native_api_client.get('/customer/documents',headers=headers(context(local_data_api,1001)))
    assert response.status_code==503 and response.json()['error']['code']=='customer_unavailable'
    assert 'data' not in response.json()


@pytest.mark.parametrize('corruption',['scope','missing_timestamp','boolean_size','item_label','duplicate','blank_filename'])
def test_invalid_external_metadata_is_withheld(local_data_api: NativeAPI,native_api_client: TestClient,corruption: str) -> None:
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    seed_metadata(local_data_api)
    auth=headers(context(local_data_api,1001))
    payload=native_api_client.get('/customer/documents',headers=auth).json()
    if corruption=='missing_timestamp':del payload['data'][0]['uploaded_at']
    elif corruption=='boolean_size':payload['data'][0]['size_bytes']=True
    elif corruption=='item_label':payload['data'][0]['item_label']=None if payload['data'][0]['item_id'] else 'Unexpected label'
    elif corruption=='duplicate':payload['data']=[payload['data'][0],payload['data'][0]]
    elif corruption=='blank_filename':payload['data'][0]['file_name']='   '
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute(sql.SQL("""create or replace function public.customer_read_documents(p_customer_id bigint) returns jsonb
            language sql volatile security invoker set search_path='' as $$
            select jsonb_build_object('customer_id',{},'call_id',gen_random_uuid(),'as_of',clock_timestamp(),'data',{}) $$""").format(
                sql.Literal(1003 if corruption=='scope' else 1001),sql.Literal(Jsonb(payload['data']))))
    response=native_api_client.get('/customer/documents',headers=auth)
    assert response.status_code==503 and 'data' not in response.json()
