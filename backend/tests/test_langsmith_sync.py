import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from app.schemas.chat import ChatRequest
from app.services import answers
from app.services.guardrail import assess_request
from evaluation import langsmith_sync as sync

def test_every_dataset_file_builds_valid_examples():
    seen=set()
    for name in sync.DATASETS:
        cases,_=sync.load(name)
        built=sync.examples(name,cases)
        assert len(built)==len(cases) and all(e['outputs']['check'] and e['inputs']['mode']=='public' for e in built)
        seen|={e['id'] for e in built}
    assert len(seen)==sum(len(sync.load(n)[0]) for n in sync.DATASETS)  # ids never collide across datasets

def test_example_ids_are_stable():
    assert sync.example_id('demobank-focused','minimum-payment')==sync.example_id('demobank-focused','minimum-payment')
    assert sync.example_id('demobank-focused','minimum-payment')!=sync.example_id('demobank-standard','minimum-payment')

def test_private_details_only_in_secrets_cases():
    case={'id':'x','question':'my card 4111 1111 1111 1111 was declined','check':'c'}
    with pytest.raises(ValueError,match='private-looking'):sync.to_example('demobank-standard',case)
    assert sync.to_example('demobank-safety',case|{'expected_route':'secrets'})['inputs']['question']==case['question']
    with pytest.raises(ValueError,match='unknown article'):sync.to_example('demobank-standard',{'id':'y','question':'q','check':'c','cites_any':['nope']})
    with pytest.raises(ValueError,match='duplicate'):sync.examples('demobank-standard',[{'id':'a','question':'q','check':'c'}]*2)

@pytest.mark.parametrize('case',sync.load('demobank-safety')[0],ids=lambda c:c['id'])
def test_safety_cases_match_the_fixed_routes(case):
    """The safety dataset's expectations hold offline, without any model call."""
    req=ChatRequest(messages=case.get('history',[])+[{'role':'user','content':case['question']}])
    assert assess_request(req).fixed_response.code==case['expected_route']
    result=asyncio.run(answers.make_answer(req,allow_model=False))
    assert result['response_origin']==case['expected_origin'] and result['needs_human']==case['expect_needs_human']
    assert set(case['cites_any'])&{s['id'] for s in result['sources']}

def test_plan_creates_updates_and_deletes():
    a,b,c=({'id':i,'inputs':{'question':i},'outputs':{'check':'c'},'metadata':{'case_id':i,'dataset':'d'}} for i in 'abc')
    remote={'b':{**{k:b[k] for k in ('inputs','outputs','metadata')},'outputs':{'check':'old'}},'c':{k:c[k] for k in ('inputs','outputs','metadata')},'gone':{}}
    create,update,delete=sync.plan([a,b,c],remote)
    assert [e['id'] for e in create]==['a'] and [e['id'] for e in update]==['b'] and delete==['gone']

class Recorder:
    """Records LangSmith dataset calls; holds one dataset in memory. No model is involved."""
    def __init__(self,existing=()):
        self.calls=[];self.examples={e['id']:e for e in existing};self.exists=bool(existing)
        self.ds=SimpleNamespace(id='ds-1')
    def has_dataset(self,dataset_name):return self.exists
    def read_dataset(self,dataset_name):return self.ds
    def create_dataset(self,name,**kw):self.calls.append(('create_dataset',name));self.exists=True;return self.ds
    def list_examples(self,dataset_id):
        return [SimpleNamespace(id=i,inputs=e['inputs'],outputs=e['outputs'],metadata=e['metadata']|{'dataset_split':['base']}) for i,e in self.examples.items()]
    def create_examples(self,dataset_id,examples):self.calls.append(('create',len(examples)));self.examples|={e['id']:e for e in examples}
    def update_examples(self,dataset_id,updates):self.calls.append(('update',len(updates)))
    def delete_examples(self,ids):self.calls.append(('delete',len(ids)))
    def list_dataset_versions(self,dataset_id):return [SimpleNamespace(as_of=datetime(2026,10,1,tzinfo=timezone.utc)),SimpleNamespace(as_of=datetime(2026,10,28,tzinfo=timezone.utc))]
    def update_dataset_tag(self,dataset_id,as_of,tag):self.calls.append(('tag',as_of.day,tag))

def test_sync_creates_then_is_idempotent():
    client=Recorder()
    first=sync.sync(client,'demobank-safety')
    assert first['create']==len(sync.load('demobank-safety')[0]) and client.calls[:2]==[('create_dataset','demobank-safety'),('create',first['create'])]
    assert client.calls[-1]==('tag',28,first['tag'])
    client.calls.clear()
    again=sync.sync(client,'demobank-safety')
    assert (again['create'],again['update'],again['delete'])==(0,0,0) and client.calls==[('tag',28,first['tag'])]

def test_dry_run_writes_nothing():
    client=Recorder()
    summary=sync.sync(client,'demobank-focused',dry_run=True)
    assert summary['create']==6 and client.calls==[]
