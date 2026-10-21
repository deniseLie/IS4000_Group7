"""Real validator/graph/action integration, not model accuracy; no AI client is replaced.

Invalid samples enter the actual validator. Withheld results and a fixed safety-origin
valid section start the graph after dispatch. Browser tests separately exercise real
missing provider configuration through dispatch and retain explicit failure labels.
"""
import asyncio
import json
import os
import secrets
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from langgraph.graph.state import CompiledStateGraph
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from app.schemas.chat import ChatRequest
from app.schemas.contracts import RequestContext,Task,TaskPlan
from app.schemas.conversations import ConversationAccess,CreateConversation,TurnInput,TurnResponse
from app.schemas.events import AnswerEvent
from app.schemas.workflows import OwnedWorkflow,PipelineContext,ResponseMeta
from app.services import conversations
from app.services.agents.answer import WITHHELD,validate_draft
from app.services.checkpoints import PostgrestCheckpointSaver,signing_key
from app.services.guardrail import assess_request
from app.services.orchestrator import Turn,build_graph
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires real native PostgreSQL/PostgREST')


def graph_for(db: SyncPostgrestClient,ctx: RequestContext,owned: OwnedWorkflow) -> CompiledStateGraph[Turn,PipelineContext,Turn,Turn]:
    return build_graph(PostgrestCheckpointSaver(db,owned.access,ctx.model_copy(update={'case_id':owned.turn.case_id}),owned.turn,signing_key()))


async def prepare_validation(db: SyncPostgrestClient,ctx: RequestContext) -> OwnedWorkflow:
    access=ConversationAccess(session_id=uuid4(),guest_token=None)
    await conversations.create(CreateConversation(session_id=access.session_id,mode='personal',language='English'),access,ctx,db)
    question='Check my compliance deadline and account interest.'
    started=await conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text=question,priority='P2'),access,ctx,db)
    owned=OwnedWorkflow(access=access,turn=started.turn)
    req=ChatRequest(messages=[{'role':'user','content':question}],mode='personal')
    tasks=[Task(task_id='t1',domain='compliance',intent='answer_question',priority='P2',entities={'question':'My compliance deadline'}),
        Task(task_id='t2',domain='retail',intent='answer_question',priority='P3',entities={'question':'My account interest'}),
        Task(task_id='t3',domain='smalltalk',intent='answer_question',priority='P3',entities={'question':'Hello'})]
    invalid=json.dumps({'answer':'Unsupported sample interest is 987.654%.','source_ids':[],
        'answer_kind':'banking','status':'ok','clarification_question':''})
    valid=json.dumps({'answer':'Hello.','source_ids':[],'answer_kind':'conversation','status':'ok','clarification_question':''})
    results=[validate_draft(task,valid if task.domain=='smalltalk' else invalid,[]) for task in tasks]
    assert [result.status for result in results]==['error','error','ok']
    await graph_for(db,ctx,owned).aupdate_state({'configurable':{'thread_id':str(owned.turn.case_id)}},
        {'request':req,'history':req.messages,'history_count':1,'risk':assess_request(req),'workflow_version':'v2-actions-v1',
            'plan':TaskPlan(tasks=tasks),'results':results,'evidence':[[],[],[]],
            'metadata':[ResponseMeta(origin='validation',prompt_id=None),ResponseMeta(origin='validation',prompt_id=None),ResponseMeta(origin='safety',prompt_id=None)],
            'proposals':[None,None,None]},as_node='dispatch')
    return owned


async def saved_validation(db: SyncPostgrestClient,ctx: RequestContext) -> tuple[OwnedWorkflow,AnswerEvent]:
    owned=await prepare_validation(db,ctx)
    current=ctx.model_copy(update={'case_id':owned.turn.case_id})
    graph=graph_for(db,current,owned)
    config={'configurable':{'thread_id':str(owned.turn.case_id)}}
    runtime=PipelineContext(request=current,workflow=owned)
    result=await graph.ainvoke(None,config,context=runtime,durability='sync')
    before=[snapshot async for snapshot in graph.aget_state_history(config) if snapshot.next==('escalate_validation',)]
    assert len(before)==1
    repeated=await graph.ainvoke(None,before[0].config,context=runtime,durability='sync')
    assert repeated['answer']==result['answer']
    answer=AnswerEvent.model_validate(result['answer'])
    await conversations.finish_turn(TurnResponse(turn=owned.turn,response=None),answer,owned.access,current,db)
    return owned,answer


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))


def test_mixed_validation_handoff_is_durable_deduplicated_and_owned(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        owned,answer=asyncio.run(saved_validation(db,ctx))
    assert answer.response_origin=='validation' and answer.source_check=='rejected' and answer.needs_human
    assert [section.answer for section in answer.sections[:3]]==[WITHHELD,WITHHELD,'Hello.']
    assert len(answer.sections)==4 and answer.sections[-1].response_origin=='safety'
    assert 'handoff is recorded' in answer.sections[-1].answer and '987.654' not in answer.answer
    stored=native_api_client.get(f'/conversations/{owned.access.session_id}',headers=headers(ctx))
    assert stored.status_code==200 and stored.json()['messages'][-1]['payload']['sections']==answer.model_dump(mode='json')['sections']
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select reason,priority,summary from support.handoffs').fetchone()==('general_support','P2',
            'An assistant section was withheld because it could not be verified. Review the saved customer case; no rejected answer is included.')
        assert db.execute('select count(*) from actions.receipts').fetchone()==(1,)
        assert db.execute('select count(*) from events.outbox where command_id is not null').fetchone()==(1,)
        assert db.execute('select state from conversation.agent_workflows').fetchone()==('completed',)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        assert '987.654' not in str(db.execute('select data from conversation.checkpoints').fetchall())
        command_id=db.execute('select command_id from actions.commands').fetchone()[0]
    foreign=context(local_data_api,1003)
    assert native_api_client.get(f'/commands/{command_id}',headers=headers(foreign)).status_code==404
    assert native_api_client.get(f'/conversations/{owned.access.session_id}',headers=headers(foreign)).status_code==404


def test_handoff_outbox_failure_rolls_back_and_retry_records_one_receipt(local_data_api: NativeAPI,native_api_client: TestClient,enabled: None) -> None:
    import psycopg
    ctx=context(local_data_api,1001)
    with SyncPostgrestClient(local_data_api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        owned=asyncio.run(prepare_validation(db,ctx))
        graph=graph_for(db,ctx,owned)
        config={'configurable':{'thread_id':str(owned.turn.case_id)}}
        runtime=PipelineContext(request=ctx.model_copy(update={'case_id':owned.turn.case_id}),workflow=owned)
        with psycopg.connect(local_data_api.database_uri) as sql:
            sql.execute("create function events.reject_validation_test() returns trigger language plpgsql as $$begin raise exception 'validation outbox test'; end$$")
            sql.execute('create trigger reject_validation_test before insert on events.outbox for each row execute function events.reject_validation_test()')
        with pytest.raises(APIError):asyncio.run(graph.ainvoke(None,config,context=runtime,durability='sync'))
        with psycopg.connect(local_data_api.database_uri) as sql:
            for table in ['support.handoffs','actions.commands','actions.receipts','actions.workflow_commands']:
                assert sql.execute('select count(*) from '+table).fetchone()==(0,)
            sql.execute('drop trigger reject_validation_test on events.outbox')
        result=asyncio.run(graph.ainvoke(None,config,context=runtime,durability='sync'))
        assert 'handoff is recorded' in result['answer'].answer
        with psycopg.connect(local_data_api.database_uri) as sql:
            assert sql.execute('select count(*) from actions.receipts').fetchone()==(1,)
            assert sql.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
