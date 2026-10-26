"""Own real customer evidence and direct validators; optional model checks use the real provider."""
import asyncio
import json
import os
import re
from collections.abc import Iterator
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from app.core.config import settings
from app.schemas.contracts import Task
from app.schemas.retail import RetailRecords
from app.services.agents import retail,retail_records
from app.services import audit
from app.services.agents.answer import validate_draft
from app.services.authorization import PolicyDenied
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers


@pytest.fixture(autouse=True)
def retail_audit_context() -> Iterator[None]:
    token=audit.current_batch.set(None)
    try:yield
    finally:audit.current_batch.reset(token)


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
@pytest.mark.parametrize('cid,expected',[ (1001,5230.45),(1002,250000.0),(1003,84120.0)])
def test_retail_records_are_real_masked_and_customer_scoped(local_data_api: NativeAPI,native_api_client: TestClient,cid: int,expected: float) -> None:
    import psycopg
    ctx=context(local_data_api,cid).model_copy(update={'agent':'retail'})
    records=asyncio.run(retail_records.load(ctx))
    assert len(records.accounts)==1 and records.accounts[0].balance==expected and records.accounts[0].number.startswith('****')
    assert len(records.transactions)=={1001:4,1002:3,1003:4}[cid]  # 0063 adds everyday activity for Michelle and David
    evidence=retail.evidence_for(records)
    assert len(evidence)==5 and all(item.source.url is None for item in evidence)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where customer_id=%s and action='data.read'",(cid,)).fetchone()==(5,)
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
    with pytest.raises(PolicyDenied):asyncio.run(retail_records.load(ctx.model_copy(update={'agent':'document'})))


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_failed_retail_reader_never_substitutes_records(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.customer_read_loans(bigint)')
    with pytest.raises(APIError):asyncio.run(retail_records.load(context(local_data_api,1001).model_copy(update={'agent':'retail'})))


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_dataset_citations_reject_foreign_figures_and_currency(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    records=asyncio.run(retail_records.load(context(local_data_api,1001).model_copy(update={'agent':'retail'})))
    evidence=retail.evidence_for(records)
    task=Task(task_id='t1',domain='retail',intent='answer_question')
    draft={'answer':'Your synthetic account balance is SGD 5,230.45.','source_ids':['retail-accounts'],'answer_kind':'banking','status':'ok','clarification_question':''}
    assert validate_draft(task,json.dumps(draft),evidence).status=='ok'
    for invalid in [{**draft,'answer':'Your balance is USD 5,230.45.'},{**draft,'answer':'Your balance is SGD 84,120.00.'},
        {**draft,'source_ids':['retail-cards']},{**draft,'answer':'I have transferred SGD 5,230.45.'}]:
        assert validate_draft(task,json.dumps(invalid),evidence).status=='error'


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native data services')
def test_missing_provider_withholds_answer_and_records_support_handoff(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-retail-checkpoint-key-with-at-least-32-characters')
    monkeypatch.delenv('LLM_MODEL',raising=False)
    ctx=context(local_data_api,1001);sid=uuid4()
    assert native_api_client.post('/conversations',headers=headers(ctx),json={'session_id':str(sid),'mode':'personal','language':'English'}).status_code==200
    response=native_api_client.post('/chat',headers=headers(ctx),json={'mode':'personal','messages':[{'role':'user','content':'What is my account balance?'}],
        'conversation_id':str(sid),'turn_id':str(uuid4()),'revision':0})
    assert 'event: answer' in response.text and '"response_origin": "validation"' in response.text,response.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select type,state from actions.commands').fetchall()==[('handoff.create','succeeded')]
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1,)
        assert db.execute('select reason from support.handoffs').fetchall()==[('general_support',)]
        assert db.execute("select count(*) from audit.events where action='data.read' and details->>'tool' in "
            "('customer.get_profile','customer.get_accounts','customer.get_credit_cards','customer.get_loans','customer.get_transactions')").fetchone()==(5,)


@pytest.mark.skipif(os.getenv('TEST_RETAIL_LIVE')!='1',reason='Opt in to actual personal retail model calls')
def test_real_retail_provider_uses_owned_account_currency(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    async def check() -> None:
        for cid,amount in [(1001,'5,230.45'),(1002,'250,000'),(1003,'84,120')]:
            task=Task(task_id='live1',domain='retail',intent='answer_question',entities={'question':'What is my account balance? State the recorded currency and exact amount only.'})
            result,evidence,prompt_id=await retail.respond(task,[],'English',context(local_data_api,cid).model_copy(update={'agent':'retail'}),[])
            assert result.status=='ok' and result.answer_kind=='banking',result.model_dump_json()
            assert amount.replace(',','') in result.text.replace(',','') and 'SGD' in result.text,result.text
            assert {item.source_id for item in result.citations}=={'retail-accounts'}
            assert prompt_id.startswith('v2+') and len(evidence)==5
    asyncio.run(check())


@pytest.mark.skipif(os.getenv('TEST_RETAIL_LIVE')!='1',reason='Opt in to actual personal retail model calls')
def test_real_retail_provider_does_not_infer_unrecorded_loan_currency(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    task=Task(task_id='live2',domain='retail',intent='answer_question',entities={'question':'What is the recorded currency of my loan?'})
    result,_,_=asyncio.run(retail.respond(task,[],'English',context(local_data_api,1001).model_copy(update={'agent':'retail'}),[]))
    assert result.status in {'ok','needs_human'} and result.answer_kind=='banking',result.model_dump_json()
    assert re.search(r'unrecorded|not (?:recorded|specified|provided|available)',result.text,re.I),result.text
    assert not re.search(r'\b(?:SGD|USD|EUR|GBP|AUD)\b',result.text),result.text
    assert {item.source_id for item in result.citations}=={'retail-loans'}
