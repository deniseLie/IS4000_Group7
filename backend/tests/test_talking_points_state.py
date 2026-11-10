"""Lane 1 v2 (0143): the gate with carry-over, what the model sees, the code checks on each proposed change and the SQL's
apply rules as a pure function. The model call is stubbed at llm_gateway.call as other unit tests do; SQL is in
test_talking_points_state_db."""
import asyncio
import json
from collections import Counter
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4
from app.core import data_api
from app.schemas.chat import ChatRequest,Message
from app.schemas.contracts import Principal,RequestContext
from app.schemas.talking_points_state import TopicState
from app.services import audit,llm_gateway,prompts,talking_points_state as v2
from app.services.guardrail import assess_request

HOME={'topic':'home_loan','stage':'planning','timeframe':'within_12m','kind':None,'concerns':['rates'],'origin':'inferred','on_hold':False}


def state(points=(),active=(),muted=()) -> TopicState:
    return TopicState(chat_topics=True,paused=False,has_rm=True,rm_first_name='Sophie',muted=list(muted),prompted=[],meeting_topics=[],
        points=list(points),active_topics=list(active))


def risk(text: str):
    return assess_request(ChatRequest(messages=[{'role':'user','content':text}],mode='personal'))


def change(action='update',topic='home_loan',quote='an HDB resale flat',stage='planning',timeframe='unknown',kind='none',concerns=()):
    return {'action':action,'topic':topic,'stage':stage,'timeframe':timeframe,'kind':kind,'concerns':list(concerns),'quote':quote}


def test_the_gate_runs_a_follow_up_without_a_topic_word_only_while_a_topic_is_active() -> None:
    text='Is the lock-in usually two years?'
    assert v2.gate(text,risk(text))=='no_topic' and v2.gate(text,risk(text),active=['home_loan']) is None
    for skipped,reason in (('Thanks!','greeting'),('yes','yes_no'),('I lost my card','safety'),('My wife is pregnant, is the lock-in long?','sensitive')):
        assert v2.gate(skipped,risk(skipped),active=['home_loan'])==reason,skipped  # every other skip stays
    assert v2.gate(text,risk(text),offer_reply=True,active=['home_loan'])=='yes_no'


def test_the_model_sees_list_values_and_her_own_messages_only() -> None:
    history=[Message(role='user',content=f'message {n}') if n%2==0 else Message(role='assistant',content='SECRET reply') for n in range(16)]
    history.append(Message(role='user',content='latest'))
    earlier=v2.earlier_messages(history)
    assert earlier==['message 6','message 8','message 10','message 12','message 14']  # her last 5 before the latest, oldest first
    [(role,content)]=v2.model_input(state([HOME],active=['home_loan']),earlier,'latest')
    sent=json.loads(content)
    assert role=='user' and sent=={'current_points':[HOME],'active_topics':['home_loan'],'earlier_messages':earlier,'message':'latest'}
    assert 'SECRET' not in content
    assert v2.SCHEMA['title']=='talking_point_changes' and 'none' in v2.SCHEMA['properties']['changes']['items']['properties']['kind']['enum']
    assert 'v2' in prompts.load('talking_points','v2')['id']


def test_a_carry_over_counts_only_for_an_active_topic_and_her_own_words() -> None:
    message='Is the lock-in usually two years? We like an HDB resale flat'
    kept,dropped=v2.validate([change(quote='Is the lock-in usually two years?',concerns=['lock_in','lock_in','taxes']),
        change(topic='investments',quote='usually two years'),  # not active, no topic word
        change(topic='fixed_deposit',quote='two years',action='add'),
        change(quote='an HDB resale flat'),  # a second change for one topic
        change(topic='insurance',quote='lock-in for three years')],  # not her words
        message,state([HOME],active=['home_loan']))
    assert kept==[{'action':'update','topic':'home_loan','stage':'planning','timeframe':'unknown','kind':None,'concerns':['lock_in'],
        'quote':'Is the lock-in usually two years?','carried':False}]  # her message names it (HDB), so it is no carry-over
    assert dropped==Counter({'not_active':2,'repeat':1,'not_her_words':1})
    follow_up='Is the lock-in usually two years?'
    assert v2.validate([change(quote=follow_up)],follow_up,state([HOME],active=['home_loan']))[0][0]['carried'] is True
    assert v2.validate([change(quote=follow_up)],follow_up,state([HOME]))==([],Counter({'not_active':1}))


def test_a_topic_named_in_her_message_counts_when_the_quote_leaves_the_word_out() -> None:
    """Evaluation case TP04: the model quotes the shortest span, which need not hold the topic word."""
    message="We've found an HDB flat and want to apply for the loan now"
    [kept],dropped=v2.validate([change(action='add',quote='want to apply for the loan now',stage='ready',timeframe='now')],message,state())
    assert (kept['action'],kept['stage'],kept['carried'],dropped)==('add','ready',False,Counter())


def test_kinds_points_and_confirmed_topics_are_checked_by_code() -> None:
    message="We're buying an HDB resale flat and want to start investing in ETFs, maybe insurance too. I want to place a fixed deposit at $50,000"
    shared={**HOME,'topic':'insurance','origin':'confirmed'}
    kept,dropped=v2.validate([change(kind='etf'),  # a kind of another topic
        change(topic='investments',quote='start investing in ETFs',kind='etf',action='update'),  # no point yet: becomes an add
        change(topic='insurance',quote='insurance'),  # she shared it herself
        change(topic='fixed_deposit',quote='place a fixed deposit at $50,000',action='on_hold'),  # nothing to put on hold
        change(topic='home_loan',quote='an HDB resale flat',kind='hdb_resale',action='add',concerns=['rates','fees','eligibility','timing','risk'])],
        message,state([HOME,shared]))
    assert [(c['action'],c['topic'],c['kind']) for c in kept]==[('add','investments','etf'),('update','home_loan','hdb_resale')]
    assert kept[1]['concerns']==['eligibility','fees','rates','timing']  # at most 4, the first ones she raised
    assert dropped==Counter({'wrong_kind':1,'confirmed':1,'no_point':1})
    assert v2.validate([change(topic='fixed_deposit',quote='a fixed deposit at $50,000',action='add')],message,state())[0][0]['quote']=='a fixed deposit at [amount]'
    assert v2.validate([change(quote='an HDB resale flat')],message,state([HOME],muted=['home_loan']))==([],Counter({'muted':1}))


def test_on_hold_needs_her_words_that_the_plan_is_off() -> None:
    current=state([HOME],active=['home_loan'])
    for text in ("Actually we've decided not to go ahead with it","We've put it off until next year","I've changed my mind about the condo",
            "We're holding off for now","We are not going ahead","Let's postpone the flat"):
        [kept],_=v2.validate([change(action='on_hold',quote=text)],text,current)
        assert kept['action']=='on_hold' and kept['carried']==(not v2.tp.TOPIC_WORDS['home_loan'].search(text)),text
    for text in ("I'm not sure about fixed or floating","Maybe later, what are the rates?","Is it worth it?"):
        assert v2.validate([change(action='on_hold',quote=text)],text,current)==([],Counter({'no_off_words':1})),text


def test_apply_follows_the_sql_rules() -> None:
    points={'home_loan':dict(HOME)}
    points,_=v2.apply(points,{'action':'update','topic':'home_loan','stage':'curious','timeframe':'unknown','kind':'hdb_resale','concerns':['lock_in'],'carried':True},
        active=['home_loan'])
    assert points['home_loan']=={**HOME,'kind':'hdb_resale','concerns':['lock_in','rates']}  # stage only up, unknown never overwrites
    held,_=v2.apply(points,{'action':'on_hold','topic':'home_loan','stage':'ready','timeframe':'now','kind':None,'concerns':[],'carried':False})
    assert held['home_loan']=={**points['home_loan'],'on_hold':True}  # on hold changes nothing else
    back,_=v2.apply(held,{'action':'update','topic':'home_loan','stage':'ready','timeframe':'within_3m','kind':None,'concerns':['fees'],'carried':False})
    assert back['home_loan']=={**HOME,'stage':'ready','timeframe':'within_3m','kind':'hdb_resale','concerns':['fees','lock_in','rates'],'on_hold':False}
    assert v2.apply(back,{'action':'update','topic':'home_loan','stage':'ready','timeframe':'now','kind':None,'concerns':[],'carried':True},active=[])[1]=='not_active'
    assert v2.apply({},{'action':'on_hold','topic':'home_loan','stage':'ready','timeframe':'now','kind':None,'concerns':[],'carried':False})[1]=='no_point'
    shared={'home_loan':{**HOME,'origin':'confirmed'}}
    assert v2.apply(shared,{'action':'update','topic':'home_loan','stage':'ready','timeframe':'now','kind':None,'concerns':[],'carried':False})==(shared,'confirmed')
    assert v2.merge_concerns(['eligibility','fees','rates','timing'],['lock_in','rates'])==['eligibility','fees','lock_in','rates']


def test_extract_applies_each_kept_change_through_the_sql_function(monkeypatch) -> None:
    calls,rpcs=[],[]
    async def call(prompt,system,messages,schema,model=None,retries=None):
        calls.append((prompt['id'],retries,schema['title']))
        return llm_gateway.LLMResult(data={'changes':[change(quote='Is the lock-in usually two years?',concerns=['lock_in']),
            change(topic='investments',quote='two years')]},model='synthetic',prompt_id='talking_points/v2',input_tokens=1,output_tokens=1,latency_ms=1)
    async def rpc(current,name,params):
        rpcs.append((name,params))
        return {'recorded':True}
    monkeypatch.setattr(llm_gateway,'call',call)
    monkeypatch.setattr(v2.tp,'_rpc',rpc)
    ctx=RequestContext(session_id=uuid4(),case_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token'))
    turn=uuid4()
    counts=asyncio.run(v2.extract(ctx,turn,'Is the lock-in usually two years?',['I want a home loan'],state([HOME],active=['home_loan'])))
    assert calls==[(prompts.load('talking_points','v2')['id'],0,'talking_point_changes')]
    assert rpcs==[('customer_apply_talking_point',{'p_turn_id':str(turn),'p_action':'update','p_topic':'home_loan','p_stage':'planning',
        'p_timeframe':'unknown','p_kind':None,'p_concerns':['lock_in'],'p_quote':'Is the lock-in usually two years?','p_carried':True})]
    assert counts=={'proposed':2,'kept':1,'dropped':{'not_active':1},'outcomes':{'recorded':1},'actions':{'update':1},'carried':1}


def test_v2_is_the_lane_one_default() -> None:
    assert prompts.VERSIONS['talking_points']=='v2' and v2.version()=='v2'  # the evaluation gate passed (prompt log v2)


def test_after_reply_on_v2_reads_the_state_and_audits_counts_only(monkeypatch) -> None:
    seen=SimpleNamespace(sent=[],rpcs=[],events=[],state=state([HOME],active=['home_loan']))
    async def call(prompt,system,messages,schema,model=None,retries=None):
        seen.sent.append(json.loads(messages[0][1]))
        return llm_gateway.LLMResult(data={'changes':[change(quote='Is the lock-in usually two years?',concerns=['lock_in'])]},model='synthetic',
            prompt_id=prompt['id'],input_tokens=1,output_tokens=1,latency_ms=1)
    async def read(current,turn_id):return seen.state
    async def rpc(current,name,params):
        seen.rpcs.append((name,params))
        return {'recorded':True}
    async def flush(self):seen.events.extend(self.events)
    monkeypatch.setattr(llm_gateway,'call',call)
    monkeypatch.setattr(v2,'state',read)
    monkeypatch.setattr(v2.tp,'_rpc',rpc)
    monkeypatch.setattr(data_api,'connect',lambda current:nullcontext(None))
    monkeypatch.setattr(audit.AuditBatch,'flush',flush)
    history=[Message(role='user',content='We want a home loan for an HDB resale flat'),Message(role='assistant',content='SECRET reply'),
        Message(role='user',content='Is the lock-in usually two years?')]
    ctx=RequestContext(session_id=uuid4(),case_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token'))
    turn=uuid4()
    asyncio.run(v2.tp.after_reply(ctx,turn,history,assess_request(ChatRequest(messages=history,mode='personal'))))
    assert seen.sent==[{'current_points':[HOME],'active_topics':['home_loan'],'earlier_messages':['We want a home loan for an HDB resale flat'],
        'message':'Is the lock-in usually two years?'}]
    assert [name for name,_ in seen.rpcs]==['customer_apply_talking_point'] and seen.rpcs[0][1]['p_carried'] is True
    agent=[(e.action,e.details) for e in seen.events if e.category=='agent']
    assert agent==[('agent.talking_points_extracted',{'turn_id':str(turn),'proposed':1,'kept':1,'dropped':{},'outcomes':{'recorded':1},
        'actions':{'update':1},'carried':1,'version':'v2'})]
    seen.state,seen.events=state([HOME]),[]  # nothing active in this chat: a follow-up without a topic word is skipped
    asyncio.run(v2.tp.after_reply(ctx,turn,history,assess_request(ChatRequest(messages=history,mode='personal'))))
    assert len(seen.sent)==1 and [(e.action,e.details) for e in seen.events if e.category=='agent']==[
        ('agent.talking_points_skipped',{'reason':'no_topic','turn_id':str(turn),'version':'v2'})]


def test_on_v2_no_grant_or_a_pause_skips_and_a_model_failure_is_logged_with_ids_only(monkeypatch) -> None:
    seen=SimpleNamespace(models=[],events=[],state=state(),reply=None)
    async def call(prompt,system,messages,schema,model=None,retries=None):
        seen.models.append((model,retries))
        raise seen.reply
    async def read(current,turn_id):return seen.state
    async def rpc(current,name,params):raise AssertionError('nothing is applied')
    async def flush(self):seen.events.extend(self.events)
    monkeypatch.setattr(llm_gateway,'call',call)
    monkeypatch.setattr(v2,'state',read)
    monkeypatch.setattr(v2.tp,'_rpc',rpc)
    monkeypatch.setattr(data_api,'connect',lambda current:nullcontext(None))
    monkeypatch.setattr(audit.AuditBatch,'flush',flush)
    monkeypatch.setattr(v2.settings,'talking_points_model','synthetic/small-model')
    text='We want a home loan for an HDB resale flat'
    history=[Message(role='user',content=text)]
    ctx=RequestContext(session_id=uuid4(),case_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token'))
    run=lambda:asyncio.run(v2.tp.after_reply(ctx,uuid4(),history,assess_request(ChatRequest(messages=history,mode='personal'))))
    agent=lambda:[(e.action,e.outcome,e.details.get('reason')) for e in seen.events if e.category=='agent']
    seen.state=state().model_copy(update={'chat_topics':False});run()
    seen.state=state().model_copy(update={'paused':True});run()
    assert seen.models==[] and agent()==[('agent.talking_points_skipped','success','no_consent'),('agent.talking_points_skipped','success','paused')]
    seen.state,seen.reply=state(),llm_gateway.LLMTimeout('slow');run()
    seen.reply=RuntimeError(text);run()  # any other failure: the reply is already sent, nothing raises
    assert seen.models==[('synthetic/small-model',0)]*2  # its own model setting, never retried
    assert agent()[2:]==[('agent.talking_points_extracted','failed','timeout'),('agent.talking_points_extracted','failed','RuntimeError')]
    assert text not in json.dumps([e.details for e in seen.events])
