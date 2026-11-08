"""Consent changes in the chat (RM copilot build plan 6e): agents/consent_answers offers consent.grant with a scope and an
end, consent.revoke and insight.delete from her records by rule (no model), and a meeting request carries optional grants
that stay off unless she switches them on (offers.optional, migration 0114). Synthetic records only; SQL is in
test_consent_v2.py."""
import asyncio
from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.schemas.chat import ChatRequest
from app.schemas.consent_sharing import ConsentSharing
from app.schemas.contracts import AgentOutput,AgentResult,OfferProposal,PlanTask
from app.schemas.customer_data import CustomerEnvelope
from app.schemas.talking_points import MyTalkingPoints
from app.services import customer_data,offers,orchestrator,talking_points
from app.services.agents import consent_answers,runner
from app.services.guardrail import assess_request
from tests.test_orchestrator_graph import context

SOPHIE='00000000-0000-4000-8000-100200000002'
GRANT,POINT,MEETING=uuid4(),uuid4(),uuid4()
TODAY=date(2026,11,5)
NOW=datetime(2026,11,5,2,tzinfo=timezone.utc)


def sharing(active: tuple[str,...]=('transaction_insights',),rm: bool=True,meeting: bool=True) -> CustomerEnvelope[ConsentSharing]:
    def card(code: str) -> dict:
        grant={'grant_id':str(GRANT),'legacy':False,'scope':None,'until':'date','basis':'consent','granted_at':NOW.isoformat(),
            'expires_at':(NOW+timedelta(days=30)).isoformat(),'until_meeting_id':None,'until_request_id':None,'renewal_due':False}
        return {'code':code,'label':code.replace('_',' '),'description':'What is shared.','gated':True,'default_on':False,'withdrawable':True,'max_months':12,'notice_version':'v1',
            'grant':grant if code in active else None}
    data={'rm':{'advisor_id':SOPHIE,'name':'Sophie Lim (DEMO)'} if rm else None,'categories':[card('transaction_insights'),card('chat_topics')],
        'accounts':[],'next_meeting':{'meeting_id':str(MEETING),'starts_at':(NOW+timedelta(days=3)).isoformat()} if meeting else None,
        'topic_areas':['home_property','investments'],'today':TODAY.isoformat()}
    return CustomerEnvelope[ConsentSharing].model_validate({'customer_id':1002,'call_id':str(uuid4()),'as_of':NOW.isoformat(),'data':data})


def topics(points: tuple[str,...]=('home_loan',)) -> CustomerEnvelope[MyTalkingPoints]:
    rows=[{'point_id':str(POINT),'topic':t,'stage':'planning','timeframe':'within_12m','origin':'inferred','first_seen':TODAY.isoformat(),'last_seen':TODAY.isoformat(),'pending':False,
        'visible_from':NOW.isoformat()} for t in points]
    return CustomerEnvelope[MyTalkingPoints].model_validate({'customer_id':1002,'call_id':str(uuid4()),'as_of':NOW.isoformat(),
        'data':{'points':rows,'muted':[],'pause':None,'chat_topics':True,'rm_first_name':'Sophie'}})


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch):
    found={'sharing':sharing(),'topics':topics()}
    async def read(ctx):return found['sharing']
    async def mine(ctx):return found['topics']
    monkeypatch.setattr(customer_data,'get_consent_sharing_record',read)
    monkeypatch.setattr(talking_points,'mine',mine)
    return found


def ask(text: str):
    return asyncio.run(consent_answers.respond('t1',text,context().request))


def test_stopping_offers_the_revoke_of_her_grant_with_what_changes_at_once(records):
    found=ask('Please stop sharing my transactions with Sophie')
    [offer]=found.proposals
    assert (offer.type,offer.target,offer.agent)==('consent.revoke',{'grant_id':str(GRANT)},'relationship')
    assert 'at once' in found.result.text and found.result.citations[0].source_id=='consent-sharing' and found.evidence[0].id=='consent-sharing'
    assert str(GRANT) in found.evidence[0].text and 'Sophie Lim' not in found.evidence[0].text  # ids and codes, no names
    found=ask('I no longer want to share my chat topics')
    assert found.proposals==[] and found.result.text==consent_answers.NOT_SHARED.format(label='chat insights',rm='Sophie Lim')


def test_sharing_offers_a_grant_with_a_scope_and_an_end_she_named_or_three_months(records):
    found=ask('Share my chat topics about my home and investments with Sophie for 6 weeks')
    [offer]=found.proposals
    assert offer.type=='consent.grant' and offer.target=={'category':'chat_topics','recipient':SOPHIE,'scope':{'topic_areas':['home_property','investments']},
        'notice_version':'ct-2026-11','until':'date','expires_on':(TODAY+timedelta(days=42)).isoformat()}
    assert offer.summary=='Share your chat insights with Sophie Lim (home property, investments) until 17 Dec 2026'
    assert not offers.optional(offer.type,offer.target)
    found=ask('Let my RM see my chat topics until my next meeting')
    assert found.proposals[0].target['until']=='meeting' and found.proposals[0].target['meeting_id']==str(MEETING)
    assert found.proposals[0].target['scope']=={'topic_areas':list(consent_answers.AREA_WORDS)}
    records['sharing']=sharing(active=())
    found=ask('Please share my transactions with Sophie for 5 years')
    assert found.proposals[0].target['scope']=={'accounts':'all','lookback_months':6}
    assert found.proposals[0].target['expires_on']==(TODAY+timedelta(days=365)).isoformat()  # at most 12 months
    assert ask('Share my transactions with Sophie').proposals[0].target['expires_on']==(TODAY+timedelta(days=90)).isoformat()


def test_already_shared_no_rm_and_what_is_not_a_consent_change(records):
    assert ask('Share my transactions with Sophie').result.text.startswith('You already share transaction insights with Sophie Lim')
    for text in ('Do you share my transactions with Sophie?','Can I share my transactions with my accountant?',
            'Book a meeting with Sophie and share my chat topics','Show my transactions from last month',
            'Cancel the payment I can see in my transactions','Delete my account','How much did I spend on transactions abroad?'):
        assert ask(text) is None,text
    records['sharing']=sharing(rm=False)
    found=ask('Stop sharing my transactions')
    assert found.result.text==consent_answers.NO_RM and found.proposals==[]


def test_deleting_a_topic_offers_insight_delete_for_the_point_she_has(records):
    found=ask('Please delete the home loan topic')
    [offer]=found.proposals
    assert (offer.type,offer.target)==('insight.delete',{'point_id':str(POINT)}) and '7 days' in found.result.text
    found=ask('Remove the insurance topic Sophie sees')
    assert found.proposals==[] and found.result.text==consent_answers.NO_TOPIC.format(rm='Sophie',topic='insurance')


def test_stopping_meeting_recording_points_to_withdraw_on_the_meeting_and_offers_nothing(records):
    """0147: a meeting's transcript is her choice on that meeting (I agree / Withdraw, 0122), at once; no request to a team."""
    for text in ('Please stop recording my meetings','I don’t want my meetings with Sophie recorded','Do not record my calls',
            'I want to opt out of meeting recording','Withdraw my consent for recording my meetings'):
        found=ask(text)
        assert found.proposals==[] and found.result.text==consent_answers.RECORDING.format(rm='Sophie Lim'),text
        assert 'Withdraw' in found.result.text and 'at once' in found.result.text and 'team' not in found.result.text
        assert found.evidence[0].id=='consent-sharing' and 'Sophie Lim' not in found.evidence[0].text
    records['sharing']=sharing(rm=False)
    found=ask('Stop recording my meetings')
    assert found.proposals==[] and found.result.text==consent_answers.RECORDING.format(rm='your relationship manager')
    for text in ('Do you record my meetings?','Please record my next meeting','Don’t forget to record the meeting',
            'I don’t need a record of that','Stop sharing my records'):
        found=ask(text)
        assert found is None or 'Withdraw' not in found.result.text,text


def test_the_relationship_agent_cannot_raise_a_request_and_recording_wording_is_planned_by_rule():
    from app.services.agents import registry
    from app.services.authorization import decide
    ctx=context().request.model_copy(update={'agent':'relationship'})
    assert decide(ctx.principal,'ticket.create',{'customer_id':ctx.principal.customer_id},ctx).allow is False
    assert 'ticket.create' not in registry.CHAT_SPECS['relationship'].offers
    req=ChatRequest(messages=[{'role':'user','content':'Please stop recording my meetings with Sophie'}],mode='personal')
    risk=assess_request(req)
    assert [(t.agent,t.intent) for t in orchestrator.overridden(orchestrator.rule_plan(req,risk,req.messages),risk)]==[('relationship','consent')]


def test_a_meeting_request_carries_optional_grants_for_what_she_does_not_share_yet(records):
    found=asyncio.run(consent_answers.meeting_shares('t1',context().request))
    assert [(p.type,p.chained,p.task_id,p.target['category'],p.target['until']) for p in found]==[('consent.grant',True,'t1','chat_topics','request')]
    assert all(offers.optional(p.type,p.target) for p in found) and 'until this request is answered' in found[0].summary
    records['sharing']=sharing(rm=False)
    assert asyncio.run(consent_answers.meeting_shares('t1',context().request))==[]


def test_compose_marks_the_switches_and_a_typed_yes_sends_the_request_only(monkeypatch: pytest.MonkeyPatch):
    meeting=OfferProposal(task_id='t1',agent='relationship',type='meeting.request',target={'advisor_id':SOPHIE},summary='Send a meeting request to Sophie')
    switch=OfferProposal(task_id='t1',agent='relationship',type='consent.grant',chained=True,summary='Also share your chat insights with Sophie until this request is answered',
        target={'category':'chat_topics','recipient':SOPHIE,'scope':{'topic_areas':['investments']},'notice_version':'ct-2026-11','until':'request'})
    async def run(spec,task,*a,**kw):
        return AgentOutput(task_id=task.id,agent=spec.name,status='ok',origin='safety',proposals=[meeting,switch],
            result=AgentResult(task_id=task.id,agent=spec.name,status='ok',answer_kind='conversation',text='Here is your meeting request.'))
    async def plan(history):return [PlanTask(id='t1',agent='relationship',intent='meeting_request',question='A meeting about investments')]
    async def save(proposals,ctx,turn):
        return [SimpleNamespace(offer_id=uuid4(),type=p.type,summary=p.summary,expires_at=datetime.now(timezone.utc)+timedelta(minutes=30)) for p in proposals]
    monkeypatch.setattr(runner,'run_agent',run);monkeypatch.setattr(orchestrator,'model_plan',plan);monkeypatch.setattr(offers,'save',save)
    req=ChatRequest(messages=[{'role':'user','content':'A meeting about investments please'}],mode='personal')
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context(conversation=False)))
    assert [(o.type,o.optional) for o in answer.offer.offers]==[('meeting.request',False),('consent.grant',True)]
    assert 'Would you like me to: Send a meeting request to Sophie?' in answer.answer and 'stay off unless you switch them on' in answer.answer
    assert 'covers every offer' not in answer.answer  # one required offer
    seen=[]
    async def confirm(group,ctx,include=None):
        seen.append(include);return []
    async def open_groups(ctx,conversation):return {uuid4():[]}
    async def unchained(*a):return set()
    monkeypatch.setattr(offers,'confirm',confirm);monkeypatch.setattr(offers,'open_groups',open_groups);monkeypatch.setattr(offers,'chained',unchained)
    req=ChatRequest(messages=[{'role':'user','content':'meeting'},{'role':'assistant','content':'x '+orchestrator.CONFIRM_LINE},{'role':'user','content':'yes'}],mode='personal')
    asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    on=uuid4()
    req=ChatRequest(messages=[{'role':'user','content':'Yes, confirm'}],mode='personal',offer={'offer_group':str(uuid4()),'decision':'confirm','include':[str(on)]})
    asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    assert seen==[[],[on]]  # a typed yes switches nothing on


def test_consent_wording_is_planned_by_rule_for_relationship_and_questions_are_not():
    for text in ('Stop sharing my transactions with Sophie','Please delete the home loan topic','Let my RM see my transactions until my next meeting'):
        req=ChatRequest(messages=[{'role':'user','content':text}],mode='personal')
        risk=assess_request(req)
        assert [(t.agent,t.intent) for t in orchestrator.overridden(orchestrator.rule_plan(req,risk,req.messages),risk)]==[('relationship','consent')],text
    req=ChatRequest(messages=[{'role':'user','content':'What does Sophie see about my transactions?'}],mode='personal')
    assert orchestrator.fixed_task(req.messages[-1].content,req) is None
    req=ChatRequest(messages=[{'role':'user','content':'Stop sharing my transactions with Sophie'}])  # public chat: no records
    assert orchestrator.fixed_task(req.messages[-1].content,req) is None


def test_the_data_sharing_reads_need_her_session_and_read_through_customer_data(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client=TestClient(app)
    for path in ('/consent/sharing','/consent/asks','/consent/access-log'):assert client.get(path).status_code==401,path
    seen=[]
    async def read(tool,rpc,params,adapter,ctx):
        seen.append((tool,rpc,params));raise customer_data.CustomerDataIntegrityError('stop here')
    monkeypatch.setattr(customer_data,'_read',read)
    for call in (customer_data.get_consent_sharing,customer_data.get_consent_asks,customer_data.get_consent_access_log):
        with pytest.raises(customer_data.CustomerDataIntegrityError):asyncio.run(call(context().request))
    assert seen==[('consent.sharing','customer_read_consent_sharing',{}),('consent.asks','customer_read_consent_asks',{}),
        ('consent.access_log','my_consent_access_log',{'p_limit':50})]


def test_a_switch_left_off_has_no_line_and_is_not_counted_as_failed(monkeypatch: pytest.MonkeyPatch):
    from contextlib import contextmanager
    def outcome(state,type_,until=None):
        target=SimpleNamespace(model_dump=lambda mode='json':{'until':until} if until else {})
        return SimpleNamespace(command=SimpleNamespace(command_id=uuid4(),state=state,type=type_,target=target),
            receipt=SimpleNamespace(outcome='succeeded',result=None) if state=='succeeded' else None)
    async def confirm(group,ctx,include=None):return [outcome('succeeded','meeting.request'),outcome('rejected','consent.grant','request')]
    async def unchained(*a):return set()
    spans=[]
    @contextmanager
    def span(name,kind,**attrs):
        spans.append((name,attrs));yield attrs
    monkeypatch.setattr(offers,'confirm',confirm);monkeypatch.setattr(offers,'chained',unchained);monkeypatch.setattr(orchestrator,'span',span)
    req=ChatRequest(messages=[{'role':'user','content':'Yes, confirm'}],mode='personal',offer={'offer_group':str(uuid4()),'decision':'confirm'})
    answer=asyncio.run(orchestrator.answer(req,assess_request(req),context()))
    assert answer.answer==orchestrator.DONE and orchestrator.CANCELLED not in answer.answer
    [act]=[attrs for name,attrs in spans if name=='act']
    assert (act['offers'],act['failed'],act['switches_on'])==(2,0,0)
