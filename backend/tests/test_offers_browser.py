"""v5 offers in the normal React UI (build plan phase 6): Confirm and Cancel buttons, a typed yes, expiry and two offers in one
reply, over native JWT/RLS data and the real /chat route.

The lost card is a fixed no-model route (one urgent request to her RM since build plan 2b). The two-offer reply starts after
the planner (a lockout is a rule-planned Guide task) with a fixed safety-origin output for the graph mechanics; no model
answer is simulated.

Run from backend/: TEST_BROWSER_LOCAL=1 ../.venv/bin/python -m pytest tests/test_offers_browser.py -q
Needs the native Data API (see test_data_api_local.py) and frontend npm dependencies.
"""
import os
import secrets
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.schemas.contracts import AgentOutput,AgentResult,OfferProposal
from app.services.agents import runner
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to native browser checks')


def two_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(spec,task,inputs,ctx,history=None,language='English'):
        target=lambda reason,priority,summary:{'reason':reason,'priority':priority,'summary':summary}
        return AgentOutput(task_id=task.id,agent=spec.name,status='needs_human',origin='safety',
            result=AgentResult(task_id=task.id,agent=spec.name,status='needs_human',answer_kind='conversation',text='Here is what happens next.'),
            proposals=[OfferProposal(task_id=task.id,agent='relationship',type='handoff.create',summary='Ask the bank to check your card',
                    target=target('card_verification','P0','Card ending 4821: one charge the customer does not recognise.')),
                OfferProposal(task_id=task.id+'b',agent='relationship',type='handoff.create',summary='Ask your relationship manager to follow up',
                    target=target('general_support','P3','The customer asked for a follow-up after a card check.'))])
    monkeypatch.setattr(runner,'run_agent',run)


@pytest.mark.parametrize('case',['button confirm','typed yes','cancel','expired','two in one reply','meeting request'])
def test_offers_ui(local_data_api: NativeAPI,native_api_client: TestClient,monkeypatch: pytest.MonkeyPatch,tmp_path: Path,case: str) -> None:
    import psycopg
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY',secrets.token_urlsafe(32))
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    if case=='two in one reply':two_offers(monkeypatch)
    if case=='meeting request':monkeypatch.setattr(settings,'client_intent_routing','rules')  # 2a: rules route the ask, no planner call
    ctx=context(local_data_api,1001)
    session=NativeBrowserSession(data_api_url=local_data_api.url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id)
    run_browser(native_api_client,session,f'offer {case}',tmp_path,timeout=300)
    with psycopg.connect(local_data_api.database_uri) as db:
        reasons=[row[0] for row in db.execute('select reason from support.handoffs where customer_id=1001 order by priority').fetchall()]
        states=sorted(row[0] for row in db.execute("select state from actions.commands where customer_id=1001 and type='handoff.create'").fetchall())
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
        meetings=[row[0] for row in db.execute('select r.status from rm.meeting_requests r join actions.commands c using(command_id) where c.customer_id=1001').fetchall()]
    expected={'button confirm':(['general_support'],['succeeded']),'typed yes':(['general_support'],['succeeded']),'cancel':([],['rejected']),
        'expired':([],['proposed']),'two in one reply':(['card_verification','general_support'],['succeeded','succeeded']),'meeting request':([],[])}[case]
    assert (reasons,states)==expected and meetings==(['requested'] if case=='meeting request' else [])
