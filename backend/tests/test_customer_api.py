"""Customer HTTP boundary over real JWT/RLS reads, including upstream corruption and audit failure."""
import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from tests.data_api_support import NativeAPI,mint_token

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST tests')
RESOURCES=['profile','accounts','cards','loans','compliance','advisor']


def headers(api: NativeAPI,cid: int) -> dict[str,str]:
    return {'Authorization':'Bearer '+mint_token(api,cid,'aal1',uuid4())}


@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_customer_api_reads_use_current_session_and_return_audited_call_ids(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    auth=headers(local_data_api,cid)
    responses=[native_api_client.get('/customer/'+path,headers=auth) for path in RESOURCES]
    responses.append(native_api_client.post('/customer/transactions',headers=auth,json={'from_date':None,'to_date':None,'filters':None}))
    for response in responses:assert response.status_code==200,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        call_ids={row[0] for row in db.execute("select resource_id from audit.events where customer_id=%s and action='data.read'",(cid,))}
        assert call_ids=={response.json()['call_id'] for response in responses}
        assert db.execute("select count(*) from audit.events where customer_id=%s and action='policy.decided'",(cid,)).fetchone()==(7,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    assert all('customer_id' not in str(response.json()['data']) for response in responses)
    for path in RESOURCES:
        assert native_api_client.get('/customer/'+path).status_code==401
        assert native_api_client.get('/customer/'+path,headers=headers(local_data_api,9999)).status_code==403
    for query in [{},{'from_date':'2030-02-30','to_date':None,'filters':None},
        {'from_date':'2030-02-02','to_date':'2030-02-01','filters':None},
        {'from_date':None,'to_date':None,'filters':{'is_overseas':1}}]:
        assert native_api_client.post('/customer/transactions',headers=auth,json=query).status_code==422


@pytest.mark.parametrize('corruption',['foreign_scope','missing_field','unmasked_account','audit_failure'])
def test_customer_api_withholds_invalid_or_unaudited_records(local_data_api: NativeAPI,native_api_client: TestClient,corruption: str) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(local_data_api.database_uri) as db:
        if corruption=='audit_failure':
            db.execute("create function public.native_reject_read() returns trigger language plpgsql as $$ begin if new.action='data.read' and new.outcome='success' then raise exception 'native audit failure'; end if; return new; end $$")
            db.execute('create trigger native_reject_read before insert on audit.events for each row execute function public.native_reject_read()')
        else:
            data={'account_id':'acc_10011','type':'savings','number':'****1234','currency':'SGD','balance':123.45,'status':'active'}
            if corruption=='missing_field':del data['balance']
            if corruption=='unmasked_account':data['number']='4000000000000000'
            from psycopg.types.json import Jsonb
            db.execute(sql.SQL("""create or replace function public.customer_read_accounts(p_customer_id bigint) returns jsonb
                language sql volatile security invoker set search_path='' as $$
                select jsonb_build_object('customer_id',{},'call_id',gen_random_uuid(),'as_of',clock_timestamp(),'data',{}) $$""").format(
                    sql.Literal(1003 if corruption=='foreign_scope' else 1001),sql.Literal(Jsonb([data]))))
    response=native_api_client.get('/customer/accounts',headers=headers(local_data_api,1001))
    assert response.status_code==503 and response.json()['error']['code']=='customer_unavailable'
    assert 'data' not in response.json() and '4000000000000000' not in response.text
