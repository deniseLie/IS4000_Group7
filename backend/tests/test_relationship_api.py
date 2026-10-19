"""Native JWT/RLS/API relationship reads, including hidden booking conflicts and expired holds."""
import os
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from tests.data_api_support import NativeAPI,mint_token

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')
SGT=timezone(timedelta(hours=8))


def headers(api: NativeAPI,cid: int) -> dict[str,str]:
    return {'Authorization':'Bearer '+mint_token(api,cid,'aal1',uuid4())}


def monday() -> date:
    today=datetime.now(SGT).date()
    return today+timedelta(days=7-today.weekday())


def query(day: date) -> dict[str,str | int]:
    return {'from_date':day.isoformat(),'to_date':day.isoformat(),'limit':20}


@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_requests_and_slots_are_scoped_and_audited(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    auth=headers(local_data_api,cid)
    requests=native_api_client.get('/relationship/requests',headers=auth)
    assert requests.status_code==200 and requests.json()['call_id'] and requests.json()['as_of']
    with psycopg.connect(local_data_api.database_uri) as db:
        expected={str(row[0]) for row in db.execute('select request_id from rm.requests where customer_id=%s',(cid,)).fetchall()}
    assert {row['request_id'] for row in requests.json()['data']}==expected
    assert all('customer_id' not in row for row in requests.json()['data'])
    slots=native_api_client.post('/relationship/slots',headers=auth,json=query(monday()))
    assert slots.status_code==200 and slots.json()['call_id']!=requests.json()['call_id']
    assert bool(slots.json()['data'])==(cid==1003)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchone()[0]==2
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_slots_exclude_private_bookings_blocks_and_active_holds(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    day=monday()
    with psycopg.connect(local_data_api.database_uri) as db:
        advisor=db.execute('select advisor_id from rm.assignments where customer_id=1003 and ended_on is null').fetchone()[0]
        db.execute('delete from rm.working_hours where advisor_id=%s',(advisor,))
        db.execute("insert into rm.working_hours values (%s,1,'09:00','12:00')",(advisor,))
        # The caller cannot read customer 1001's appointment, but its time must still be unavailable.
        for hour,minute,status,expiry in [(9,0,'confirmed',None),(10,0,'held',timedelta(hours=1)),
            (10,30,'held',timedelta(seconds=-1)),(11,0,'cancelled',None)]:
            start=datetime.combine(day,datetime.min.time(),tzinfo=SGT)+timedelta(hours=hour,minutes=minute)
            db.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,hold_expires_at)
                values (1001,%s,%s,%s,'video',%s,case when %s::interval is null then null else clock_timestamp()+%s end)""",
                (advisor,start,start+timedelta(minutes=30),status,expiry,expiry))
        blocked=datetime.combine(day,datetime.min.time(),tzinfo=SGT)+timedelta(hours=9,minutes=30)
        db.execute('insert into rm.advisor_blocks(advisor_id,starts_at,ends_at) values (%s,%s,%s)',(advisor,blocked,blocked+timedelta(minutes=30)))
    token=mint_token(local_data_api,1003,'aal1',uuid4())
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        assert db.schema('rm').table('appointments').select('*').eq('customer_id',1001).execute().data==[]
    response=native_api_client.post('/relationship/slots',headers={'Authorization':'Bearer '+token},json=query(day))
    assert response.status_code==200
    starts=[datetime.fromisoformat(row['starts_at']).astimezone(SGT).strftime('%H:%M') for row in response.json()['data']]
    assert starts==['10:30','11:00','11:30']
    assert all(set(row)=={'advisor_id','starts_at','ends_at'} for row in response.json()['data'])


def test_slot_rpc_rejects_customer_override_and_guest(local_data_api: NativeAPI) -> None:
    day=monday()
    params={'p_customer_id':1003,'p_from':day.isoformat(),'p_to':day.isoformat(),'p_limit':4}
    with SyncPostgrestClient(local_data_api.url) as db:
        with pytest.raises(APIError):db.rpc('appointments_find_slots',params).execute()
        db.auth(mint_token(local_data_api,1001,'aal1',uuid4()))
        with pytest.raises(APIError) as error:db.rpc('appointments_find_slots',params).execute()
        assert error.value.code=='42501'


def test_guest_unlinked_invalid_dates_and_missing_rpc_fail_explicitly(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    assert native_api_client.get('/relationship/requests').status_code==401
    assert native_api_client.post('/relationship/slots',json=query(monday())).status_code==401
    assert native_api_client.get('/relationship/requests',headers=headers(local_data_api,9999)).status_code==403
    auth=headers(local_data_api,1003)
    yesterday=datetime.now(SGT).date()-timedelta(days=1)
    bad=native_api_client.post('/relationship/slots',headers=auth,json=query(yesterday))
    assert bad.status_code==422 and bad.json()['error']['code']=='slots_invalid'
    for change in [{'limit':0},{'limit':True},{'to_date':(monday()-timedelta(days=1)).isoformat()},
        {'to_date':(monday()+timedelta(days=14)).isoformat()}]:
        assert native_api_client.post('/relationship/slots',headers=auth,json={**query(monday()),**change}).status_code==422
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.appointments_find_slots(bigint,date,date,int)')
    response=native_api_client.post('/relationship/slots',headers=auth,json=query(monday()))
    assert response.status_code==503 and response.json()['error']['code']=='relationship_unavailable'
