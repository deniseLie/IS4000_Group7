"""Charges the client does not recognise go to the fraud team (0081): no block or dispute offer, no paused turn, one urgent
fraud-team handoff and ticket per reported card, and the reply quotes its reference. Native PostgreSQL/PostgREST, real graph nodes.

The fixture starts after dispatch with safety-origin text, as tests/test_workflows.py does; it does not test model wording.
"""
import asyncio
import os
import secrets
from uuid import uuid4
import psycopg
import pytest
from postgrest import SyncPostgrestClient
from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentResult,Task,TaskPlan
from app.schemas.conversations import ConversationAccess,CreateConversation,TurnInput
from app.schemas.fraud import DisputeProposal,FraudProposal
from app.schemas.workflows import OwnedWorkflow,PipelineContext,ResponseMeta
from app.services import conversations,orchestrator
from app.services.checkpoints import PostgrestCheckpointSaver,signing_key
from app.services.guardrail import assess_request
from app.services.orchestrator import build_graph
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')
QUERY="I don't recognise the London charges on my card ending 7310."


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.setattr(settings,'conversation_store','database')


async def report(db: SyncPostgrestClient,api: NativeAPI,again: bool=False) -> dict:
    ctx=context(api,1002)
    access=ConversationAccess(session_id=uuid4(),guest_token=None)
    await conversations.create(CreateConversation(session_id=access.session_id,mode='personal',language='English'),access,ctx,db)
    turn=await conversations.start_turn(TurnInput(turn_id=uuid4(),revision=0,user_text=QUERY,priority='P0'),access,ctx,db)
    owned=OwnedWorkflow(access=access,turn=turn.turn)
    current=ctx.model_copy(update={'case_id':turn.turn.case_id})
    req=ChatRequest(messages=[{'role':'user','content':QUERY}],mode='personal')
    graph=build_graph(PostgrestCheckpointSaver(db,access,current,turn.turn,signing_key()))
    config={'configurable':{'thread_id':str(turn.turn.case_id)}}
    task=Task(task_id='t1',domain='fraud',intent='answer_question',priority='P0',entities={'question':QUERY})
    await graph.aupdate_state(config,{'request':req,'history':req.messages,'history_count':1,'risk':assess_request(req),
        'workflow_version':'v2-actions-v1','plan':TaskPlan(tasks=[task]),
        'results':[AgentResult(task_id='t1',agent='fraud',status='needs_human',text='Call the fraud line on the back of your card now.',answer_kind='conversation')],
        'evidence':[[]],'metadata':[ResponseMeta(origin='safety',prompt_id=None)],
        'proposals':[FraudProposal(card_id='card_10021',transaction_id='txn_100216')],
        'disputes':[DisputeProposal(card_id='card_10021',transaction_ids=['txn_100215','txn_100216'])]},as_node='dispatch')
    result=await graph.ainvoke(None,config,context=PipelineContext(request=current,workflow=owned),durability='sync')
    if again:  # a retried settle step (same turn) must reuse the recorded handoff, not open a second one
        await orchestrator.report_to_fraud_team(result,current,owned)
    return result


def run(api: NativeAPI,again: bool=False) -> dict:
    ctx=context(api,1002)
    with SyncPostgrestClient(api.url) as db:
        db.auth(ctx.principal.jwt.get_secret_value())
        return asyncio.run(report(db,api,again))


def test_reported_charges_go_to_the_fraud_team_without_a_pause_or_a_block(local_data_api: NativeAPI,native_api_client,enabled: None) -> None:
    result=run(local_data_api)
    assert '__interrupt__' not in result and result['pending_reviews']==[]
    answer=result['answer']
    with psycopg.connect(local_data_api.database_uri) as db:
        handoffs=db.execute("select reason,priority,summary from support.handoffs where customer_id=1002").fetchall()
        tickets=db.execute("select t.reference,t.category,t.urgency,t.summary from support.tickets t join support.handoffs h using(handoff_id) where h.customer_id=1002").fetchall()
        assert db.execute('select status from banking.credit_cards where card_id=10021').fetchone()==('active',)  # only the bank blocks it
        assert db.execute("select count(*) from actions.commands where customer_id=1002 and type in ('card.temporary_block','card.dispute')").fetchone()==(0,)
    assert [(reason,priority) for reason,priority,_ in handoffs]==[('card_verification','P0')]
    summary=handoffs[0][2]
    assert 'Card ending 7310' in summary and 'MAYFAIR AUDIO LONDON' in summary and 'LUXEHAUS LONDON' in summary and 'Temporary block' not in summary
    (reference,category,urgency,title),=tickets
    assert (category,urgency)==('suspected_fraud','urgent') and 'fraud team contacts you' in title
    assert f'reference {reference}' in answer.answer and 'fraud team' in answer.answer and 'I can’t block the card' in answer.answer
    assert answer.needs_human and not any(section.pending_actions for section in answer.sections)


def test_a_retried_turn_reuses_the_same_report(local_data_api: NativeAPI,native_api_client,enabled: None) -> None:
    run(local_data_api,again=True)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select count(*) from support.handoffs where customer_id=1002 and reason='card_verification'").fetchone()==(1,)
