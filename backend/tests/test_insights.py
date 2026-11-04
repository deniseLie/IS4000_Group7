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
