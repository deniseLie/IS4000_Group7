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


def test_attention_times_are_singapore_time():
    late=datetime(2026,11,4,17,30,tzinfo=timezone.utc)  # already 5 Nov in Singapore
    assert insights.sgt(late)=='5 Nov' and insights.sgt(late,time=True)=='5 Nov 01:30 SGT'


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
    assert 'first seen 4 Nov 19:00 SGT;' in attention['new_failure']['detail']  # 11:00 UTC
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


def timed(row: dict,latency: float) -> dict:
    return {**row,'latency_s':latency}


def test_agent_scorecards_and_orchestration_panels():
    rows=[req(1,trace='t1'),req(2,trace='t2',route='lost_card'),req(3,trace='t3',offered=2),req(4,trace='t4',offered=0)]
    steps=[step('agent','t1',agent='accounts',intent='card_limit',path='rule',status='ok',evidence_count=2,exports=1),
        step('agent','t3',agent='accounts',intent='open',path='loop',status='withheld',tool_calls=3,refused=1),
        step('agent','t3',agent='rm_client',intent='client_brief'),
        step('agent','t3'),step('planner','t1'),  # LangGraph node runs of the same names
        step('planner','t1',route='model',tasks=2,dependent=1),step('planner','t3',route='act'),
        step('planner','t4',route='expired'),step('planner','t5',side='rm',route='button',tasks=1,dependent=0),
        step('gather','t1',sends=1,skipped={'relationship':'condition_false'},rerouted={},exports=['accounts>relationship:due_dates']),
        step('act','t3',decision='confirm',offers=2,chained=1,failed=1),
        step('tool_call','t1',tool='my_cards'),step('tool_call','t1',error="ToolError('down')",tool='my_cards'),
        step('tool_call','t3',tool='kb_search'),
        step('kb.search','t3',mode='hybrid',count=0,embed_ms=120,sql_ms=200),
        step('kb.search','t1',mode='keyword_only',count=3,embedding_error='not_configured')]
    client_run=exp('e1','demobank-qa1-client',1,[('C01','pass'),('C02','fail',('reply_has',))])
    client_run['cases'][0]['agents']={'accounts':1}
    planner_run=exp('p1','demobank-planner',1,[('P01','pass'),('P02','fail',('agents',))])
    planner_run['cases'][0]['checks']['agents']={'score':1,'comment':''}
    out=insights.agents(rows,steps,[client_run,planner_run],{'accounts':'accounts','rm_client':'rm_assistant'},{'accounts':'v1','rm_assistant':'v4'})
    cards={c['agent']:c for c in out['agents']}
    assert list(cards)==['accounts','rm_client']
    acc=cards['accounts']
    assert acc['runs']==2 and acc['share']==0.5 and acc['outcomes']=={'ok':1,'withheld':1} and acc['bad_rate']==0.5
    assert acc['paths']=={'rule':1,'loop':1} and acc['tool_calls_per_loop']==3 and acc['refused']==1 and acc['exports']==1
    assert acc['live_prompt']=='v1' and acc['eval_cases']==1 and acc['eval_pass_rate']==1.0
    assert cards['rm_client']['paths']=={'handler':1} and cards['rm_client']['side']=='rm' and cards['rm_client']['live_prompt']=='v4'
    assert out['fixed_routes']=={'requests':1,'share':0.25,'routes':{'lost_card':1}}
    plan=out['planner']
    assert plan['client']['routes']=={'model':1,'act':1,'expired':1} and plan['client']['tasks_per_plan']==2 and plan['client']['multi_agent']==1.0
    assert plan['rm']['routes']=={'button':1} and plan['rm']['multi_agent']==0.0 and plan['accuracy']['pass_rate']==0.5
    assert plan['accuracy']['checks']['agents']==0.5
    assert out['gather']=={'gathers':1,'second_wave':1,'sends':1,'skipped':{'condition_false':1},'rerouted':{},'exports':{'accounts>relationship:due_dates':1}}
    assert out['offers']=={'offered':1,'offers':2,'confirmed':1,'cancelled':0,'expired':1,'ask_which':0,'chained':1,'failed':1}
    assert out['tools'][0]['tool']=='my_cards' and out['tools'][0]['errors']==1 and out['tools'][0]['error_types']=={'ToolError':1}
    know=out['knowledge']
    assert know['searches']==2 and know['hybrid_share']==0.5 and know['empty_rate']==0.5 and know['embedding_errors']=={'not_configured':1}
    assert know['embed_ms_p50']==120 and know['sql_ms_p50']==200
    assert insights.offers_panel([req(1)],[])['offered'] is None  # older requests did not record offers: not zero


def test_latency_percentiles_breakdown_fan_out_and_slowest():
    rows=[timed(req(1,trace='a',surface='public',first_section_ms=1500,agents=['guide']),4),
        timed(req(2,trace='b',agents=['accounts','relationship'],tokens=3000),12),
        timed(req(3,trace='c',surface='rm',error="LLMBudgetExceeded('used up')",status='error',question='Brief me on all my clients'),70)]
    steps=[timed(step('llm','b',agent='accounts',model='haiku',prompt_version='v1+aa',outcome='ok',input_tokens=900,output_tokens=100,retries=1),3),
        timed(step('tool_call','b',tool='my_cards'),2),timed(step('tool_call','b',tool='kb_search'),0.8),
        timed(step('kb.search','b',mode='hybrid',count=2),0.5),step('planner','b'),
        *[step('agent','c',agent=a,intent='client_brief') for a in ('rm_client','rm_requests','rm_requests')],
        step('gather','c',sends=1),node:=step('gather','c'),{**step('gather','c'),'parent_id':node['run_id']}]  # a node run and the span inside it
    out=insights.latency(rows,steps,7)
    assert out['surfaces']['all']['n']==3 and out['surfaces']['rm']['p50_s']==70 and out['surfaces']['public']['first_section_p50_s']==1.5
    assert [h['count'] for h in out['histogram']]==[0,1,0,1,0,0,1] and out['histogram'][-1]['label']=='60 s or more'
    parts={p['part']:p['seconds'] for p in out['breakdown']['parts']}
    assert out['breakdown']['requests']==3 and out['breakdown']['traced']==1 and parts['Model calls']==1.0
    assert abs(parts['Knowledge search']-0.5/3)<1e-3 and abs(parts['Record and other tools']-2.3/3)<1e-3  # the search ran inside its tool call
    assert [f['agents'] for f in out['fan_out']]==['1','2','3+'] and out['second_wave']==1
    assert next(n for n in out['nodes'] if n['name']=='planner')['n']==1 and next(n for n in out['nodes'] if n['name']=='gather')['n']==1
    assert out['models'][0]['name']=='haiku' and out['models'][0]['retries']==1 and out['prompts'][0]['name']=='accounts/v1'
    assert out['tokens']['budget_hits']==1 and out['tokens']['by_family']==[{'family':'accounts','calls':1,'input':900,'output':100}]
    slow=out['slowest'][0]
    assert slow['latency_s']==70 and slow['agents']==['rm_client','rm_requests','rm_requests'] and slow['question']=='Brief me on all my clients'
    assert out['throughput']['peak_requests']==1 and out['throughput']['per_hour']==round(3/168,4)


def test_agent_and_latency_routes(monkeypatch):
    monkeypatch.setattr(quality,'requests',lambda source,days:[req(1,trace='t1')])
    monkeypatch.setattr(quality,'steps',lambda source,days:[step('agent','t1',agent='guide',intent='faq',path='reads',status='ok')])
    monkeypatch.setattr(quality,'experiment_runs',lambda:[])
    body=client.get('/admin/agents?window=7d&source=live',headers=ADMIN).json()
    assert body['agents'][0]['agent']=='guide' and body['agents'][0]['prompt']=='guide' and body['source']=='live' and not body['capped']
    assert client.get('/admin/agents?side=rm',headers=ADMIN).json()['agents']==[] and client.get('/admin/agents?side=staff',headers=ADMIN).status_code==422
    assert client.get('/admin/failures?side=client',headers=ADMIN).json()['side']=='client'
    body=client.get('/admin/latency?window=24h',headers=ADMIN).json()
    assert body['surfaces']['all']['n']==1 and body['window_days']==1 and body['source']=='all'
    assert client.get('/admin/latency?source=prod',headers=ADMIN).status_code==422


def test_agents_and_failures_filter_by_side_and_count_stops_refusals_and_consent_gaps():
    """Build plan 3a: the Agents and Failures pages filter by who asked (client chat or RM workspace); a loop that hit its
    read limit is listed as a failure, while tool refusals and consent gaps are counts (the checks working)."""
    rows=[req(1,trace='c1'),req(1,trace='r1',name='rm_assistant',surface='rm',side='rm')]
    steps=[step('agent','c1',agent='accounts',intent='open',path='loop',status='ok',stop='answered',tool_calls=1,refused=0),
        step('agent','r1',agent='rm_requests',intent='open',path='loop',status='ok',stop='cap',tool_calls=4,limit=4,refused=1),
        step('agent','r1',agent='rm_client',intent='client_overview',path='reads',status='ok'),
        step('tool_call','r1',tool='rm_ticket',agent='rm_requests',allowed=False,reason='not_in_book'),
        step('tool_call','r1',tool='rm_transaction_insights',agent='rm_client',allowed=True,shared=False,category='transaction_insights'),
        step('tool_call','c1',tool='my_accounts',agent='accounts',allowed=True,rows=2)]
    rm=insights.agents(rows,steps,[],{},{},'rm')
    cards={c['agent']:c for c in rm['agents']}
    assert rm['side']=='rm' and rm['requests']==1 and list(cards)==['rm_client','rm_requests']
    assert cards['rm_requests']['stops']=={'cap':1} and cards['rm_requests']['tool_refusals']==1 and cards['rm_requests']['refused']==1
    assert cards['rm_client']['consent_gaps']==1 and cards['rm_client']['tool_refusals']==0
    assert [c['agent'] for c in insights.agents(rows,steps,[],{},{},'client')['agents']]==['accounts']
    assert [c['agent'] for c in insights.agents(rows,steps,[],{},{})['agents']]==['accounts','rm_client','rm_requests']  # all
    failures=insights.failures(rows,steps,[],NOW,1,'live','rm')
    assert [(g['kind'],g['agent'],g['title']) for g in failures['groups']]==[('loop_cap','rm_requests','rm_requests reached its read limit')]
    assert (failures['side'],failures['tool_refusals'],failures['loop_stops'],failures['consent_gaps'])==(
        'rm',{'rm_ticket: not_in_book':1},{'cap':1},{'transaction_insights':1})
    assert insights.failures(rows,steps,[],NOW,1,'live','client')['groups']==[]
