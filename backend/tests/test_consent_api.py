"""Real FastAPI -> SDK -> PostgREST -> PostgreSQL consent checks, with native audit writes."""
import asyncio
import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.schemas.consent import ConsentQuery,ConsentResult,RelationshipConsent
from app.schemas.contracts import Principal,RequestContext
from app.services.authorization import PolicyDenied
from app.services.consent import has_consent
from tests.data_api_support import NativeAPI,mint_token
from tests.database_support import persona_id

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')


def query(recipient: str) -> dict[str,str | list[str]]:
    return {'purpose':'rm_brief','recipient':recipient,'categories':['profile']}


def test_api_reads_exact_consent_and_records_audit(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    recipient=uuid4()
    with psycopg.connect(local_data_api.database_uri) as db:
        grant_id=db.execute("""insert into consent.grants(customer_id,purpose,recipient,categories,expires_at)
            values (1001,'rm_brief',%s,array['profile','accounts'],clock_timestamp()+interval '1 day') returning grant_id""",(str(recipient),)).fetchone()[0]
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    # An extra client-supplied customer_id never controls the service's code-side scope.
    response=native_api_client.post('/consent/check',headers={'Authorization':'Bearer '+token},json={**query(str(recipient)),'customer_id':1003})
    assert response.status_code==200
    result=ConsentResult.model_validate(response.json())
    assert result.granted and result.grant_id==grant_id and result.expires_at>result.checked_at
    missing=native_api_client.post('/consent/check',headers={'Authorization':'Bearer '+token},json=query(str(uuid4())))
    assert missing.status_code==200 and not ConsentResult.model_validate(missing.json()).granted
    with psycopg.connect(local_data_api.database_uri) as db:
        rows=db.execute("select customer_id,outcome,details from audit.events where action='consent.checked'").fetchall()
        assert len(rows)==2 and all(row[0]==1001 and row[1]=='success' for row in rows)
        assert rows[0][2]=={'granted':True,'category_count':1}
        assert rows[1][2]=={'granted':False,'category_count':1}
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_api_rejects_guest_and_unlinked_identity(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    data=query(str(uuid4()))
    assert native_api_client.post('/consent/check',json=data).status_code==401
    token=mint_token(local_data_api,9999,'aal1',uuid4())
    response=native_api_client.post('/consent/check',headers={'Authorization':'Bearer '+token},json=data)
    assert response.status_code==403 and response.json()['error']['code']=='profile_missing'


def test_relationship_view_uses_current_assignment_and_profile_scope(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        advisor=db.execute("select advisor_id from rm.assignments where customer_id=1003 and ended_on is null").fetchone()[0]
        db.execute("insert into consent.grants(customer_id,purpose,recipient,categories) values (1003,'rm_brief',%s,array['profile'])",(str(advisor),))
    token=mint_token(local_data_api,1003,'aal1',uuid4())
    response=native_api_client.get('/consent/rm',headers={'Authorization':'Bearer '+token})
    assert response.status_code==200
    result=RelationshipConsent.model_validate(response.json())
    assert result.recipient.rm_id==advisor and result.consent.granted and result.categories==['profile']
    with psycopg.connect(local_data_api.database_uri) as db:db.execute("delete from rm.assignments where customer_id=1001")  # a customer without any advisor
    alex=mint_token(local_data_api,1001,'aal1',uuid4())
    missing=native_api_client.get('/consent/rm',headers={'Authorization':'Bearer '+alex})
    assert missing.status_code==200
    absent=RelationshipConsent.model_validate(missing.json())
    assert absent.recipient is None and absent.consent is None


def test_database_prevents_ambiguous_current_managers(local_data_api: NativeAPI) -> None:
    import psycopg
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(local_data_api.database_uri) as db:
            db.execute("""insert into rm.assignments(customer_id,advisor_id,started_on)
                select customer_id,advisor_id,current_date from rm.assignments where customer_id=1003 and ended_on is null""")


def test_db_independently_rejects_a_context_client_mismatch(local_data_api: NativeAPI) -> None:
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    context=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',customer_id=1003,user_id=persona_id(1003),jwt=token))
    request=ConsentQuery.model_validate(query(str(uuid4())))
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        with pytest.raises(APIError) as error:asyncio.run(has_consent(request,context,db))
        assert error.value.code=='42501'


def test_agent_denial_precedes_registry_access(local_data_api: NativeAPI) -> None:
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    context=RequestContext(session_id=uuid4(),agent='document',principal=Principal(kind='customer',customer_id=1001,user_id=persona_id(1001),jwt=token))
    request=ConsentQuery.model_validate(query(str(uuid4())))
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        with pytest.raises(PolicyDenied,match='agent_tool_forbidden'):asyncio.run(has_consent(request,context,db))


def test_missing_registry_is_an_error_not_an_assumed_denial(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('drop function public.consent_check(bigint,text,uuid,text[])')
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    response=native_api_client.post('/consent/check',headers={'Authorization':'Bearer '+token},json=query(str(uuid4())))
    assert response.status_code==503 and response.json()['error']['code']=='consent_unavailable'
    assert 'granted' not in response.json()


def test_audit_failure_prevents_release_of_consent_status(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute('drop function public.audit_record_batch(jsonb)')
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    response=native_api_client.post('/consent/check',headers={'Authorization':'Bearer '+token},json=query(str(uuid4())))
    assert response.status_code==503 and response.json()['error']['code']=='audit_unavailable'


@pytest.mark.parametrize('categories',[[],['profile','profile'],['private free text'],[1]])
def test_invalid_category_contract(categories: list[str | int]) -> None:
    with pytest.raises(ValidationError):ConsentQuery.model_validate({**query(str(uuid4())),'categories':categories})
