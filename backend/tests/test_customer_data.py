"""Real JWT/Data API customer reads, atomic audit and explicit predicates independent of RLS."""
import asyncio
import os
from datetime import date
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import ValidationError
from tests.langsmith_support import Runs
from app.schemas.contracts import Principal,RequestContext
from app.services import customer_data as cd
from app.services.authorization import PolicyDenied
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST tests')
READERS=[cd.get_profile,cd.get_accounts,cd.get_credit_cards,cd.get_loans,cd.get_compliance_case,cd.get_rm_assignment]


def context(api: NativeAPI,cid: int) -> RequestContext:
    session=uuid4()
    return RequestContext(session_id=session,principal=Principal(kind='customer',user_id=persona_id(cid),customer_id=cid,
        jwt=mint_token(api,cid,'aal1',session)))


@pytest.mark.parametrize('cid',[1001,1002,1003])
@pytest.mark.parametrize('leaky',[False,True])
def test_snapshots_match_owned_database_records_and_atomic_audit(local_data_api: NativeAPI,native_api_client: TestClient,langsmith_runs: Runs,cid: int,leaky: bool) -> None:
    import psycopg
    from psycopg import sql
    if leaky:
        # Actual deliberately permissive RLS: SQL customer predicates must independently isolate each read.
        with psycopg.connect(local_data_api.database_uri) as db:
            for schema,table in [('banking','bank_accounts'),('banking','credit_cards'),('banking','loans'),('banking','transactions'),
                ('compliance','cases'),('compliance','requested_items'),('compliance','status_history'),('compliance','info_requests'),
                ('rm','assignments'),('rm','advisors')]:
                db.execute(sql.SQL('create policy native_leaky on {}.{} for select to authenticated using(true)').format(sql.Identifier(schema),sql.Identifier(table)))
    ctx=context(local_data_api,cid)
    results=[asyncio.run(reader(ctx)) for reader in READERS]
    results.append(asyncio.run(cd.get_transactions(ctx,None,None,None)))
    profile,accounts,cards,loans,case,assignment,transactions=results
    with psycopg.connect(local_data_api.database_uri) as db:
        assert profile.data['name']==db.execute('select full_name from banking.customer_profiles where customer_id=%s',(cid,)).fetchone()[0]
        for rows,table,prefix,column in [(accounts.data,'bank_accounts','acc_','account_id'),(cards.data,'credit_cards','card_','card_id'),
            (loans.data,'loans','loan_','loan_id'),(transactions.data,'transactions','txn_','transaction_id')]:
            expected={prefix+str(row[0]) for row in db.execute(sql.SQL('select {} from banking.{} where customer_id=%s').format(sql.Identifier(column),sql.Identifier(table)),(cid,))}
            key='txn_id' if table=='transactions' else column
            assert {row[key] for row in rows}==expected
        expected_case=db.execute('select case_id from compliance.cases where customer_id=%s order by notice_date desc,case_id limit 1',(cid,)).fetchone()
        assert (case.data['case_id'] if case.data else None)==(str(expected_case[0]) if expected_case else None)
        if case.data:
            assert 'risk_rating' not in str(case.data) and 'reviewer_note' not in str(case.data)
            assert [item['sort_order'] for item in case.data['requested_items']]==sorted(item['sort_order'] for item in case.data['requested_items'])
        expected_rm=db.execute('select advisor_id from rm.assignments where customer_id=%s and ended_on is null',(cid,)).fetchone()
        assert (assignment.data['current']['rm_id'] if assignment.data and assignment.data['current'] else None)==(str(expected_rm[0]) if expected_rm else None)
        reads=db.execute("select resource_id,details->>'tool' from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchall()
        assert len(reads)==7 and {row[0] for row in reads}=={str(result.call_id) for result in results}
        assert {run['name'] for run in langsmith_runs.created}=={row[1] for row in reads}
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    for row in accounts.data:assert row['number'].startswith('****') and len(row['number'])==8
    for row in cards.data:assert len(row['last4'])==4 and not {'pan','card_number','customer_id'}&row.keys()
    assert all(result.as_of.tzinfo is not None for result in results)
    assert len({result.call_id for result in results})==7


def test_transaction_dates_filters_and_bounds(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    with psycopg.connect(local_data_api.database_uri) as db:
        account=db.execute('select account_id from banking.bank_accounts where customer_id=1001 order by account_id limit 1').fetchone()[0]
        db.execute("""insert into banking.transactions(customer_id,account_id,txn_date,merchant,amount,status,is_overseas)
            select 1001,%s,t,'Boundary DEMO',-12.34,'pending',true from unnest(array[
            '2030-01-01T15:59:59Z'::timestamptz,'2030-01-01T16:00:00Z'::timestamptz,
            '2030-01-02T15:59:59Z'::timestamptz,'2030-01-02T16:00:00Z'::timestamptz])t""",(account,))
    rows=asyncio.run(cd.get_transactions(ctx,date(2030,1,2),date(2030,1,2),{'status':'pending','is_overseas':True,'currency':'SGD'})).data
    assert len(rows)==2 and all(row['date']=='2030-01-02' and row['amount']==-12.34 and row['card_last4'] is None for row in rows)
    assert len(asyncio.run(cd.get_transactions(ctx,None,None,{'txn_id':rows[0]['txn_id']})).data)==1
    assert asyncio.run(cd.get_transactions(ctx,None,None,{'card_id':'card_999999'})).data==[]
    for from_,to,filters in [(date(2030,1,3),date(2030,1,2),None),(None,None,{'status':'invented'}),(None,None,{'is_overseas':'true'})]:
        with pytest.raises(ValidationError):asyncio.run(cd.get_transactions(ctx,from_,to,filters))
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("""insert into banking.transactions(customer_id,account_id,txn_date,merchant,amount,status)
            select 1001,%s,'2031-01-01'::timestamptz+n*interval '1 second','Bounded DEMO',1,'posted' from generate_series(1,205)n""",(account,))
    bounded=asyncio.run(cd.get_transactions(ctx,None,None,None)).data
    assert len(bounded)==200 and all(row['description']=='Bounded DEMO' for row in bounded)
    assert int(bounded[0]['txn_id'][4:])>int(bounded[-1]['txn_id'][4:])


def test_scope_denial_empty_records_and_audit_failure(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    with SyncPostgrestClient(local_data_api.url) as db:
        for rpc in ['customer_read_profile','customer_read_accounts','customer_read_compliance','customer_read_assignment']:
            with pytest.raises(APIError):db.rpc(rpc,{'p_customer_id':1001}).execute()
        db.auth(ctx.principal.jwt.get_secret_value())
        with pytest.raises(APIError) as mismatch:db.rpc('customer_read_accounts',{'p_customer_id':1003}).execute()
        assert mismatch.value.code=='42501'
    for principal in [Principal(kind='guest'),Principal(kind='customer',user_id=persona_id(1001),customer_id=1001)]:
        with pytest.raises(PolicyDenied):asyncio.run(cd.get_accounts(ctx.model_copy(update={'principal':principal})))
    with pytest.raises(APIError) as unlinked:asyncio.run(cd.get_accounts(context(local_data_api,9999)))
    assert unlinked.value.code=='42501'
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('delete from banking.loans where customer_id=1001')
        db.execute('delete from compliance.cases where customer_id=1001')
        db.execute('delete from rm.assignments where customer_id=1001')
    assert asyncio.run(cd.get_loans(ctx)).data==[]
    assert asyncio.run(cd.get_compliance_case(ctx)).data is None
    assert asyncio.run(cd.get_rm_assignment(ctx)).data is None
    with psycopg.connect(local_data_api.database_uri) as db:
        before=db.execute("select count(*) from audit.events where action='data.read'").fetchone()[0]
        db.execute("create function public.native_reject_read() returns trigger language plpgsql as $$ begin if new.action='data.read' then raise exception 'native audit failure'; end if; return new; end $$")
        db.execute('create trigger native_reject_read before insert on audit.events for each row execute function public.native_reject_read()')
    with pytest.raises(APIError):asyncio.run(cd.get_accounts(ctx))
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where action='data.read'").fetchone()[0]==before


def test_cross_customer_card_link_is_an_integrity_error(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        card=db.execute("insert into banking.credit_cards(customer_id,card_name,network,last4,credit_limit) values(1003,'Foreign DEMO','visa','3456',100) returning card_id").fetchone()[0]
        db.execute("insert into banking.transactions(customer_id,card_id,txn_date,merchant,amount,status) values(1001,%s,clock_timestamp(),'Invalid DEMO',1,'posted')",(card,))
    with pytest.raises(APIError) as invalid:asyncio.run(cd.get_transactions(context(local_data_api,1001),None,None,None))
    assert invalid.value.code=='23514'
