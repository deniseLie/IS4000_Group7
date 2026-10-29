"""Michelle answers the bank's question on her review, and the bank closes it (0080). Native PostgREST, real JWT/RLS."""
import os
from uuid import uuid4
import psycopg
import pytest
from tests.test_actions_api import context,headers

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native PostgREST')
CASE='00000000-0000-4000-8000-100200000001'
REQUEST='00000000-0000-4000-8000-100200000021'  # "Please explain the source of the SGD 120,000 incoming transfer…"


def answer(client,ctx,text,request=REQUEST):
    scope=client.post('/actions/cases',headers=headers(ctx),json={'session_id':str(uuid4()),'case_id':str(uuid4())}).json()
    return client.post('/commands',headers=headers(ctx),json={'command_id':str(uuid4()),'case_id':scope['case_id'],'task_id':uuid4().hex[:12],
        'type':'compliance.answer_request','target':{'case_id':CASE,'request_id':request,'answer':text}})


def requests(client,ctx):
    return {r['request_id']:r for r in client.get('/customer/compliance',headers=headers(ctx)).json()['data']['info_requests']}


def test_michelle_answers_and_the_bank_accepts(local_data_api,native_api_client) -> None:
    ctx=context(local_data_api,1002)
    sent=answer(native_api_client,ctx,'It is the sale of my flat in Tampines; my PIN is 4821.')
    assert sent.status_code==200 and sent.json()['receipt']['outcome']=='succeeded',sent.text
    mine=requests(native_api_client,ctx)[REQUEST]
    assert mine['instructions'].startswith('Answer in the reply box')
    assert mine['status']=='responded' and mine['responded_at'] and '4821' not in mine['response'] and 'Tampines' in mine['response']
    again=answer(native_api_client,ctx,'Second answer')
    assert again.status_code!=200 or again.json()['receipt']['outcome']!='succeeded'  # only an open request can be answered
    with psycopg.connect(local_data_api.database_uri) as db:
        delivered,=db.execute("select notify.deliver(o)->>'disposition' from events.outbox o where o.event_type='compliance.request_answered' and o.customer_id=1002").fetchone()
        assert delivered=='delivered'  # Inbox: "Your answer was sent to the bank"
        assert db.execute("select compliance.record_request_decision(%s,'accepted')",(REQUEST,)).fetchone()==('Answer accepted',)
    mine=requests(native_api_client,ctx)[REQUEST]
    assert (mine['status'],mine['outcome'])==('closed','accepted')


def test_bank_asks_a_follow_up_question(local_data_api,native_api_client) -> None:
    ctx=context(local_data_api,1002)
    assert answer(native_api_client,ctx,'Sale of my flat.').json()['receipt']['outcome']=='succeeded'
    with psycopg.connect(local_data_api.database_uri) as db:
        with pytest.raises(psycopg.Error,match='what else'):db.execute("select compliance.record_request_decision(%s,'more_info',' ')",(REQUEST,))
        db.rollback()
        db.execute("select compliance.record_request_decision(%s,'more_info','Please send the sale agreement.')",(REQUEST,))
    listed=requests(native_api_client,ctx)
    assert listed[REQUEST]['outcome']=='more_info'
    follow=[r for r in listed.values() if r['status']=='open' and r['question']=='Please send the sale agreement.']
    assert len(follow)==1 and follow[0]['due_date']


def test_another_client_cannot_answer_michelles_request(local_data_api,native_api_client) -> None:
    ctx=context(local_data_api,1001)
    sent=answer(native_api_client,ctx,'Not mine')
    assert sent.status_code in (403,404,409,422) or sent.json()['receipt']['outcome']!='succeeded'
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select status from compliance.info_requests where request_id=%s',(REQUEST,)).fetchone()==('open',)
