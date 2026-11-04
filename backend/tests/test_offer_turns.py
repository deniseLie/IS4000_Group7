"""v5 confirmations as chat turns (build plan phase 5), through the normal /chat route on native PostgreSQL/PostgREST.

Turn 1 offers the fraud-team report and records nothing; a typed "yes" is planner → act → compose (no agent, no model) and
records one P0 card_verification handoff with its suspected_fraud ticket; a second "yes" finds nothing open. The Security
output is a fixed safety-origin fixture for the graph mechanics (the charge wording is tested in test_fraud_confirmation.py);
no model answer is simulated.

Run from backend/: TEST_DATA_API_LOCAL=1 ../.venv/bin/python -m pytest tests/test_offer_turns.py -q
"""
import json
import os
import secrets
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.schemas.contracts import AgentOutput,AgentResult,OfferProposal
from app.services import orchestrator
from app.services.agents import runner
from evaluation.targets import sse
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')
QUERY="I don't recognise the London charges on my card ending 7310."
ASK='Can I report these charges to our fraud team as urgent?'


@pytest.fixture
def security(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.setattr(settings,'conversation_store','database')
    ran: list[str]=[]
    async def run(spec,task,inputs,ctx,history=None,language='English'):
        ran.append(spec.name)
        return AgentOutput(task_id=task.id,agent=spec.name,status='needs_human',origin='safety',decision=ASK,
            result=AgentResult(task_id=task.id,agent=spec.name,status='needs_human',answer_kind='conversation',text='Call the fraud line on the back of your card now.'),
            proposals=[OfferProposal(task_id=task.id,agent='security',type='handoff.create',summary='Report these charges to our fraud team as urgent',
                target={'reason':'card_verification','priority':'P0','summary':'Card ending 7310: two London charges the customer does not recognise.'})])
    monkeypatch.setattr(runner,'run_agent',run)
    return ran


def chat(client: TestClient,auth: dict[str,str],session: str,revision: int,text: str) -> tuple[dict,int]:
    body={'messages':[{'role':'user','content':text}],'mode':'personal','language':'English','conversation_id':session,'turn_id':str(uuid4()),'revision':revision}
    response=client.post('/chat',headers=auth,json=body)
    assert response.status_code==200,response.text
    events=sse(response.text)
    assert 'error' not in [e for e,_ in events],response.text
    revision=next(json.loads(d)['revision'] for e,d in reversed(events) if e=='conversation')
    return json.loads(next(d for e,d in events if e=='answer')),revision


def conversation(client: TestClient,auth: dict[str,str]) -> tuple[str,int]:
    session=str(uuid4())
    created=client.post('/conversations',headers=auth,json={'session_id':session,'mode':'personal','language':'English'})
    created.raise_for_status()
    return session,created.json()['revision']


def counts(api: NativeAPI) -> tuple[int,int]:
    with psycopg.connect(api.database_uri) as db:
        return (db.execute("select count(*) from support.handoffs where customer_id=1002 and reason='card_verification'").fetchone()[0],
            db.execute("select count(*) from support.tickets where customer_id=1002 and category='suspected_fraud'").fetchone()[0])


def test_a_typed_yes_records_the_offered_report_once(local_data_api: NativeAPI,native_api_client: TestClient,security: list[str]) -> None:
    auth=headers(context(local_data_api,1002))
    session,revision=conversation(native_api_client,auth)
    first,revision=chat(native_api_client,auth,session,revision,QUERY)
    assert security==['security'] and counts(local_data_api)==(0,0)
    assert first['offer'] and [o['type'] for o in first['offer']['offers']]==['handoff.create'] and ASK in first['answer']
    second,revision=chat(native_api_client,auth,session,revision,'yes')
    assert security==['security'] and counts(local_data_api)==(1,1)  # no agent on the confirming turn
    with psycopg.connect(local_data_api.database_uri) as db:
        reference,priority=db.execute("""select t.reference,h.priority from support.tickets t join support.handoffs h using(handoff_id)
            where h.customer_id=1002 and h.reason='card_verification'""").fetchone()
        assert db.execute('select status from banking.credit_cards where card_id=10021').fetchone()==('active',)  # only the bank blocks it
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
    assert priority=='P0' and reference in second['answer'] and second['offer'] is None and second['needs_human']
    third,_=chat(native_api_client,auth,session,revision,'yes')
    assert third['answer']==orchestrator.EXPIRED and counts(local_data_api)==(1,1)


def test_a_no_cancels_the_offer_and_records_nothing(local_data_api: NativeAPI,native_api_client: TestClient,security: list[str]) -> None:
    auth=headers(context(local_data_api,1002))
    session,revision=conversation(native_api_client,auth)
    _,revision=chat(native_api_client,auth,session,revision,QUERY)
    declined,_=chat(native_api_client,auth,session,revision,'no')
    assert declined['answer']==orchestrator.FRAUD_DECLINED and counts(local_data_api)==(0,0)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select state from actions.commands where customer_id=1002 and type='handoff.create'").fetchall()==[('rejected',)]
