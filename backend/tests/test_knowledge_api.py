"""Real knowledge RPC/SDK/API checks; no AI output is simulated or asserted as live accuracy."""
import asyncio
import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.schemas.contracts import Principal,RequestContext,Task
from app.schemas.knowledge import KnowledgeDetails,KnowledgeFact,KnowledgeQuery
from app.services import kb
from app.services.orchestrator import database_evidence
from tests.data_api_support import NativeAPI,mint_token

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgREST with TEST_DATA_API_LOCAL=1')


def guest() -> RequestContext:
    return RequestContext(session_id=uuid4(),principal=Principal(kind='guest'))


def test_real_search_scope_no_match_and_missing_fact(local_data_api: NativeAPI) -> None:
    with SyncPostgrestClient(local_data_api.url) as db:
        results=asyncio.run(kb.search(KnowledgeQuery(query='fixed deposit early withdrawal',scope='retail',k=4),guest(),db))
        assert results and any(item.article_id=='sg-accounts' for item in results)
        assert all(item.score>0 and item.current and item.cite==item.source().id for item in results)
        assert asyncio.run(kb.search(KnowledgeQuery(query='zxqvfrubble',scope='public',k=4),guest(),db))==[]
        assert asyncio.run(kb.search(KnowledgeQuery(query='the and or',scope='public',k=4),guest(),db))==[]
        assert asyncio.run(kb.search(KnowledgeQuery(query='fixed deposit',scope='compliance',k=4),guest(),db))==[]
        assert asyncio.run(kb.get_fact('fee.card_replacement',guest(),db)) is None


def test_source_round_trip_guest_customer_and_audit(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with SyncPostgrestClient(local_data_api.url) as db:
        record=asyncio.run(kb.search(KnowledgeQuery(query='fixed deposit',scope='public',k=1),guest(),db))[0]
    for headers in [{},{'Authorization':'Bearer '+mint_token(local_data_api,9999,'aal1',uuid4())}]:
        response=native_api_client.get(record.source().url,headers=headers)
        assert response.status_code==200
        details=KnowledgeDetails.model_validate(response.json())
        assert details.id==record.cite and details.content==record.body and details.notice==record.scope
        assert details.reference_url==record.reference_url and details.current
    with psycopg.connect(local_data_api.database_uri) as db:
        events=db.execute("select details from audit.events where action='data.read'").fetchall()
        assert len(events)==2 and all(row[0]=={'tool':'kb.search','result_count':1} for row in events)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_retired_citation_readable_draft_hidden_and_search_uses_latest(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update kb.articles set status='retired' where article_id='accounts'")
        db.execute("""insert into kb.articles select article_id,2,title,categories,publisher,scope,kind,'published',
            reference_url,checked_on,review_due from kb.articles where article_id='accounts' and version=1""")
        db.execute("""insert into kb.sections select article_id,2,anchor,heading,body,keywords
            from kb.sections where article_id='accounts' and version=1""")
        db.execute("update kb.articles set status='draft' where article_id='loans'")
    old=native_api_client.get('/knowledge/sections/accounts/1/main')
    assert old.status_code==200 and old.json()['current'] is False and old.json()['version']=='v1'
    assert native_api_client.get('/knowledge/sections/loans/1/main').status_code==404
    assert native_api_client.get('/knowledge/sections/accounts/2/missing').status_code==404
    with SyncPostgrestClient(local_data_api.url) as db:
        records=asyncio.run(kb.search(KnowledgeQuery(query='account balance',scope='public',k=20),guest(),db))
        versions={item.version for item in records if item.article_id=='accounts'}
        assert versions=={2} and all(item.article_id!='loans' for item in records)


@pytest.mark.parametrize('review',['past','future'])
def test_out_of_review_sources_are_not_searched(local_data_api: NativeAPI,native_api_client: TestClient,review: str) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        if review=='past':db.execute("update kb.articles set checked_on=current_date-10,review_due=current_date-1 where article_id='sg-accounts'")
        else:db.execute("update kb.articles set checked_on=current_date+1,review_due=current_date+10 where article_id='sg-accounts'")
    with SyncPostgrestClient(local_data_api.url) as db:
        records=asyncio.run(kb.search(KnowledgeQuery(query='fixed deposit',scope='public',k=20),guest(),db))
        assert all(item.article_id!='sg-accounts' for item in records)
    response=native_api_client.get('/knowledge/sections/sg-accounts/1/main')
    assert response.status_code==200 and response.json()['current'] is False


def test_real_fact_value_and_no_write_access(local_data_api: NativeAPI) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("""insert into kb.facts(fact_id,value,unit,article_id,version,anchor)
            values ('test.synthetic_amount','12.5'::jsonb,'SGD','accounts',1,'main')""")
    with SyncPostgrestClient(local_data_api.url) as db:
        fact=asyncio.run(kb.get_fact('test.synthetic_amount',guest(),db))
        assert isinstance(fact,KnowledgeFact) and fact.value==12.5 and fact.source_id=='kb:accounts@v1#main'
        with pytest.raises(APIError):db.schema('kb').table('facts').delete().eq('fact_id','test.synthetic_amount').execute()
        assert asyncio.run(kb.get_fact('test.synthetic_amount',guest(),db))==fact


def test_registry_failure_is_not_a_no_match(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.kb_get_section(text,int,text)')
    response=native_api_client.get('/knowledge/sections/accounts/1/main')
    assert response.status_code==503 and response.json()['error']['code']=='knowledge_unavailable'


def test_source_is_not_released_if_audit_fails(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import psycopg
    with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.audit_record_batch(jsonb)')
    response=native_api_client.get('/knowledge/sections/accounts/1/main')
    assert response.status_code==503 and response.json()['error']['code']=='audit_unavailable'
    assert 'content' not in response.json()


def test_orchestrator_evidence_uses_real_db_without_model(local_data_api: NativeAPI,native_api_client: TestClient) -> None:
    import json
    import psycopg
    from app.api.chat import audit_reply
    from app.core.observability import start_trace
    from app.schemas.chat import ChatRequest
    from app.schemas.contracts import TaskPlan
    from app.services import audit
    from app.services.agents.answer import validate_draft
    from app.services.orchestrator import compose
    task=Task(task_id='t1',domain='retail',intent='answer_question',entities={'question':'fixed deposit'})
    evidence=asyncio.run(database_evidence(task,guest()))
    assert evidence and any(item.id=='kb:sg-accounts@v1#main' for item in evidence)
    assert all(item.publisher and item.scope and item.id==item.source.id for item in evidence)
    # Validate a sample string directly, without representing it as a live model response.
    result=validate_draft(task,json.dumps({'answer':'Early withdrawal may reduce interest.',
        'source_ids':['kb:sg-accounts@v1#main'],'answer_kind':'banking','status':'ok','clarification_question':''}),evidence)
    request=ChatRequest(messages=[{'role':'user','content':'fixed deposit'}])
    answer=compose({'request':request,'history':request.messages,'plan':TaskPlan(tasks=[task]),
        'results':[result],'evidence':[evidence]})['answer']
    assert answer.sources[0].id=='kb:sg-accounts@v1#main' and not answer.needs_human
    batch=audit.start_batch(None,session_id=uuid4())
    trace=start_trace()
    audit_reply(batch,trace,answer.model_dump(mode='json'))
    asyncio.run(batch.flush())
    with psycopg.connect(local_data_api.database_uri) as db:
        details=db.execute("select details from audit.events where action='chat.reply_sent'").fetchone()[0]
        assert details['source_ids']==['sg-accounts@v1#main']
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    missing=task.model_copy(update={'entities':{'question':'zxqvfrubble'}})
    assert asyncio.run(database_evidence(missing,guest()))==[]


@pytest.mark.parametrize('payload',[{'query':'x','scope':'private','k':1},{'query':'x','scope':'public','k':0},
    {'query':'x','scope':'public','k':True},{'query':'x'*4001,'scope':'public','k':4}])
def test_invalid_query_contracts(payload: dict[str,str | int]) -> None:
    with pytest.raises(ValidationError):KnowledgeQuery.model_validate(payload)
