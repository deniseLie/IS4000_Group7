"""Real concurrent JWT/RLS reads and task deadlines; missing provider config is an explicit failure.

No model or service is replaced. PostgreSQL delays/failures exercise the normal reader and
dispatch boundary; successful AI/UI behavior is checked by the opt-in foundation browser suite.
"""
import asyncio
import os
from datetime import datetime,timedelta
from collections.abc import Iterator
import pytest
from fastapi.testclient import TestClient
from langgraph.runtime import Runtime
from postgrest import SyncPostgrestClient
from app.core.config import settings
from app.core.observability import Trace,current_trace,start_trace
from app.schemas.agents import PlannedIssue
from app.schemas.chat import ChatRequest
from app.schemas.contracts import RequestContext
from app.schemas.workflows import PipelineContext
from app.services import audit,prompts
from app.services.guardrail import assess_request
from app.services.agents import retail
from app.services.orchestrator import DispatchUpdate,compose,dispatch,prioritise
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')


@pytest.fixture(autouse=True)
def isolated_dispatch_context() -> Iterator[None]:
    batch_token=audit.current_batch.set(None);trace_token=current_trace.set(None)
    try:yield
    finally:
        audit.current_batch.reset(batch_token);current_trace.reset(trace_token)


def delay_accounts(api: NativeAPI) -> None:
    import psycopg
    with psycopg.connect(api.database_uri) as db:
        db.execute('alter function public.customer_read_accounts(bigint) rename to native_accounts')
        db.execute("""create function public.customer_read_accounts(p_customer_id bigint) returns jsonb
            language plpgsql volatile security invoker set search_path='' as $$ begin
            perform pg_sleep(1.5); return public.native_accounts(p_customer_id); end $$""")
        db.execute('revoke all on function public.customer_read_accounts(bigint) from public')
        db.execute('grant execute on function public.customer_read_accounts(bigint) to authenticated')


async def run_dispatch(api: NativeAPI,ctx: RequestContext) -> tuple[DispatchUpdate,Trace]:
    request=ChatRequest(messages=[{'role':'user','content':'Explain my account and my review status.'}],mode='personal')
    state={'request':request,'history':request.messages,'history_count':1,'risk':assess_request(request),
        'issues':[PlannedIssue(domain='retail',question='Explain my account.'),
            PlannedIssue(domain='compliance',question='When is my review due?')]}
    state={**state,**prioritise(state)}
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        batch=audit.start_batch(db,session_id=ctx.session_id);trace=start_trace()
        update=await dispatch(state,Runtime(context=PipelineContext(request=ctx,workflow=None)))
        answer=compose({**state,**update,'decision_sections':[]})['answer']
        assert [section.domain for section in answer.sections]==['compliance','retail']
        assert all(section.response_origin=='validation' and section.needs_human for section in answer.sections)
        await batch.flush()
        return update,trace


@pytest.mark.parametrize('failure',['provider','reader','timeout'])
def test_independent_personal_tasks_overlap_and_keep_owned_failures(local_data_api: NativeAPI,native_api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,failure: str) -> None:
    import psycopg
    monkeypatch.delenv('LLM_MODEL',raising=False)
    if failure=='reader':
        with psycopg.connect(local_data_api.database_uri) as db:db.execute('drop function public.customer_read_accounts(bigint)')
    else:delay_accounts(local_data_api)
    if failure=='timeout':monkeypatch.setattr(settings,'agent_timeout_s',0.5)
    update,trace=asyncio.run(run_dispatch(local_data_api,context(local_data_api,1001)))
    assert [result.task_id for result in update['results']]==['t2','t1']
    assert all(result.status=='error' and result.citations==[] for result in update['results'])
    assert update['evidence']==[[],[]] and update['proposals']==[None,None]
    assert [meta.prompt_id for meta in update['metadata']]==[prompts.load('compliance','v3')['id'],prompts.load('retail',retail.PROMPT)['id']]
    spans={item['attrs']['agent']:item for item in trace.spans if item['name']=='task'}
    assert spans['compliance']['error_type']=='LLMNotConfigured'
    assert spans['retail']['error_type']=={'provider':'LLMNotConfigured','reader':'APIError','timeout':'TimeoutError'}[failure]
    if failure!='reader':
        compliance_end=datetime.fromisoformat(spans['compliance']['started_at'])+timedelta(milliseconds=spans['compliance']['ms'])
        retail_start=datetime.fromisoformat(spans['retail']['started_at'])
        assert retail_start<compliance_end<retail_start+timedelta(milliseconds=spans['retail']['ms'])
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from audit.events where customer_id=1001 and action='data.read' and details->>'tool'='compliance.get_case'").fetchone()==(1,)
        assert db.execute("select count(*) from audit.events where action='agent.failed'").fetchone()==(2,)
        assert db.execute('select count(*) from actions.commands').fetchone()==(0,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
