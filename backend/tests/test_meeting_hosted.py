"""Hosted check of the brief readers the meeting side calls (0106, 0107; RM copilot build plan 1c/1d), as Sophie.
Opt-in: TEST_RM_LOGIN_FILE is a JSON {"email","password"} for Sophie's hosted login (demo.sophie@example.com), plus
SUPABASE_URL and SUPABASE_ANON_KEY. Uses the public anon key and her own JWT only. Side effect: each read writes its
audit event (data.read or policy.rm_consent_gap) on the hosted project. Run 34_reset_meeting_demo.sql first if Michelle's
grant may have been revoked."""
import json
import os
import pytest

LOGIN=os.getenv('TEST_RM_LOGIN_FILE')
pytestmark=pytest.mark.skipif(not LOGIN,reason="Requires Sophie's hosted login (TEST_RM_LOGIN_FILE)")
MEI_LING_REQUEST='00000000-0000-4000-8000-100700000071'


@pytest.fixture
def sophie():
    from supabase import create_client
    sb=create_client(os.environ['SUPABASE_URL'],os.environ['SUPABASE_ANON_KEY'])
    with open(LOGIN) as f:sb.auth.sign_in_with_password(json.load(f))
    yield lambda name,args:sb.rpc(name,args).execute().data
    sb.auth.sign_out()


def test_the_brief_readers_give_data_for_michelle_and_a_gap_for_mei_ling(sophie):
    shared=sophie('rm_transaction_insights',{'p_customer_id':1002,'p_purpose':'meeting_prep'})
    assert shared['shared'] is True and shared['grant_id'] and {'monthly','large_movements','recurring'}<=set(shared['data'])
    gap=sophie('rm_transaction_insights',{'p_customer_id':1007,'p_purpose':'meeting_prep'})
    assert (gap['denied'],gap['shared'],gap['data'])==(False,False,None)
    # Since 0108 (batch 35): Michelle's seeded inferred home loan topic is past its hold; Mei Ling has no chat_topics grant,
    # so only the investments topic she shared herself comes back. Points are list values only (no text field).
    for customer,granted,seeded in ((1002,True,('home_loan','inferred')),(1007,False,('investments','confirmed'))):
        points=sophie('rm_talking_points',{'p_customer_id':customer,'p_purpose':'meeting_prep'})
        assert points['shared'] is granted and seeded in {(p['topic'],p['origin']) for p in points['data']}
        assert all(set(p)<={'point_id','topic','stage','timeframe','concerns','origin','first_seen','last_seen'} for p in points['data'])
    assert {p['origin'] for p in sophie('rm_talking_points',{'p_customer_id':1007,'p_purpose':'client_view'})['data']}=={'confirmed'}
    assert sophie('rm_transaction_insights',{'p_customer_id':1001,'p_purpose':'client_view'})['denied'] is True  # not in her book


def test_mei_lings_seeded_request_waits_for_sophie(sophie):
    requests=sophie('rm_meeting_requests',{'p_status':'open'})['data']
    assert MEI_LING_REQUEST in {r['request_id'] for r in requests}
