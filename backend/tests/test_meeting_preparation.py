"""RM/JWT scoped preparation contracts with real PostgreSQL readers; the writer uses a real model only with opt-in."""
import json,os
from uuid import uuid4
import httpx,psycopg,pytest
from tests.test_meeting_lifecycle import flow,start,headers
from pydantic import ValidationError
from app.schemas.meeting_preparation import BriefSelection,PreparationInputs
from app.services.meetings.preparation import selection_plan


@pytest.mark.parametrize('count',[0,29,30,31])
def test_complete_brief_selection_bound_applies_before_grouping(count):
    values={'items':[{'heading':'profile' if index%2 else 'requests','source_id':str(uuid4()),'kind':'record','question':None} for index in range(count)]}
    if not 1<=count<=30:
        with pytest.raises(ValidationError):BriefSelection.model_validate(values)
    else:
        plan=selection_plan(BriefSelection.model_validate(values))
        assert sum(len(section.items) for section in plan.sections)==count
        assert all(set(item.model_dump())=={'source_id','kind','question'} for section in plan.sections for item in section.items)


def test_selection_refuses_cross_section_duplicates_and_preserves_a_record_plus_its_question():
    source=str(uuid4());record={'heading':'profile','source_id':source,'kind':'record','question':None}
    with pytest.raises(ValidationError):
        BriefSelection.model_validate({'items':[record,{**record,'heading':'requests'}]})
    question={'heading':'questions','source_id':source,'kind':'question','question':'clarify_next_step'}
    plan=selection_plan(BriefSelection.model_validate({'items':[record,question]}))
    assert [section.heading for section in plan.sections]==['profile','questions']
    assert [item.kind for section in plan.sections for item in section.items]==['record','question']


def source_frame(flow,local_data_api):
    response=httpx.post(local_data_api.url+'/rpc/meeting_preparation_inputs',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting)})
    assert response.status_code==200,response.text
    return PreparationInputs.model_validate(response.json())


def store_reference_plan(flow,local_data_api,frame,items):
    # Explicit human-selected references test the storage contract, not a simulated agent answer.
    response=httpx.post(local_data_api.url+'/rpc/meeting_save_brief',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting),
        'p_revision':frame.revision,'p_source_hash':frame.source_hash,'p_plan':{'sections':[{'heading':'questions','items':items}]},'p_command_id':str(uuid4())})
    assert response.status_code==200,response.text
    return response.json()


def test_brief_inputs_are_scoped_stable_and_exclude_unnecessary_private_details(flow,local_data_api):
    start(flow,'client');first=source_frame(flow,local_data_api);second=source_frame(flow,local_data_api)
    assert first.source_hash==second.source_hash
    assert first.sources and first.talking_points
    assert any(source.category=='transaction_insights' for source in first.sources)
    assert all(set(item.point.model_dump())=={'point_id','topic','stage','timeframe','kind','concerns','origin','first_seen','last_seen'} for item in first.talking_points)
    text=json.dumps([source.model_dump(mode='json') for source in first.sources]).lower()
    assert 'counterparty' not in text and 'merchant' not in text and 'source_notes' not in text
    for role in ('client','outsider'):
        response=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,role))
        assert response.status_code==403,response.text
    with psycopg.connect(flow.worker.uri.get_secret_value(),autocommit=True) as db:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):db.execute('select public.meeting_preparation_inputs(%s)',(flow.meeting,))


def test_stored_brief_contains_only_references_and_revocation_blocks_approval(flow,local_data_api):
    start(flow,'client');frame=source_frame(flow,local_data_api)
    source=next(item for item in frame.sources if item.category=='transaction_insights')
    row=store_reference_plan(flow,local_data_api,frame,[{'source_id':str(source.source_id),'kind':'record','question':None},
        {'source_id':str(source.source_id),'kind':'question','question':'clarify_next_step'}])
    with psycopg.connect(flow.uri) as db:
        stored=db.execute('select plan,source_manifest from meetings.briefs where brief_id=%s',(row['brief_id'],)).fetchone()
        assert source.text not in json.dumps(stored) and 'Michelle' not in json.dumps(stored)
        db.execute("update consent.grants set revoked_at=clock_timestamp() where grant_id=%s",(source.grant_id,))
    response=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'));assert response.status_code==200,response.text
    result=response.json();assert result['sources_changed'] and result['card']['withheld']==2
    assert source.text not in response.text
    assert result['card']['sections'][0]['items'][0]['label']=='gap'
    decision={'version':row['version'],'revision':row['revision'],'content_hash':row['content_hash'],'outcome':'approve','note':None,'command_id':str(uuid4())}
    refused=flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json=decision)
    assert refused.status_code==409,refused.text


def test_deleted_topic_is_replaced_with_its_removal_date_and_question_is_dropped(flow,local_data_api):
    start(flow,'client');frame=source_frame(flow,local_data_api);topic=frame.talking_points[0]
    store_reference_plan(flow,local_data_api,frame,[{'source_id':str(topic.source_id),'kind':'record','question':None},
        {'source_id':str(topic.source_id),'kind':'question','question':'discuss_shared_topic'}])
    with psycopg.connect(flow.uri) as db:
        db.execute("update insights.talking_points set deleted_at=clock_timestamp(),purge_after=clock_timestamp()+interval '7 days',deleted_reason='client' where point_id=%s",(topic.point.point_id,))
    response=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'));assert response.status_code==200,response.text
    result=response.json();items=result['card']['sections'][0]['items']
    assert len(items)==1 and items[0]['label']=='gap' and 'Removed on' in items[0]['text']
    assert topic.point.line() not in response.text


def test_brief_returned_for_changes_cannot_be_approved_without_a_new_version(flow,local_data_api):
    start(flow,'client');frame=source_frame(flow,local_data_api);source=frame.sources[0]
    row=store_reference_plan(flow,local_data_api,frame,[{'source_id':str(source.source_id),'kind':'record','question':None}])
    body={'version':row['version'],'revision':row['revision'],'content_hash':row['content_hash'],'outcome':'changes','note':'Focus on the agreed documents.','command_id':str(uuid4())}
    response=flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json=body);assert response.status_code==200,response.text
    refused=flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json={**body,'outcome':'approve','command_id':str(uuid4())})
    assert refused.status_code==409,refused.text


def test_approved_brief_returns_to_rm_review_when_its_consent_is_withdrawn(flow,local_data_api):
    from tests.test_meeting_lifecycle import post
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Discuss the shared information','outcome':'Clarify next steps','command_id':str(uuid4())})
    frame=source_frame(flow,local_data_api);source=next(item for item in frame.sources if item.category=='transaction_insights')
    row=store_reference_plan(flow,local_data_api,frame,[{'source_id':str(source.source_id),'kind':'record','question':None}])
    post(flow,'rm','/preparation/decision',{'version':row['version'],'revision':row['revision'],'content_hash':row['content_hash'],'outcome':'approve','note':None,'command_id':str(uuid4())})
    with psycopg.connect(flow.uri) as db:db.execute('update consent.grants set revoked_at=clock_timestamp() where grant_id=%s',(source.grant_id,))
    for role in ('rm','client'):
        result=flow.http.get(f'/meetings/{flow.meeting}/lifecycle',headers=headers(flow,role));assert result.status_code==200,result.text
        assert result.json()['stage']=='brief_review' and result.json()['error_code']=='brief_access_changed'


def test_current_private_client_products_and_collateral_alerts_cannot_be_omitted_or_approved_after_change(flow,local_data_api):
    start(flow,'client');before=source_frame(flow,local_data_api)
    assert any(source.reference.startswith('rm_client_profile:') and source.text.startswith('Recorded investment portfolio:') for source in before.sources)
    assert not any(source.reference.startswith('rm_collateral_alert:') for source in before.sources)
    with psycopg.connect(flow.uri) as database:database.execute('select rm.demo_lombard_scenario()')
    current=source_frame(flow,local_data_api)
    assert current.source_hash!=before.source_hash
    assert any(source.text.startswith('Recorded financing product:') for source in current.sources)
    assert any(source.text.startswith('Recorded Lombard borrowing:') for source in current.sources)
    alert=next(source for source in current.sources if source.reference.startswith('rm_collateral_alert:'))
    assert '33000.00' in alert.text and 'only the bank decides' in alert.text
    unrelated=next(source for source in current.sources if source.source_id!=alert.source_id)
    invalid=httpx.post(local_data_api.url+'/rpc/meeting_save_brief',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting),
        'p_revision':current.revision,'p_source_hash':current.source_hash,'p_plan':{'sections':[{'heading':'questions','items':[
            {'source_id':str(unrelated.source_id),'kind':'question','question':'review_collateral_next_step'}]}]},'p_command_id':str(uuid4())})
    assert invalid.status_code==400 and invalid.json()['code']=='22023',invalid.text
    saved=store_reference_plan(flow,local_data_api,current,[{'source_id':str(unrelated.source_id),'kind':'record','question':None},
        {'source_id':str(alert.source_id),'kind':'question','question':'review_collateral_next_step'}])
    response=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'))
    assert response.status_code==200,response.text
    section=next(section for section in response.json()['card']['sections'] if section['heading']=='Recorded collateral alert')
    assert [item['text'] for item in section['items']]==[alert.text]
    assert len(section['items'][0]['sources'])==1 and section['items'][0]['sources'][0].startswith('rm_collateral_alert:')
    assert any('which lending values and borrowing figures' in item['text'] for section in response.json()['card']['sections'] for item in section['items'] if item['label']=='ai_suggestion')
    with psycopg.connect(flow.uri) as database:database.execute('select banking.reset_portfolio_demo()')
    changed=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'))
    assert changed.status_code==200 and changed.json()['sources_changed'] and alert.text not in changed.text
    body={'version':saved['version'],'revision':saved['revision'],'content_hash':saved['content_hash'],'outcome':'approve','note':None,'command_id':str(uuid4())}
    refused=flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json=body)
    assert refused.status_code==409,refused.text


@pytest.mark.skipif(os.getenv('TEST_MEETING_PREP_LIVE')!='1',reason='Real Codex preparation-agent opt-in required')
def test_real_brief_writer_and_exact_version_review(flow,local_data_api,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');start(flow,'client')
    response=flow.http.post(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'),timeout=180)
    assert response.status_code==200,response.text
    prepared=response.json();assert prepared['card']['sections'] and not prepared['sources_changed']
    body={'version':prepared['card']['version'],'revision':prepared['revision'],'content_hash':prepared['content_hash'],'outcome':'approve','note':None,'command_id':str(uuid4())}
    stale=flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json={**body,'version':body['version']+1})
    assert stale.status_code==409
    accepted=flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json=body)
    assert accepted.status_code==200,accepted.text
    assert accepted.json()['status']=='approved'
    assert flow.http.post(f'/meetings/{flow.meeting}/preparation/decision',headers=headers(flow,'rm'),json=body).json()==accepted.json()
