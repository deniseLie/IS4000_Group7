"""CR17: Michelle can see her RM's free times, hold one and book it (native PostgREST, real JWT/RLS)."""
import os
from datetime import datetime,timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from tests.test_actions_api import context,headers

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native PostgREST')


def test_michelle_books_and_cancels_a_meeting_with_her_rm(local_data_api,native_api_client) -> None:
    ctx=context(local_data_api,1002)
    today=datetime.now(ZoneInfo('Asia/Singapore')).date()
    slots=native_api_client.post('/relationship/slots',headers=headers(ctx),json={'from_date':str(today),'to_date':str(today+timedelta(days=13)),'limit':12})
    assert slots.status_code==200,slots.text
    free=slots.json()['data'];assert free,'Michelle has no free RM times'
    held=native_api_client.post('/relationship/holds',headers=headers(ctx),json={'command_id':str(uuid4()),'session_id':str(uuid4()),'case_id':str(uuid4()),
        'target':{**free[0],'format':'video'}})
    assert held.status_code==200 and held.json()['receipt']['outcome']=='succeeded',held.text
    meeting=held.json()['receipt']['result']
    case_id=held.json()['command']['case_id']
    def decide(kind):
        proposed=native_api_client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':case_id,'task_id':uuid4().hex[:12],'type':kind,
            'target':{'appointment_id':meeting['appointment_id']}})
        assert proposed.status_code==200,proposed.text
        done=native_api_client.post(f"/commands/{proposed.json()['command']['command_id']}/confirm",headers=headers(ctx))
        assert done.status_code==200 and done.json()['receipt']['outcome']=='succeeded',done.text
    decide('appointment.confirm')
    listed=native_api_client.get('/relationship/appointments',headers=headers(ctx)).json()['data']
    assert [m['status'] for m in listed if m['appointment_id']==meeting['appointment_id']]==['confirmed']
    decide('appointment.cancel')
    listed=native_api_client.get('/relationship/appointments',headers=headers(ctx)).json()['data']
    assert [m['status'] for m in listed if m['appointment_id']==meeting['appointment_id']]==['cancelled']
