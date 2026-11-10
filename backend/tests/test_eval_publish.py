"""Publishing local evaluations to LangSmith (evaluation/publish.py): datasets mirror their case files, one experiment per
publication, one run per case with its checks as feedback. A recorder stands in for the LangSmith client; no model runs."""
import sys
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from app.services.quality import asked,status
from evaluation import meeting_boundaries,publish,retrieval,talking_points
from evaluation.langsmith_sync import example_id


class Recorder:
    """Records LangSmith calls and holds one dataset in memory."""
    def __init__(self):
        self.examples,self.runs,self.feedback,self.projects,self.tags,self.deleted,self.exists={},[],[],[],[],[],False
        self.ds=SimpleNamespace(id='ds-1')
    def has_dataset(self,dataset_name):return self.exists
    def read_dataset(self,dataset_name):return self.ds
    def create_dataset(self,name,**kw):self.exists=True;return self.ds
    def list_examples(self,dataset_id):
        return [SimpleNamespace(id=i,inputs=e['inputs'],outputs=e['outputs'],metadata=e['metadata']|{'dataset_split':['base']}) for i,e in self.examples.items()]
    def create_examples(self,dataset_id,examples):self.examples|={e['id']:e for e in examples}
    def update_examples(self,dataset_id,updates):self.examples|={e['id']:e for e in updates}
    def delete_examples(self,ids):
        for i in ids:self.examples.pop(i)
    def list_dataset_versions(self,dataset_id):return [SimpleNamespace(as_of=datetime(2026,11,9,tzinfo=timezone.utc))]
    def update_dataset_tag(self,dataset_id,as_of,tag):self.tags.append(tag)
    def create_project(self,name,**kw):
        self.projects.append((name,kw));return SimpleNamespace(id=f'exp-{len(self.projects)}',url=f'https://smith/{name}')
    def delete_project(self,project_id):self.deleted.append(project_id)
    def create_run(self,name,inputs,run_type,**kw):self.runs.append({'name':name,'inputs':inputs,**kw})
    def create_feedback(self,run_id,key,**kw):self.feedback.append({'run_id':run_id,'key':key,**kw})
    def flush(self):pass


CASES=[publish.Case('a',{'say':'first'},{'check':'one'}),publish.Case('b',{'say':'second'},{'check':'two'},{'group':'g'})]


def test_publish_mirrors_the_file_and_records_one_run_per_case(tmp_path):
    source=tmp_path/'cases.json';source.write_text('[]')
    ls=Recorder()
    results=[publish.Result('a',{'answer':'ok'},{'right':(1.0,''),'graded':(0.5,'rank 2'),'unscored':(None,'')},12.5),
        publish.Result('b',{'answer':'no'},{'right':(0.0,'got x')},3.0,error=None)]
    out=publish.publish('demobank-x','d',source,CASES,results,'v1',{'model':'m'},client=ls)
    assert out['create']==2 and out['cases']==2 and set(ls.examples)=={example_id('demobank-x','a'),example_id('demobank-x','b')}
    name,kw=ls.projects[0]
    assert name.startswith('demobank-x-v1-') and kw['reference_dataset_id']=='ds-1'
    assert kw['metadata']['partial'] is False and kw['metadata']['case_count']==2 and kw['metadata']['model']=='m'
    assert kw['metadata']['dataset_file_tag']==publish.tag(source)==ls.tags[0]
    assert [r['reference_example_id'] for r in ls.runs]==[example_id('demobank-x','a'),example_id('demobank-x','b')]
    assert all(r['project_name']==name and r['trace_id']==r['id'] and r['dotted_order'].endswith(str(r['id'])) for r in ls.runs)
    assert (ls.runs[0]['end_time']-ls.runs[0]['start_time']).total_seconds()==0.0125 and ls.runs[1]['start_time']>ls.runs[0]['end_time']
    assert [(f['key'],f['score']) for f in ls.feedback]==[('right',1.0),('graded',0.5),('right',0.0)]  # None is not sent
    assert all(f['session_id']=='exp-1' for f in ls.feedback)
    again=publish.publish('demobank-x','d',source,CASES,results[:1],'v1',{},client=ls)
    assert (again['create'],again['update'],again['delete'])==(0,0,0) and ls.projects[1][1]['metadata']['partial'] is True


def test_publish_updates_and_deletes_examples_and_refuses_unknown_results(tmp_path):
    source=tmp_path/'cases.json';source.write_text('[]')
    ls=Recorder()
    publish.publish('demobank-x','d',source,CASES,[publish.Result('a',{},{'right':(1.0,'')})],'v1',{},client=ls)
    out=publish.publish('demobank-x','d',source,[publish.Case('a',{'say':'first'},{'check':'changed'})],[publish.Result('a',{},{'right':(1.0,'')})],'v1',{},client=ls)
    assert (out['create'],out['update'],out['delete'])==(0,1,1)
    with pytest.raises(ValueError,match='unknown cases'):publish.publish('demobank-x','d',source,CASES,[publish.Result('z',{},{})],'v1',{},client=ls)
    with pytest.raises(ValueError,match='duplicate'):publish.examples('demobank-x',[CASES[0],CASES[0]])


def test_a_refused_run_fails_the_publication_and_says_how_far_it_got(tmp_path):
    """A LangSmith usage limit (429) on the runs is an error, never a reported success; an experiment left with no runs is
    deleted again, one with some runs is kept."""
    source=tmp_path/'cases.json';source.write_text('[]')
    both=[publish.Result('a',{},{'right':(1.0,'')}),publish.Result('b',{},{'right':(1.0,'')})]
    ls=Recorder()
    def refuse(*a,**kw):raise RuntimeError('429 Monthly unique traces usage limit exceeded')
    ls.create_run=refuse
    with pytest.raises(RuntimeError,match=r'after 0 of 2 cases \(experiment removed\): .*usage limit'):
        publish.publish('demobank-x','d',source,CASES,both,'v1',{},client=ls)
    assert ls.deleted==['exp-1']
    ls=Recorder();runs=[]
    def second(name,inputs,run_type,**kw):
        if runs:refuse()
        runs.append(name)
    ls.create_run=second
    with pytest.raises(RuntimeError,match=r'after 1 of 2 cases \(experiment kept\)'):
        publish.publish('demobank-x','d',source,CASES,both,'v1',{},client=ls)
    assert ls.deleted==[]


@pytest.mark.parametrize('built,file',[(retrieval.published_cases,retrieval.cases),(talking_points.published_cases,talking_points.cases),
    (talking_points.published_conversations,talking_points.conversations),(meeting_boundaries.published_cases,meeting_boundaries.cases)],
    ids=['search','chat-insights','conversations','tool-rules'])
def test_every_case_file_builds_examples_the_dashboard_can_show(built,file):
    cases=built()
    assert len(cases)==len(file()) and len(publish.examples('demobank-x',cases))==len(cases)
    assert all(asked(c.inputs) and c.expected['check'] for c in cases)


def test_search_passes_in_the_top_four_and_grades_the_rank():
    row=lambda rank,expected=('a',),found=('x','a'):{'id':'r','expected':list(expected),'found':list(found),'rank':rank,'ms':4.0}
    first=retrieval.published_result(row(1,found=('a',)));second=retrieval.published_result(row(2))
    missed=retrieval.published_result(row(None,found=('x',)));gap=retrieval.published_result(row(None,expected=(),found=('x',)))
    assert first.checks['reciprocal_rank']==(1.0,'rank 1') and second.checks['reciprocal_rank']==(0.5,'rank 2')
    assert second.checks['in_top_4']==(1.0,'') and missed.checks['in_top_4']==(0.0,'got x') and gap.checks=={'nothing_returned':(0.0,'got x')}
    grade=lambda r:status([SimpleNamespace(key=k,score=s) for k,(s,_) in r.checks.items()],r.error)
    assert [grade(r) for r in (first,second,missed,gap)]==['pass','pass','fail','fail']
    assert retrieval.published_result(row(None,expected=(),found=())).checks=={'nothing_returned':(1.0,'')}


def test_chat_insights_score_topics_false_points_stages_and_the_share_offer():
    base={'id':'c','group':'positive','expect':{'home_loan':'planning'},'gate':'model','topics':['home_loan'],'stages':{'home_loan':'planning'},'share':None,'ms':9.0}
    exact=talking_points.published_result(base)
    assert {k:s for k,(s,_) in exact.checks.items()}=={'topics_match':1.0,'no_false_point':1.0,'stage_match':1.0,'no_false_share':1.0}
    assert exact.outputs['answer']=='Recorded home_loan (planning)' and exact.outputs['model_calls']==1
    wrong=talking_points.published_result(base|{'topics':['home_loan','insurance'],'stages':{'home_loan':'exploring','insurance':'planning'},'share':'insurance'})
    assert {k:s for k,(s,_) in wrong.checks.items()}=={'topics_match':0.0,'no_false_point':0.0,'stage_match':0.0,'no_false_share':0.0}
    stopped=talking_points.published_result(base|{'expect':{},'gate':'no_topic','topics':[],'stages':{}})
    assert stopped.checks['stage_match']==(None,'') and stopped.outputs['answer']=='Recorded nothing (stopped before the model: no_topic)'
    assert talking_points.published_result(base|{'topics':[],'stages':{},'error':'timeout'}).error=='LLMError: timeout'


def test_conversations_score_the_final_state_false_points_and_false_holds():
    row={'id':'CV1','match':False,'problems':['home_loan.stage: exploring'],'false_points':['insurance'],'false_on_hold':[],'calls':3,'errors':[],'ms':20.0,
        'final':{'home_loan':{'stage':'exploring','timeframe':'6 months','on_hold':False},'insurance':{'stage':'exploring'}}}
    out=talking_points.published_conversation(row)
    assert {k:s for k,(s,_) in out.checks.items()}=={'final_state_match':0.0,'no_false_point':0.0,'no_false_on_hold':1.0}
    assert out.outputs['answer']=='Final topics: home_loan: exploring, 6 months; insurance: exploring' and out.outputs['model_calls']==3
    assert talking_points.published_conversation(row|{'errors':['timeout']}).error=='LLMError: timeout'


def test_every_tool_rule_case_passes_the_policy_and_the_connector():
    for case in meeting_boundaries.cases():
        result=meeting_boundaries.published_result(meeting_boundaries.check(case))
        assert all(score==1.0 for score,_ in result.checks.values()),(case.id,result.checks)
        assert ('connector' in result.checks)==(case.actor not in ('rm_chat','client_chat'))


@pytest.mark.parametrize('module,argv,message',[(talking_points,['--publish'],'needs --live'),
    (retrieval,['--publish','--search','keyword,hybrid'],'one set-up')],ids=['chat-insights-offline','search-two-setups'])
def test_publish_refuses_a_run_it_cannot_record_honestly(module,argv,message,monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['x',*argv])
    with pytest.raises(SystemExit):module.main()
    assert message in capsys.readouterr().err
