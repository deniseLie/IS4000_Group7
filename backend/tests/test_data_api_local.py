"""Native Data API and SDK checks; requires both local integration opt-in flags."""
import os
from decimal import Decimal
from uuid import uuid4
import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import BaseModel,ConfigDict,Field,TypeAdapter
from tests.data_api_support import NativeAPI,mint_token

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')


class SearchHit(BaseModel):
    model_config=ConfigDict(extra='ignore')
    cite: str
    article_id: str
    publisher: str
    scope: str
    score: float = Field(gt=0)


class Account(BaseModel):
    model_config=ConfigDict(extra='ignore')
    customer_id: int
    balance: Decimal


class AuditChain(BaseModel):
    model_config=ConfigDict(extra='ignore')
    checked: int
    first_bad_event_id: int | None


def test_anonymous_sdk_can_search_only_public_knowledge(local_data_api: NativeAPI) -> None:
    with SyncPostgrestClient(local_data_api.url) as db:
        data=db.rpc('kb_search',{'p_query':'fixed deposit early withdrawal','p_categories':['retail'],'p_k':4}).execute().data
        hits=TypeAdapter(list[SearchHit]).validate_python(data)
        assert hits and any(hit.article_id=='sg-accounts' for hit in hits)
        assert all(hit.cite.startswith('kb:') and hit.publisher and hit.scope for hit in hits)
        assert db.rpc('kb_get_fact',{'p_fact_id':'fee.card_replacement'}).execute().data==[]
        with pytest.raises(APIError):db.schema('banking').table('bank_accounts').select('*').execute()


@pytest.mark.parametrize('customer_id',[1001,1002,1003])
def test_jwt_roles_apply_rls_through_real_sdk_http(local_data_api: NativeAPI,customer_id: int) -> None:
    token=mint_token(local_data_api,customer_id,'aal1',uuid4())
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        data=db.schema('banking').table('bank_accounts').select('customer_id,balance').execute().data
        accounts=TypeAdapter(list[Account]).validate_python(data)
        assert len(accounts)==1 and accounts[0].customer_id==customer_id
        with pytest.raises(APIError):db.schema('banking').table('bank_accounts').update({'balance':0}).eq('customer_id',customer_id).execute()


def test_native_jwks_and_signature_checks(local_data_api: NativeAPI) -> None:
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    key=jwt.PyJWKClient(local_data_api.url+'/rpc/native_test_jwks').get_signing_key_from_jwt(token)
    claims=jwt.decode(token,key.key,algorithms=['ES256'],audience='authenticated')
    assert claims['role']=='authenticated'
    invalid=jwt.encode({'sub':claims['sub'],'role':'authenticated','aud':'authenticated','exp':claims['exp']},'synthetic-wrong-signing-key-for-local-tests-only',algorithm='HS256')
    response=httpx.get(local_data_api.url+'/bank_accounts',headers={'Authorization':'Bearer '+invalid,'Accept-Profile':'banking'})
    assert response.status_code==401


def test_internal_schemas_are_not_exposed(local_data_api: NativeAPI) -> None:
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    response=httpx.get(local_data_api.url+'/outbox',headers={'Authorization':'Bearer '+token,'Accept-Profile':'events'})
    assert response.status_code==406


def test_real_api_gateway_and_audit_use_native_jwks_and_customer_jwt(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    token=mint_token(local_data_api,1001,'aal1',uuid4())
    headers={'Authorization':'Bearer '+token}
    response=native_api_client.post('/chat',headers=headers,json={'mode':'personal','messages':[{'role':'user','content':'I lost my card'}]})
    assert response.status_code==200 and 'event: answer' in response.text and 'event: error' not in response.text
    assert '"response_origin": "safety"' in response.text
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(token)
        chains=TypeAdapter(list[AuditChain]).validate_python(db.rpc('audit_verify_chain',{'p_at':None}).execute().data)
        assert len(chains)==1 and chains[0].checked>=3 and chains[0].first_bad_event_id is None
    unlinked=mint_token(local_data_api,9999,'aal1',uuid4())
    denied=native_api_client.post('/chat',headers={'Authorization':'Bearer '+unlinked},
        json={'mode':'personal','messages':[{'role':'user','content':'What is my balance?'}]})
    assert denied.status_code==403 and denied.json()['error']['code']=='profile_missing'
