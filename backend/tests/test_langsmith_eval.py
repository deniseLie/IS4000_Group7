"""The LangSmith runner's pieces without a model or network: SSE parsing, node tracking and the deterministic checks
on sample outputs (allowed: validators on sample data). The live run is python -m evaluation.langsmith_run."""
import asyncio
from types import SimpleNamespace
from typing import TypedDict
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END,START,StateGraph
from langgraph.types import interrupt
from evaluation import evaluators,langsmith_run,langsmith_sync,targets

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

def test_node_log_marks_acting_idle_and_paused_nodes():
    class S(TypedDict):
        x: int
        y: list[int]
    async def changes(s):return {'x':1}
    async def same(s):return {'x':s['x'],'y':[]}
    async def pauses(s):interrupt({'q':1});return {}
    graph=StateGraph(S)
    for name,fn in (('plan',changes),('propose_actions',same),('review_actions',pauses)):graph.add_node(name,fn)
    graph.add_edge(START,'plan');graph.add_edge('plan','propose_actions');graph.add_edge('propose_actions','review_actions');graph.add_edge('review_actions',END)
    log=targets.NodeLog()
    async def main():
        token=targets.node_log.set(log)
        try:await graph.compile(checkpointer=InMemorySaver()).ainvoke({'x':0,'y':[]},{'configurable':{'thread_id':'t'}})
        finally:targets.node_log.reset(token)
    asyncio.run(main())
    assert log.ran=={'plan','propose_actions','review_actions'} and log.acted=={'plan','review_actions'}

def test_reply_source_origin_and_route_checks():
    ref={'reply_has':['SGD ?2\\b'],'reply_lacks':['approved'],'cites_any':['savings-fees'],'expected_origin':'ai','expected_route':'guest_balance','expect_needs_human':False}
    good=out(answer='The fee is SGD 2.',replies=[{'say':'q','answer':'The fee is SGD 2.','origin':'ai','needs_human':False,'sources':['savings-fees']}],routes=['guest_balance'])
    assert set(scores(evaluators.checks(good,ref)).values())=={1}
    bad=out(answer='Approved: no fee.',replies=[{'say':'q','answer':'x','origin':'safety','needs_human':True,'sources':['cards'],'error':'assistant_unavailable'}])
    # 'Approved: no fee.' has no SGD 2; reply_lacks patterns are case-sensitive, so 'approved' is not found
    assert scores(evaluators.checks(bad,ref))=={'completed':0,'reply_has':0,'reply_lacks':1,'cites':0,'origin':0,'fixed_route':0,'needs_human':0}
    assert scores(evaluators.checks(out(not_offered=[{'tap':'Yes, report it'}]),{}))=={'completed':1,'offered':0}
    assert evaluators.checks(out(played=False,skipped=[{'upload':'bill'}]),ref)==[{'key':'played','score':None,'comment':'no step can be played in chat: [{"upload": "bill"}]'}]

def test_ticket_checks():
    fraud={'reference':'TCK-001200','category':'suspected_fraud','status':'submitted'}
    assert scores(evaluators.checks(out(tickets={'opened':[fraud],'all':[fraud]}),{'ticket':'suspected_fraud'}))['ticket']==1
    reused=out(answer='Your reference is TCK-001200.',tickets={'opened':[],'all':[fraud]})
    assert scores(evaluators.checks(reused,{'ticket':'suspected_fraud'}))['ticket']==1
    assert scores(evaluators.checks(reused,{'ticket':'rm_handover'}))['ticket']==0
    assert scores(evaluators.checks(out(tickets={'opened':[fraud],'all':[fraud]}),{'ticket':None}))['ticket']==0
    assert scores(evaluators.checks(out(skipped=[{'form':'x'}]),{'ticket':'card_limit_change'}))['ticket'] is None

def test_trajectory_checks():
    t={'nodes':['plan','dispatch','compose'],'idle':['propose_actions'],'agents':{'retail':1},'tools':['my_cards'],'no_tools':['my_t*'],'model':True}
    good=out(nodes={'ran':[],'acted':['plan','prioritise','dispatch','compose']},agents={'retail':1},tools=['my_cards','knowledge_search'],model_calls=2)
    assert set(scores(evaluators.checks(good,{'trajectory':t})).values())=={1}
    bad=out(nodes={'ran':[],'acted':['plan','propose_actions']},agents={'retail':2},tools=['my_transactions'],model_calls=0)
    found={r['key']:r for r in evaluators.checks(bad,{'trajectory':t})}
    assert all(found[k]['score']==0 for k in ('nodes','agents','tools','model'))
    assert found['nodes']['comment']=='did not act: dispatch, compose; should be idle: propose_actions'
    fixed={'nodes':[],'idle':[],'agents':{},'tools':[],'model':False,'graph':False}
    assert set(scores(evaluators.checks(out(),{'trajectory':fixed})).values())=={1}
    assert scores(evaluators.checks(out(nodes={'ran':['plan'],'acted':['plan']},tools=['x']),{'trajectory':fixed}))=={'completed':1,'nodes':0,'agents':1,'tools':0,'model':1}

@pytest.mark.parametrize('name',sorted(langsmith_sync.DATASETS))
def test_every_case_reference_is_checkable(name):
    """Every case file's reference fields fit the evaluators (a renamed field would silently skip a check)."""
    for case in langsmith_sync.load(name)[0]:
        ref={k:case[k] for k in langsmith_sync.EXPECTED if k in case}
        evaluators.checks(out(),ref)

def test_judge_needs_its_own_model(monkeypatch):
    monkeypatch.delenv('JUDGE_MODEL',raising=False)
    with pytest.raises(RuntimeError,match='Set JUDGE_MODEL'):evaluators.judge_model()
    monkeypatch.setenv('JUDGE_MODEL','same');monkeypatch.setenv('LLM_MODEL','same')
    with pytest.raises(RuntimeError,match='must differ'):evaluators.judge_model()
    assert 'Must not' not in evaluators.transcript(out(replies=[{'say':'hi','answer':'hello','sources':['support']}]))

def test_case_status_from_feedback():
    row=lambda error=None,*scores:{'run':SimpleNamespace(error=error),'evaluation_results':{'results':[SimpleNamespace(key=k,score=s) for k,s in scores]}}
    assert langsmith_run.status(row(None,('nodes',1),('judge_rubric',0)))=='pass'
    assert langsmith_run.status(row(None,('nodes',1),('ticket',0)))=='fail'
    assert langsmith_run.status(row(None,('played',None)))=='skipped'
    assert langsmith_run.status(row('Boom',('nodes',1)))=='error'
