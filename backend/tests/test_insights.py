"""Admin dashboard analytics (services/insights.py) on sample run dicts shaped like services/quality.py's reads, and the
/admin/overview, /admin/failures and failure-card routes with those reads replaced (the real reads are opt-in live checks)."""
from datetime import datetime,timedelta,timezone
from itertools import count
from app.services import insights,prompts,quality
from tests.test_gateway import bearer,client,code,env  # noqa: F401  (env is the autouse JWKS/database fixture)

NOW=datetime(2026,11,4,12,tzinfo=timezone.utc)
ADMIN=bearer(app_metadata={'demobank_role':'admin'})
ids=count(1)


def req(hours_ago: float,trace: str | None=None,**kw) -> dict:
    run=f'r{next(ids)}'
    return {'run_id':run,'trace_id':trace or run,'source':'live','name':'chat','started_at':NOW-timedelta(hours=hours_ago),'latency_s':5.0,
        'surface':'personal','status':'success','error':None,'tokens':1000,'route':None,'plan_route':None,'outcome':'ok','origin':'ai',
        'source_check':'basic_checks_passed','needs_human':False,'reject_reason':None,'prompt_version':None,'pipeline':'v2','model_calls':1,
        'agents':None,'agent_status':None,'prompt_ids':None,'model':None,'first_section_ms':None,'app_version':'abc1234','error_type':None,
        'question':'What is my card limit?','answer':'Your limit is SGD 5,000.','sources':[],**kw}


def step(name: str,trace: str,hours_ago: float=1,error: str | None=None,depth: int=1,**meta) -> dict:
    return {'run_id':f's{next(ids)}','trace_id':trace,'parent_id':trace,'name':name,'run_type':'llm' if name=='llm' else 'chain',
        'started_at':NOW-timedelta(hours=hours_ago),'latency_s':1.0,'run_status':'error' if error else 'success','error':error,'tokens':None,
        'meta':meta,'source':'live','depth':depth}


def exp(name: str,dataset: str,days_ago: float,cases: list[tuple],partial=None) -> dict:
    """cases: (case id, status[, failed checks[, prompt ids called]])."""
    at=NOW-timedelta(days=days_ago)
    def case(case_id,state,failed=(),called=()):
        return {'case_id':case_id,'run_id':f'{name}-{case_id}','status':state,'checks':{k:{'score':0,'comment':f'{k} wrong'} for k in failed},
            'failed':list(failed),'judge':None,'latency_s':3.0,'tokens':None,'error':None,'agents':{},'prompt_ids':list(called) or None,'started_at':at,
            'asked':[f'{case_id} question']}
    return {'id':name,'name':name,'dataset':dataset,'started_at':at,'url':None,'model':'haiku','partial':partial,'cases':[case(*c) for c in cases]}


def full(name,days_ago,passing,total=10,**kw):
    return exp(name,'demobank-qa1-client',days_ago,[(f'C{i:02}','pass' if i<passing else 'fail',() if i<passing else ('reply_has',)) for i in range(total)],**kw)


def test_live_tiles_count_failures_held_back_answers_and_handoffs():
    rows=[req(1,latency_s=1),req(2,latency_s=2,origin='validation',source_check='rejected'),req(3,latency_s=3,needs_human=True,model_calls=0),
        req(4,latency_s=10,error="LLMError('provider down')",status='error',surface='rm')]
    k=insights.kpis(rows)
    assert (k['requests'],k['client'],k['rm'],k['failed'])==(4,3,1,1)
    assert (k['success_rate'],k['held_back_rate'],k['handoff_rate'])==(0.75,0.25,0.25)
    assert k['p50_s']==2.5 and k['model_calls_per_request']==0.75
    buckets=insights.series(rows,NOW,1)
    assert len(buckets)==24 and sum(b['requests'] for b in buckets)==4 and sum(b['failed'] for b in buckets)==1


def test_latest_evals_compare_full_runs_and_skip_subsets():
    first,latest,subset=full('e1',3,8),full('e3',2,9),full('e2',1,2,total=2)
    row=insights.latest_evals([first,subset,latest],NOW)[0]
    assert row['experiment']=='e3' and row['pass_rate']==0.9 and row['previous_pass_rate']==0.8
    assert row['last_run']==subset['started_at'] and not row['stale']
    assert insights.subset(full('e4',1,9,partial=False),100) is False  # recorded as a full run, whatever its size
    old=insights.latest_evals([full('e0',10,9)],NOW)[0]
    assert old['stale']


def test_a_case_that_passed_and_now_fails_is_a_regression():
    before=exp('e1','demobank-planner',2,[('P03','pass'),('P04','fail',('agents',))])
    after=exp('e2','demobank-planner',1,[('P03','fail',('agents',)),('P04','fail',('agents',))])
    found=insights.regressions([before,after])
    assert [(r['case_id'],r['previous_experiment'],r['experiment'],r['failed'])  for r in found]==[('P03','e1','e2',['agents'])]
    assert found[0]['comments']=={'agents':'agents wrong'}


def test_failures_name_the_step_and_group_by_signature():
    withheld=req(1,trace='t1',origin='validation',source_check='rejected')
    errors=[req(2,trace='t2',error="LLMError('timeout')",status='error'),req(30,trace='t3',error="LLMError('timeout')",status='error')]
    steps=[step('agent','t1',1,agent='retail',intent='card_limit',status='withheld',path='rules'),step('agent','t1',1)]  # the LangGraph node has no agent meta
    result=insights.failures([withheld,*errors],steps,[],NOW,1,'live')
    kinds={g['kind']:g for g in result['groups']}
    assert set(kinds)=={'agent_withheld','request_error'}  # the agent already reported the withheld answer
    assert kinds['agent_withheld']['title']=='retail withheld its answer' and kinds['agent_withheld']['items'][0]['question']=='What is my card limit?'
    g=kinds['request_error']
    assert (g['title'],g['in_window'],g['count'],g['new'])==('Request failed: LLMError',1,2,False)  # also seen in the window before


def test_eval_cases_show_what_the_case_asked_and_its_history():
    runs=[exp('e1','demobank-graph',3,[('C23','pass')]),exp('e2','demobank-graph',0.5,[('C23','fail',('nodes',))])]
    result=insights.failures([],[],runs,NOW,1,'eval')
    [g]=result['groups']
    assert g['kind']=='eval_case' and g['history']==['pass','fail'] and g['items'][0]['question']=='C23 question'
    assert g['items'][0]['detail']=='nodes: nodes wrong'
    assert insights.failures([],[],runs,NOW,1,'live')['groups']==[]


def test_attention_flags_safety_and_search_problems():
    rows=[req(1,route='lost_card',model_calls=1),req(2),req(3)]
    steps=[step('kb.search','x',1,mode='keyword_only',embedding_error='not_configured')]
    items={i['kind']:i for i in insights.attention(rows,steps,[],[],NOW)}
    assert items['fixed_route_model']['level']=='high' and 'lost_card' in items['fixed_route_model']['detail']
    assert items['keyword_fallback']['level']=='info'
    steps=[step('kb.search','x',1,mode='keyword_only',embedding_error='HTTPError')]
    assert {i['kind']:i for i in insights.attention(rows,steps,[],[],NOW)}['keyword_fallback']['level']=='medium'
    assert 'error_rate' not in items and 'latency' not in items


def test_untested_prompts_are_configured_versions_no_case_called():
    runs=[exp('e1','demobank-graph',1,[('C23','pass',(),['planner/v7+ab12','retail/v10+cd34'])])]
    called=[step('llm','x',1,agent='answer',prompt_version='v8+ef56')]
    assert insights.untested_prompts({'planner':'v7','retail':'v10','answer':'v8','fraud':'v7'},called,runs)==['fraud v7']
    assert insights.untested_prompts({'fraud':'v7'},[],[exp('e0','demobank-graph',1,[('C23','pass')])])==[]  # nothing recorded: unknown


def test_the_failure_card_points_at_the_deepest_failing_step():
    root=req(1,trace='t9',origin='validation',source_check='rejected',reject_reason='unsupported_figure')
    runs=[{**step('chat','t9',depth=0),'run_type':'chain','request':root},step('planner','t9',route='agents',tasks=1),
        step('agent','t9',agent='retail',intent='card_limit',status='withheld',path='open',wave=1,tool_calls=2),
        step('llm','t9',depth=2,agent='retail',prompt_version='v10+cd34',model='haiku',transport='api',outcome='provider_error',retries=2)]
    case={'case_id':'C07','status':'fail','checks':{'reply_has':{'score':0,'comment':'missing /30 Nov/'}}}
    card=insights.card({'root_id':'t9','url':'https://smith.example/t9','case':case,'asked':['When is my bill due?'],
        'expected':{'reply_has':['30 Nov'],'internal':'x'},'runs':runs,'experiment':{'name':'e2','dataset':'demobank-qa1-client','url':None}})
    assert card['failure']['step']=='llm' and card['failure']['outcome']=='provider_error' and card['failure']['retries']==2
    assert [s['name'] for s in card['steps'] if s['origin']]==['llm'] and sum(s['failing'] for s in card['steps'])==2
    assert card['plan']['route']=='agents' and card['plan']['agents'][0]['status']=='withheld'
    assert card['asked']==['When is my bill due?'] and card['saw']['answer']=='Your limit is SGD 5,000.'
    assert card['config']['prompts']==['retail v10+cd34'] and card['config']['app_version']=='abc1234'
    assert card['eval']['expected']=={'reply_has':['30 Nov']} and card['eval']['checks'][0]['key']=='reply_has'
    assert card['eval']['repro']=='python -m evaluation.langsmith_run --dataset demobank-qa1-client --case C07' and card['repro'] is None


def test_dashboard_routes_answer_from_the_reads(monkeypatch):
    rows=[req(1,error="LLMError('down')",status='error'),req(2),req(3)]
    monkeypatch.setattr(quality,'now',lambda:NOW)
    monkeypatch.setattr(quality,'requests',lambda source,days:rows)
    monkeypatch.setattr(quality,'steps',lambda source,days:[])
    monkeypatch.setattr(quality,'experiment_runs',lambda:[full('e1',1,9),exp('e2','demobank-graph',1,[('C23','pass',(),['planner/v7+ab12'])])])
    monkeypatch.setattr(prompts,'in_use',lambda:{'answer':'v8','planner':'v7'})
    body=client.get('/admin/overview?window=24h',headers=ADMIN).json()
    assert body['window_days']==1 and body['live']['now']['failed']==1
    assert {e['dataset']:e['pass_rate'] for e in body['evals']}=={'demobank-graph':1.0,'demobank-qa1-client':0.9}
    attention={i['kind']:i for i in body['attention']}
    assert {'new_failure','error_rate','untested_prompt'}<=set(attention) and attention['untested_prompt']['detail']=='answer v8'
    body=client.get('/admin/failures?window=7d&source=all',headers=ADMIN).json()
    assert {g['kind'] for g in body['groups']}=={'request_error','eval_case'}
    assert client.get('/admin/overview?window=1y',headers=ADMIN).status_code==422
    assert client.get('/admin/failures?source=prod',headers=ADMIN).status_code==422
    assert code(client.get('/admin/overview',headers=bearer(app_metadata={'demobank_role':'rm'})))=='admin_only'
    monkeypatch.setattr(quality,'trace_runs',lambda run_id:(_ for _ in ()).throw(ConnectionError('down')))
    assert code(client.get('/admin/failures/00000000-0000-4000-8000-000000000001',headers=ADMIN))=='admin_unavailable'


def test_agent_failures_come_from_the_request_summary_when_steps_were_not_read():
    row=req(1,trace='t5',source='eval',agent_status={'security':'ok','fraud':'timeout'},origin='validation')
    [g]=insights.failures([row],[],[],NOW,1,'eval')['groups']
    assert (g['kind'],g['agent'],g['items'][0]['detail'])==('agent_timeout','fraud','from the request summary')
    stepped=[step('agent','t5',1,agent='fraud',intent='unrecognised_charge',status='ok')]
    assert [g['kind'] for g in insights.failures([row],stepped,[],NOW,1,'eval')['groups']]==['held_back']  # the steps say the agent was fine


def test_timeline_marks_what_changed_between_full_runs_and_skips_subsets():
    a,b,c=full('e1',3,8),full('e2',2,6,total=2),full('e3',1,9)
    a['prompts'],c['prompts']={'retail':'v10','planner':'v7'},{'retail':'v11','planner':'v7'}
    c['model']='sonnet'
    points=insights.timeline([a,b,c])
    assert [p['subset'] for p in points]==[False,True,False]
    assert points[1]['changes']==[] and points[2]['changes']==['model haiku → sonnet','retail v10 → v11']
    assert insights.changes({'answer_prompt':'v7'},{'answer_prompt':'v8'})==['answer v7 → v8']


def test_check_trends_are_grouped_by_what_they_check():
    graph=[exp('e1','demobank-graph',2,[('C23','fail',('nodes',))]),exp('e2','demobank-graph',1,[('C23','pass')])]
    graph[0]['cases'][0]['checks']['reply_has']={'score':1,'comment':''}
    rows={r['key']:r for r in insights.check_trends(graph,'demobank-graph')}
    assert rows['reply_has']['group']=='answer' and rows['nodes']['group']=='trajectory'
    assert [p['rate'] for p in rows['nodes']['points']]==[0,None] and rows['nodes']['latest']==0
    assert insights.check_group('agents','demobank-planner')=='planner' and insights.check_group('judge_rubric','demobank-graph')=='judge'
    assert insights.check_group('ticket_card_verification','demobank-qa1-client')=='answer'


def test_cases_are_labelled_from_their_history():
    label=insights.case_label
    assert label(['pass','fail'])=='regressed' and label(['fail','pass'])=='fixed' and label(['fail','error','fail'])=='always_failing'
    assert label(['pass','fail','pass','pass'])=='flaky' and label(['pass','pass'])=='passing' and label(['skipped'])=='not_scored'
    assert label(['fail','fail','pass','pass'])=='passing' and label(['pass','fail','fail'])=='failing'
    runs=[exp(f'e{i}','demobank-graph',12-i,[('C23','pass' if i%2 else 'fail'),('C24','pass')]) for i in range(12)]
    heat=insights.heatmap(runs)
    assert len(heat['experiments'])==10 and [r['case_id'] for r in heat['rows']]==['C23','C24']
    assert heat['rows'][0]['label']=='fixed' and heat['labels']=={'fixed':1,'passing':1}


def test_compare_two_runs_case_by_case():
    a=exp('e1','demobank-graph',2,[('C23','pass'),('C24','fail',('nodes',)),('C25','fail',('tools',)),('C26','pass')])
    b=exp('e2','demobank-graph',1,[('C23','fail',('reply_has',)),('C24','pass'),('C25','fail',('tools',)),('C27','pass')])
    result=insights.compare(a,b)
    kinds={r['case_id']:r['kind'] for r in result['rows']}
    assert kinds=={'C23':'regressed','C24':'fixed','C25':'still_failing','C26':'only_a','C27':'only_b'}
    assert result['rows'][0]['case_id']=='C23' and result['rows'][0]['b']['comments']=={'reply_has':'reply_has wrong'}
    assert result['counts']['regressed']==1 and result['counts']['still_passing']==0


def test_prompt_catalogue_joins_versions_logs_live_calls_and_eval_cases():
    runs=[exp('e1','demobank-graph',2,[('C23','pass',(),['retail/v10+aa']),('C24','fail',('nodes',),['retail/v10+aa'])]),
        exp('e2','demobank-graph',1,[('C23','pass',(),['retail/v11+bb'])])]
    live=[step('llm','t1',1,agent='retail',prompt_version='v11+bb',outcome='ok',input_tokens=900,output_tokens=100),
        step('llm','t2',1,agent='retail',prompt_version='v11+bb',outcome='timeout',retries=2)]
    called=[{**step('llm','e2-C23',1,agent='planner',prompt_version='v7+cc'),'source':'eval'}]  # inside the eval trace of case C23 in e2
    families={f['family']:f for f in insights.prompt_catalogue([('retail','v10'),('retail','v11'),('planner','v7')],{'retail':'v11','planner':'v7','fraud':'v7'},
        {('retail','v11'):{'goal':'Shorter answers'}},live,called,runs)}
    retail=families['retail']
    assert [v['version'] for v in retail['versions']]==['v11','v10'] and retail['live']=='v11' and retail['latest_eval']=='v11' and not retail['untested']
    v11,v10=retail['versions']
    assert v11['log']=={'goal':'Shorter answers'} and v11['live_metrics']['calls']==2 and v11['live_metrics']['ok_rate']==0.5
    assert v11['live_metrics']['outcomes']=={'ok':1,'timeout':1} and v11['live_metrics']['retries']==2
    assert v10['eval_metrics']['pass_rate']==0.5 and v10['live_metrics'] is None and v11['eval_metrics']['cases']==1
    assert families['planner']['versions'][0]['eval_metrics']['cases']==1  # from the case's trace, not its recorded prompt ids
    assert families['fraud']['latest_eval'] is None and not families['fraud']['untested']
    runs[1]['cases'][0]['prompt_ids']=['retail/v10+aa']
    assert insights.prompt_catalogue([],{'retail':'v11'},{},[],[],runs)[0]['untested']


def test_evaluation_routes(monkeypatch):
    a=exp('demobank-graph-v2-a','demobank-graph',2,[('C23','pass')]);b=exp('demobank-graph-v2-b','demobank-graph',1,[('C23','fail',('nodes',))])
    other=exp('demobank-planner-v2-c','demobank-planner',1,[('P03','pass')])
    monkeypatch.setattr(quality,'experiment_runs',lambda:[a,b,other])
    monkeypatch.setattr(quality,'datasets',lambda:[{'name':'demobank-graph','id':'d1','examples':8},{'name':'demobank-planner','id':'d2','examples':20}])
    monkeypatch.setattr(quality,'steps',lambda source,days:[])
    graph=client.get('/admin/evaluations',headers=ADMIN).json()['datasets'][0]
    assert graph['dataset']=='demobank-graph' and graph['heatmap']['rows'][0]['label']=='regressed' and len(graph['timeline'])==2
    body=client.get('/admin/evaluations/compare?a=demobank-graph-v2-a&b=demobank-graph-v2-b',headers=ADMIN).json()
    assert body['counts']['regressed']==1
    assert code(client.get('/admin/evaluations/compare?a=demobank-graph-v2-a&b=demobank-planner-v2-c',headers=ADMIN))=='different_datasets'
    assert code(client.get('/admin/evaluations/compare?a=demobank-graph-v2-a&b=demobank-graph-v2-z',headers=ADMIN))=='not_found'
    detail=client.get('/admin/evaluations/demobank-graph-v2-b',headers=ADMIN).json()
    assert detail['cases'][0]['repro']=='python -m evaluation.langsmith_run --dataset demobank-graph --case C23'
    body=client.get('/admin/prompts?window=30d',headers=ADMIN).json()
    assert body['window_days']==30 and any(f['family']=='planner' and f['live'] for f in body['families'])
