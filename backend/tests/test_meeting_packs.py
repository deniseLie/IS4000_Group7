"""Real SQL/JWT pack permissions, dynamic catalogue figures and exact approved sharing. No simulated agent."""
import json,os
from uuid import uuid4
import httpx,psycopg,pytest
from tests.test_meeting_lifecycle import flow,start,headers
from app.schemas.meeting_packs import PackContent,PackInputs,PackPlan,PackView
from app.services.meetings.documents import discussion_pack
from app.services.meetings.packs import with_discussion_items


def pack_inputs(flow,local_data_api,amounts):
    response=httpx.post(local_data_api.url+'/rpc/meeting_pack_inputs',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting),'p_amounts':amounts})
    assert response.status_code==200,response.text
    return response.json()


def setup_pack(flow,local_data_api):
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.appointments set topics=array['fixed_deposit'] where appointment_id=%s",(flow.meeting,))
        db.execute("update meetings.catalogue_terms set annual_rate=3.1234,minimum_amount=12345.67,fee=12.34,version=2 where code='demo-fixed-deposit-sgd'")
    start(flow,'client')
    amounts=[{'label':'Amount for discussion, entered by RM','currency':'SGD','amount':'99999999999999.99'}]
    inputs=pack_inputs(flow,local_data_api,amounts);command=str(uuid4())
    plan={'agenda':['goals','options','next_steps'],'questions':['ask_product_risks'],'product_ids':[inputs['catalogue'][0]['item_id']]}
    body={'p_meeting_id':str(flow.meeting),'p_revision':inputs['revision'],'p_source_hash':inputs['source_hash'],'p_amounts':amounts,'p_plan':plan,'p_command_id':command}
    response=httpx.post(local_data_api.url+'/rpc/meeting_save_pack',headers=headers(flow,'rm'),json=body)
    assert response.status_code==200,response.text
    return response.json(),body


def approval(document):
    return {key:document[key] for key in ('document_id','version','revision','content_hash')}|{'command_id':str(uuid4())}


def test_dynamic_pack_terms_remain_private_until_exact_separate_approval_and_share(flow,local_data_api):
    document,body=setup_pack(flow,local_data_api)
    assert document['content']['products'][0]['annual_rate']=='3.1234'
    assert document['content']['products'][0]['minimum_amount']=='12345.67'
    assert document['content']['stated_amounts'][0]['amount']=='99999999999999.99'
    assert 'source_notes' not in json.dumps(document) and 'transaction_insights' not in json.dumps(document)
    for role in ('client','outsider'):
        refused=httpx.post(local_data_api.url+'/rpc/meeting_pack_inputs',headers=headers(flow,role),json={'p_meeting_id':str(flow.meeting),'p_amounts':[]})
        assert refused.status_code==403,refused.text
    assert flow.http.get(f'/meetings/{flow.meeting}/pack',headers=headers(flow,'client')).json() is None
    assert flow.http.post(f'/meetings/{flow.meeting}/pack/share',headers=headers(flow,'rm'),json=approval(document)).status_code==409
    assert flow.http.post(f'/meetings/{flow.meeting}/pack/approve',headers=headers(flow,'client'),json=approval(document)).status_code==403
    stale=approval(document)|{'version':document['version']+1}
    assert flow.http.post(f'/meetings/{flow.meeting}/pack/approve',headers=headers(flow,'rm'),json=stale).status_code==409
    accepted=flow.http.post(f'/meetings/{flow.meeting}/pack/approve',headers=headers(flow,'rm'),json=approval(document));assert accepted.status_code==200,accepted.text
    assert flow.http.get(f'/meetings/{flow.meeting}/pack',headers=headers(flow,'client')).json() is None
    share=approval(document)
    first=flow.http.post(f'/meetings/{flow.meeting}/pack/share',headers=headers(flow,'rm'),json=share);assert first.status_code==200,first.text
    assert flow.http.post(f'/meetings/{flow.meeting}/pack/share',headers=headers(flow,'rm'),json=share).json()==first.json()
    visible=flow.http.get(f'/meetings/{flow.meeting}/pack',headers=headers(flow,'client'));assert visible.status_code==200,visible.text
    assert PackContent.model_validate(visible.json()['content'])==PackContent.model_validate(document['content'])
    assert flow.http.get(f'/meetings/{flow.meeting}/pack',headers=headers(flow,'outsider')).status_code==403
    with psycopg.connect(flow.uri) as db:db.execute("update meetings.catalogue_terms set annual_rate=9,version=3 where code='demo-fixed-deposit-sgd'")
    unchanged=flow.http.get(f'/meetings/{flow.meeting}/pack',headers=headers(flow,'client')).json()
    assert PackContent.model_validate(unchanged['content'])==PackContent.model_validate(document['content']) and unchanged['sources_changed']


def test_catalogue_change_blocks_stale_pack_and_duplicate_plan_codes_are_refused(flow,local_data_api):
    document,body=setup_pack(flow,local_data_api)
    with psycopg.connect(flow.uri) as db:db.execute("update meetings.catalogue_terms set fee=99,version=3 where code='demo-fixed-deposit-sgd'")
    refused=flow.http.post(f'/meetings/{flow.meeting}/pack/approve',headers=headers(flow,'rm'),json=approval(document))
    assert refused.status_code==409,refused.text
    fresh=pack_inputs(flow,local_data_api,body['p_amounts'])
    duplicate={**body,'p_command_id':str(uuid4()),'p_source_hash':fresh['source_hash'],'p_plan':{**body['p_plan'],'agenda':['goals','goals']}}
    response=httpx.post(local_data_api.url+'/rpc/meeting_save_pack',headers=headers(flow,'rm'),json=duplicate)
    assert response.status_code==400,response.text
    for question,product_ids in [('confirm_document_review',fresh['catalogue'][:1]),('compare_catalogue_options',[]),
        ('compare_catalogue_options',fresh['catalogue'][:1]),('review_catalogue_product',[])]:
        plan={**body['p_plan'],'questions':[question],'product_ids':[product['item_id'] for product in product_ids]}
        response=httpx.post(local_data_api.url+'/rpc/meeting_save_pack',headers=headers(flow,'rm'),
            json={**body,'p_command_id':str(uuid4()),'p_source_hash':fresh['source_hash'],'p_plan':plan})
        assert response.status_code==400 and response.json()['code']=='22023',response.text
    for amounts in ([{'label':'Bad amount','currency':'SGD','amount':'-0.01'}],[{'label':'Bad amount','currency':'SGD','amount':'100000000000000.00'}]):
        response=flow.http.post(f'/meetings/{flow.meeting}/pack/draft',headers=headers(flow,'rm'),json={'amounts':amounts,'command_id':str(uuid4())})
        assert response.status_code==422,response.text


def test_product_discussion_matches_the_saved_catalogue_selection(flow,local_data_api):
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.appointments set topics=array['fixed_deposit','home_loan'] where appointment_id=%s",(flow.meeting,))
    start(flow,'rm')
    frame=PackInputs.model_validate(pack_inputs(flow,local_data_api,[]))
    assert len(frame.catalogue)==2
    expected=(None,'Which terms of the listed product','Which of the listed product terms should we compare')
    for count,question in enumerate(expected):
        selection=PackPlan(agenda=('goals','options'),questions=('review_options',),product_ids=tuple(item.item_id for item in frame.catalogue[:count]))
        plan=with_discussion_items(selection,frame)
        result=httpx.post(local_data_api.url+'/rpc/meeting_save_pack',headers=headers(flow,'rm'),json={
            'p_meeting_id':str(flow.meeting),'p_revision':frame.revision,'p_source_hash':frame.source_hash,'p_amounts':[],
            'p_plan':plan.model_dump(mode='json'),'p_command_id':str(uuid4())})
        assert result.status_code==200,result.text
        document=discussion_pack(PackView.model_validate({**result.json(),'sources_changed':False}))
        rendered=[paragraph for section in document.sections if section.heading=='Questions to discuss' for paragraph in section.paragraphs]
        assert len(rendered)==int(question is not None)
        if question is not None:assert rendered[0].startswith(question)
        agenda=next(section.paragraphs for section in document.sections if section.heading=='Agenda')
        assert agenda[1:]==((),('Review the listed product terms',),('Discuss the available options',))[count]


@pytest.mark.skipif(os.getenv('TEST_MEETING_PREP_LIVE')!='1',reason='Real Codex preparation-agent opt-in required')
def test_real_proposal_drafter_uses_only_agreed_topics_and_code_inserted_figures(flow,local_data_api,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    with psycopg.connect(flow.uri) as db:db.execute("update rm.appointments set topics=array['fixed_deposit'] where appointment_id=%s",(flow.meeting,))
    start(flow,'rm');body={'amounts':[{'label':'RM entered discussion amount','currency':'SGD','amount':'6500.25'}],'command_id':str(uuid4())}
    response=flow.http.post(f'/meetings/{flow.meeting}/pack/draft',headers=headers(flow,'rm'),json=body,timeout=180)
    assert response.status_code==200,response.text
    document=response.json();assert document['status']=='draft' and document['content']['stated_amounts']==body['amounts']
    assert all(item['topic']=='fixed_deposit' and item['annual_rate']=='2.5000' for item in document['content']['products'])
    assert document['content']['products'],'The explicit product agenda should be represented in the pack'
    assert 'product_terms' in document['content']['agenda'] and 'options' not in document['content']['agenda']
    assert 'review_catalogue_product' in document['content']['questions'] and 'compare_catalogue_options' not in document['content']['questions']
    assert flow.http.post(f'/meetings/{flow.meeting}/pack/draft',headers=headers(flow,'rm'),json=body).json()==document
    assert flow.http.get(f'/meetings/{flow.meeting}/pack',headers=headers(flow,'client')).json() is None
