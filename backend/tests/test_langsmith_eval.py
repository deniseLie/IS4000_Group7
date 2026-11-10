"""The LangSmith runner's pieces without a model or network: SSE parsing, node tracking and the deterministic checks
on sample outputs (allowed: validators on sample data). The live run is python -m evaluation.langsmith_run."""
import asyncio
from types import SimpleNamespace
from typing import TypedDict
import pytest
from langgraph.graph import END,START,StateGraph
from evaluation import evaluators,langsmith_run,langsmith_sync,targets

def test_requested_cases_cannot_be_silently_omitted():
    langsmith_run.require_requested_cases(['C07','C20'],{'C07','C20'})
    langsmith_run.require_requested_cases(None,{'C07'})
    with pytest.raises(ValueError,match='C20'):
        langsmith_run.require_requested_cases(['C07','C20'],{'C07'})

def out(**kw):
    base={'answer':'','replies':[{'say':'q','answer':'','origin':'ai','needs_human':False,'sources':[]}],'routes':[None],'skipped':[],'played':True,
        'nodes':{'ran':[],'acted':[]},'agents':{},'tools':[],'model_calls':0,'tickets':{'opened':[],'all':[]}}
    return base|kw

def scores(found):return {r['key']:r['score'] for r in found}

def test_sse_and_turns():
    body='event: conversation\r\ndata: {"revision": 2}\r\n\r\nevent: answer\r\ndata: {"a": 1}\r\n\r\n: ping\r\n\r\n'
    assert targets.sse(body)==[('conversation','{"revision": 2}'),('answer','{"a": 1}')]
    assert targets.source_id('kb:savings-fees@v2#monthly')=='savings-fees' and targets.source_id('action-receipt-t1')=='action-receipt-t1'
    assert targets.turns_of({'question':'now?','history':[{'role':'user','content':'before'},{'role':'assistant','content':'x'}]})==[{'say':'before'},{'say':'now?'}]

def test_node_log_marks_acting_and_idle_nodes():
    class S(TypedDict):
        x: int
        y: list[int]
    async def changes(s):return {'x':1}
    async def same(s):return {'x':s['x'],'y':[]}
    async def nothing(s):return {}
    graph=StateGraph(S)
    for name,fn in (('planner',changes),('agent',same),('gather',nothing)):graph.add_node(name,fn)
    graph.add_edge(START,'planner');graph.add_edge('planner','agent');graph.add_edge('agent','gather');graph.add_edge('gather',END)
    log=targets.NodeLog()
    async def main():
        token=targets.node_log.set(log)
        try:await graph.compile().ainvoke({'x':0,'y':[]})
        finally:targets.node_log.reset(token)
    asyncio.run(main())
    assert log.ran=={'planner','agent','gather'} and log.acted=={'planner'}

def test_reply_source_origin_and_route_checks():
    ref={'reply_has':['SGD ?2\\b'],'reply_lacks':['approved'],'cites_any':['savings-fees'],'expected_origin':'ai','expected_route':'guest_balance','expect_needs_human':False}
    good=out(answer='The fee is SGD 2.',replies=[{'say':'q','answer':'The fee is SGD 2.','origin':'ai','needs_human':False,'sources':['savings-fees']}],routes=['guest_balance'])
    assert set(scores(evaluators.checks(good,ref)).values())=={1}
    bad=out(answer='Approved: no fee.',replies=[{'say':'q','answer':'x','origin':'safety','needs_human':True,'sources':['cards'],'error':'assistant_unavailable'}])
    # 'Approved: no fee.' has no SGD 2; reply_lacks patterns are case-sensitive, so 'approved' is not found
    assert scores(evaluators.checks(bad,ref))=={'completed':0,'reply_has':0,'reply_lacks':1,'cites':0,'origin':0,'fixed_route':0,'needs_human':0}
    assert scores(evaluators.checks(out(not_offered=[{'tap':'Yes, report it'}]),{}))=={'completed':1,'offered':0}
    assert evaluators.checks(out(played=False,skipped=[{'upload':'bill'}]),ref)==[{'key':'played','score':None,'comment':'no step can be played in chat: [{"upload": "bill"}]'}]

def test_a_tap_after_a_skipped_form_is_unplayed_and_an_offer_can_be_expected():
    # C04: Confirm belongs to the request form the chat cannot play, so it is unplayed (None), not a failed offer
    after_form=out(skipped=[{'form':'card_limit_change'},{'tap':'Confirm','after_skipped':True}])
    assert scores(evaluators.checks(after_form,{}))=={'completed':1,'offered':None}
    # C11b: her open urgent request is quoted, so no Confirm is offered
    quoted=out(replies=[{'say':'q','answer':'TCK-001081','origin':'safety','needs_human':True,'sources':[],'offered':False}])
    assert scores(evaluators.checks(quoted,{'offer':False}))['offer']==1 and scores(evaluators.checks(quoted,{'offer':True}))['offer']==0


def test_the_after_reply_talking_points_call_is_not_the_turns_model_call():
    spans=[('agent',{'agent':'relationship'}),('llm',{'agent':'relationship'}),('llm',{'agent':'talking_points'})]
    assert targets.answer_calls(spans)==1 and targets.answer_calls(spans[2:])==0
    # C06: a fixed reply runs no tools; lane 1 still reads her topic state after it, which is not the turn's read
    reads=[('tool_call',{'tool':'my_cards','agent':'accounts'}),('tool_call',{'tool':'my_topic_state','agent':'talking_points'})]
    assert targets.turn_tools(reads)==['my_cards'] and targets.turn_tools(reads[1:])==[]


def test_ticket_checks():
    fraud={'reference':'TCK-001200','category':'suspected_fraud','status':'submitted'}
    assert scores(evaluators.checks(out(tickets={'opened':[fraud],'all':[fraud]}),{'ticket':'suspected_fraud'}))['ticket']==1
    reused=out(answer='Your reference is TCK-001200.',tickets={'opened':[],'all':[fraud]})
    assert scores(evaluators.checks(reused,{'ticket':'suspected_fraud'}))['ticket']==1
    assert scores(evaluators.checks(reused,{'ticket':'rm_handover'}))['ticket']==0
    assert scores(evaluators.checks(out(tickets={'opened':[fraud],'all':[fraud]}),{'ticket':None}))['ticket']==0
    assert scores(evaluators.checks(out(skipped=[{'form':'x'}]),{'ticket':'card_limit_change'}))['ticket'] is None
    assert scores(evaluators.checks(reused,{'ticket_categories':['suspected_fraud','rm_handover']}))=={'completed':1,'ticket_suspected_fraud':1,'ticket_rm_handover':0}

def test_trajectory_checks():
    t={'nodes':['planner','agent','compose'],'idle':['act'],'agents':{'accounts':1},'tools':['my_cards'],'no_tools':['my_t*'],'model':True}
    good=out(nodes={'ran':[],'acted':['planner','agent','compose']},agents={'accounts':1},tools=['my_cards','knowledge_search'],model_calls=2)
    assert set(scores(evaluators.checks(good,{'trajectory':t})).values())=={1}
    bad=out(nodes={'ran':[],'acted':['planner','act']},agents={'accounts':2},tools=['my_transactions'],model_calls=0)
    found={r['key']:r for r in evaluators.checks(bad,{'trajectory':t})}
    assert all(found[k]['score']==0 for k in ('nodes','agents','tools','model'))
    assert found['nodes']['comment']=='did not act: agent, compose; should be idle: act'
    fixed={'nodes':[],'idle':[],'agents':{},'tools':[],'model':False,'graph':False}
    assert set(scores(evaluators.checks(out(),{'trajectory':fixed})).values())=={1}
    assert scores(evaluators.checks(out(nodes={'ran':['planner'],'acted':['planner']},tools=['x']),{'trajectory':fixed}))=={'completed':1,'nodes':0,'agents':1,'tools':0,'model':1}
    rm={'pipeline':'rm','nodes':[],'idle':[],'agents':{},'tools':['rm_book'],'model':True}
    assert set(scores(evaluators.checks(out(tools=['rm_book'],model_calls=1),{'trajectory':rm})).values())=={1}


def test_limit_request_sources_require_a_matching_client_and_category():
    rule={'customer_id':1002,'category':'card_limit_change','fallback_source':'cards'}
    relevant={'reference':'TCK-000001','category':'card_limit_change','customer_id':1002}
    unrelated={'reference':'TCK-000002','category':'rm_handover','customer_id':1002}
    other_client={**relevant,'reference':'TCK-000003','customer_id':1003}
    def score(sources,tickets):
        return evaluators.request_sources(rule,out(replies=[{'sources':sources}],tickets={'all':tickets}))['score']
    assert score(['ticket-TCK-000001'],[relevant,unrelated])==1
    assert score(['cards'],[unrelated])==1
    assert score(['cards'],[relevant])==0
    assert score(['cards','ticket-TCK-000002'],[unrelated])==0
    assert score(['ticket-TCK-000003'],[other_client])==0

def test_direct_record_reads_are_distinct_from_mcp_tool_calls():
    ref={'trajectory':{'nodes':[],'idle':[],'agents':{},'tools':[], 'service_reads':['tickets.list']}}
    assert scores(evaluators.checks(out(service_reads=['tickets.list']),ref))['service_reads']==1
    assert scores(evaluators.checks(out(tools=['tickets.list']),ref))['service_reads']==0

def test_handover_confirmation_requires_the_actual_visible_preview():
    from app.services import rm_handover
    question='Can my RM review my annual fee?'
    assert targets.handover_reply(rm_handover.preview(question))==rm_handover.SEND_REPLY
    assert targets.handover_reply(rm_handover.preview(question,existing='TCK-001234'))=='Add to TCK-001234'
    assert targets.handover_reply('You can check TCK-001234 in Requests.') is None


def test_dependency_checks():
    t={'nodes':['planner','agent','gather','compose'],'idle':['act'],'waves':2,'exports':[{'from':'accounts','to':'relationship','name':'due_dates'}],
        'skipped':{},'rerouted':{'from':'accounts','to':'relationship'}}
    deps={'waves':2,'exports':['accounts>relationship:due_dates'],'skipped':{},'rerouted':{'from':'accounts','to':'relationship'}}
    good=out(nodes={'ran':[],'acted':['planner','agent','gather','compose']},dependencies=deps)
    assert scores(evaluators.checks(good,{'trajectory_v5':t}))=={'completed':1,'nodes':1,'waves':1,'exports':1,'skipped':1,'rerouted':1}
    bad=out(nodes={'ran':[],'acted':['planner','agent','compose']},dependencies={'waves':1,'exports':[],'skipped':{'relationship':'condition_false'},'rerouted':{}})
    found={r['key']:r for r in evaluators.checks(bad,{'trajectory_v5':t})}
    assert all(found[k]['score']==0 for k in ('nodes','waves','exports','skipped','rerouted'))
    assert found['exports']['comment']=='not passed: accounts>relationship:due_dates'
    assert 'waves' not in scores(evaluators.checks(out(),{'trajectory_v5':t}))  # an output without dependency facts (v4 runs)

def test_the_consent_fault_reads_the_grant_as_revoked(monkeypatch):
    async def rpc(name,params,ctx,tool):
        shared={'consent_active':True,'shared':[{'content':'note'}]}
        return {'data':shared if name=='rm_shared_summary' else {'eligible':True,'shared':shared} if name=='rm_handover_sources' else {'x':1}}
    monkeypatch.setattr(targets,'_RM_RPC',rpc)
    async def main():
        with targets.inject('consent_revoked') as applied:
            from app.services.rm import records
            assert applied
            assert (await records.rpc('rm_shared_summary',{},None,'t'))['data']['shared']==[]
            assert (await records.rpc('rm_handover_sources',{},None,'t'))['data']['shared']['consent_active'] is False
            assert (await records.rpc('rm_tickets',{},None,'t'))['data']=={'x':1}
    asyncio.run(main())


def test_agent_counts_may_be_a_range():
    assert evaluators.counts_match({'rm_client':1,'rm_requests':3},{'rm_client':1,'rm_requests':'1-5'})
    assert not evaluators.counts_match({'rm_client':1,'rm_requests':6},{'rm_client':1,'rm_requests':'1-5'})
    assert not evaluators.counts_match({'accounts':1,'relationship':1},{'accounts':1})

def test_planner_only_target_runs_the_planner_node_alone(monkeypatch):
    from app.services import orchestrator
    async def no_model(history):raise AssertionError('these cases are planned by rules')
    monkeypatch.setattr(orchestrator,'model_plan',no_model)
    cases={c['id']:c for c in langsmith_sync.load('demobank-planner')[0]}
    examples={e['metadata']['case_id']:e for e in langsmith_sync.examples('demobank-planner',list(cases.values()))}
    status=asyncio.run(targets.plan_only(examples['P09']['inputs']))
    assert status['plan']['tasks'][0]['agent']=='relationship' and status['model_calls']==0
    assert scores(evaluators.checks(status,examples['P09']['outputs']))=={'route':1,'agents':1}
    confirm=asyncio.run(targets.plan_only(examples['P10']['inputs']))
    assert confirm['plan']['route']=='act' and scores(evaluators.checks(confirm,examples['P10']['outputs']))=={'route':1}
    ambiguous=asyncio.run(targets.plan_only(examples['P11']['inputs']))
    assert ambiguous['plan']['route']=='ask_which' and scores(evaluators.checks(ambiguous,examples['P11']['outputs']))=={'route':1}
    # Sophie: the RM planner (rm_planner/v4) without book reads; here the model's rows are a fixed sample.
    from app.services.rm import graph
    async def rows(rt,message):return [{'intent':'reviews','depends_on':0},{'intent':'draft_message','depends_on':1,'for_each':'clients_due'}]
    monkeypatch.setattr(graph,'model_plan',rows)
    rm=asyncio.run(targets.plan_only(examples['P13']['inputs']))
    assert [t['agent'] for t in rm['plan']['tasks']]==['rm_client','rm_requests'] and rm['plan']['unlinked']==0
    assert set(scores(evaluators.checks(rm,examples['P13']['outputs'])).values())=={1}


def test_an_rm_reply_reads_as_the_workspace_shows_it():
    reply={'summary':'S','sections':[{'heading':'H','items':[{'text':'a'}]}],'drafts':[{'client':'Michelle','text':'Hi'}]}
    assert targets.rm_text(reply).splitlines()==['S','H','- a','Draft to Michelle: Hi']
    follow_ups={'summary':'P','sections':[],'documentation':{'draft':{'summary':'','actions':[{'kind':'follow_up_task','title':'Check the review',
        'evidence_quote':'Check the review status.','flags':['No owner stated in the notes']}]}}}
    assert targets.rm_text(follow_ups).splitlines()==['P','Proposed follow up task: Check the review (quotes the note: “Check the review status.”) [No owner stated in the notes]']

def test_plan_checks_score_dependencies():
    ref={'tasks':[{'id':'a','agent':'accounts'},{'agent':'relationship','depends_on':'a','condition':'due_in_window','priority':'P3'}]}
    good={'plan':{'route':'plan','unlinked':0,'tasks':[{'agent':'accounts','depends_on':None,'condition':None,'priority':'P3'},
        {'agent':'relationship','depends_on':'accounts','condition':'due_in_window','priority':'P3'}]}}
    assert set(scores(evaluators.plan_checks(good,ref)).values())=={1}
    loose={'plan':{**good['plan'],'tasks':[{**t,'depends_on':None,'condition':None} for t in good['plan']['tasks']]}}
    found={r['key']:r for r in evaluators.plan_checks(loose,ref)}
    assert found['dependencies']['score']==0 and found['dependencies']['comment']=='missing: relationship after accounts if due_in_window'
    assert scores(evaluators.plan_checks(loose,{**ref,'tasks':[{'agent':'accounts'},{'agent':'relationship'}],'no_dependencies':True}))['dependencies']==1

def test_plan_checks_score_intents_and_entities():
    ref={'tasks':[{'id':'a','agent':'accounts','intent':'savings_after_cards','entities':{'threshold':'5000'}},{'agent':'relationship','depends_on':'a'}]}
    row=lambda **kw:{'agent':'accounts','intent':'savings_after_cards','depends_on':None,'condition':None,'priority':'P3','entities':{'threshold':'5000'}}|kw
    other={'agent':'relationship','intent':'meeting_request','depends_on':'accounts','condition':None,'priority':'P3','entities':{}}
    plan=lambda first:{'plan':{'route':'plan','unlinked':0,'tasks':[first,other]}}
    assert scores(evaluators.plan_checks(plan(row()),ref))=={'route':1,'agents':1,'dependencies':1,'intents':1,'entities':1}
    found={r['key']:r for r in evaluators.plan_checks(plan(row(intent='account_balance',entities={'threshold':'500'})),ref)}
    assert (found['intents']['comment'],found['entities']['comment'])==('missing: accounts savings_after_cards','missing: accounts threshold=5000')

@pytest.mark.parametrize('name',sorted(langsmith_sync.DATASETS))
def test_every_case_reference_is_checkable(name):
    """Every case file's reference fields fit the evaluators (a renamed field would silently skip a check)."""
    for case in langsmith_sync.load(name)[0]:
        ref={k:case[k] for k in langsmith_sync.EXPECTED if k in case}
        evaluators.checks(out(),ref)

def test_judge_needs_its_own_model(monkeypatch):
    monkeypatch.delenv('JUDGE_MODEL',raising=False)
    with pytest.raises(RuntimeError,match='Set JUDGE_MODEL'):evaluators.judge_model()
    monkeypatch.setenv('JUDGE_MODEL','same');monkeypatch.setenv('LLM_MODEL','same');monkeypatch.delenv('CHAT_TRANSPORT',raising=False)
    monkeypatch.setenv('LLM_API_KEY','test-only');monkeypatch.setenv('LLM_BASE_URL','http://127.0.0.1:9')
    with pytest.raises(RuntimeError,match='must differ'):evaluators.judge_model()
    assert 'Must not' not in evaluators.transcript(out(replies=[{'say':'hi','answer':'hello','sources':['support']}]))

def test_codex_judge_compares_the_actual_subscription_model(monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');monkeypatch.setenv('CODEX_MODEL','answer-model')
    monkeypatch.setenv('LLM_MODEL','unused-api-model');monkeypatch.setenv('JUDGE_MODEL','answer-model')
    with pytest.raises(RuntimeError,match='must differ'):evaluators.judge_name()
    monkeypatch.setenv('JUDGE_MODEL','independent-model')
    assert evaluators.judge_name()=='independent-model'

def test_judge_transcript_redacts_questions_and_source_records():
    secret='4111 1111 1111 1111'
    text=evaluators.transcript(out(replies=[{'say':'My card is '+secret,'answer':'Use the official app.',
        'sources':['security'],'evidence':[{'record':{'card':secret}}]}]))
    assert secret not in text and 'Use the official app.' in text

def test_rm_ticket_envelope_and_record_citations():
    from evaluation.rm_target import ticket_rows
    import json
    item={'reference':'TCK-001055','customer_id':1002,'category':'rm_handover','status':'submitted','rm_owned':True,'age_hours':1.5}
    rows=ticket_rows(json.dumps({'data':{'rm_owned':[item],'specialist':[]}}))
    assert rows[0].reference=='TCK-001055'
    with pytest.raises(ValueError):ticket_rows('{"data": []}')
    assert scores(evaluators.checks(out(replies=[{'say':'q','sources':['ticket-TCK-001055']}]),{'source_prefix':'ticket-'}))['record_sources']==1

def test_case_status_from_feedback():
    row=lambda error=None,*scores:{'run':SimpleNamespace(error=error),'evaluation_results':{'results':[SimpleNamespace(key=k,score=s) for k,s in scores]}}
    assert langsmith_run.status(row(None,('nodes',1),('judge_rubric',0)))=='pass'
    assert langsmith_run.status(row(None,('nodes',1),('ticket',0)))=='fail'
    assert langsmith_run.status(row(None,('played',None)))=='skipped'
    assert langsmith_run.status(row('Boom',('nodes',1)))=='error'
