"""Live RLS check on the hosted project (migrations 0002-0004, docs/planning/synthetic-data-spec.md).
Opt-in: TEST_PERSONA_FIXTURE_FILE is a JSON list of {"email","password"} for the three persona logins,
plus SUPABASE_URL and SUPABASE_ANON_KEY. Uses the public anon key and each persona's own JWT only."""
import json, os
import pytest

FIXTURE = os.getenv('TEST_PERSONA_FIXTURE_FILE')
pytestmark = pytest.mark.skipif(not FIXTURE, reason='Requires persona test logins (TEST_PERSONA_FIXTURE_FILE)')

# Rows each persona must see from the seed; every other customer table must hold none of theirs or anyone else's.
EXPECTED = {
    'demo.alex@example.com': {1001: {'banking.bank_accounts': 1, 'banking.credit_cards': 1, 'banking.transactions': 6, 'banking.loans': 1, 'notify.preferences': 1}},
    'demo.michelle@example.com': {1002: {'banking.bank_accounts': 1, 'banking.transactions': 1, 'compliance.cases': 1, 'compliance.requested_items': 4, 'compliance.status_history': 4, 'compliance.info_requests': 1, 'notify.preferences': 1}},
    'demo.david@example.com': {1003: {'banking.bank_accounts': 1, 'rm.assignments': 2, 'rm.advisors': 2, 'rm.working_hours': 5, 'notify.preferences': 1}},
}
CUSTOMER_TABLES = ['banking.bank_accounts', 'banking.credit_cards', 'banking.transactions', 'banking.loans',
                   'compliance.cases', 'compliance.requested_items', 'compliance.status_history', 'compliance.info_requests',
                   'rm.assignments', 'notify.preferences']
INTERNAL = ['compliance.case_internal', 'rm.advisor_blocks', 'notify.templates']


def _client(user):
    from supabase import create_client
    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_ANON_KEY'])
    sb.auth.sign_in_with_password({'email': user['email'], 'password': user['password']})
    return sb


def _rows(sb, name, cols='*'):
    schema, table = name.split('.')
    return sb.schema(schema).table(table).select(cols).execute().data


@pytest.mark.parametrize('user', json.load(open(FIXTURE)) if FIXTURE else [], ids=lambda u: u['email'].split('@')[0])
def test_persona_sees_only_own_rows(user):
    (cid, expected), = EXPECTED[user['email']].items()
    sb = _client(user)
    try:
        assert [p['customer_id'] for p in _rows(sb, 'banking.customer_profiles', 'customer_id')] == [cid]
        for name in CUSTOMER_TABLES:
            rows = _rows(sb, name, 'customer_id')
            assert {r['customer_id'] for r in rows} <= {cid}, name
            assert len(rows) == expected.get(name, 0), name
        seed=sb.schema('rm').table('requests').select('customer_id').eq('request_id','00000000-0000-4000-8000-100300000031').execute().data
        assert seed==([{'customer_id':1003}] if cid==1003 else [])
        for name in ['rm.advisors', 'rm.working_hours']:
            assert len(_rows(sb, name)) == expected.get(name, 0), name
        for name in INTERNAL:
            with pytest.raises(Exception):
                _rows(sb, name)
    finally:
        sb.auth.sign_out()


@pytest.mark.parametrize('user', json.load(open(FIXTURE)) if FIXTURE else [], ids=lambda u: u['email'].split('@')[0])
def test_foundation_records_remain_customer_scoped(user):
    """Mutable foundation records have no fixed seed counts; explicit foreign filters must still return nothing."""
    (cid, _), = EXPECTED[user['email']].items()
    sb=_client(user)
    try:
        for name in ['rm.agendas','rm.shared_summaries','consent.requests','consent.grants','actions.commands','actions.receipts','rm.requests','compliance.documents']:
            schema,table=name.split('.')
            rows=sb.schema(schema).table(table).select('customer_id').limit(200).execute().data
            assert {row['customer_id'] for row in rows}<={cid},name
            foreign=sb.schema(schema).table(table).select('customer_id').neq('customer_id',cid).limit(1).execute().data
            assert foreign==[],name
    finally:
        sb.auth.sign_out()
