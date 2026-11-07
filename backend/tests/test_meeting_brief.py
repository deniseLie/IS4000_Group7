"""Brief Writer (0118, services/meetings/brief.py): what a saved brief keeps, and how it is re-read when opened.
Pure functions on synthetic replies and evidence: no database and no model."""
from uuid import uuid4
from app.services.meetings import brief as b
from app.services.rm import evidence as ev

TOPIC="Topic from the client's chats: Property financing · planning · within 12 months · inferred from chat (last mentioned 4 Nov 2026). Not confirmed by the client."


def reply() -> dict:
    return {'status':'ok','intent':'meeting_brief','summary':'Briefing for Michelle Ng.','prompt_version':'rm_assistant/v6','sections':[
        {'heading':'Who the client is','items':[{'text':'Michelle Ng has been with you since 1 Oct 2026.','kind':'record','owner':'rm','source_ids':['profile']}]},
        {'heading':'What the client has told us','items':[
            {'text':'Prefers video calls in the afternoon (recorded 20 Oct 2026).','kind':'client_stated','owner':'client','source_ids':['preferences']},
            {'text':TOPIC,'kind':'suggestion','owner':'rm','source_ids':['talking-points']},
            {'text':'The client has not shared transaction insights with you.','kind':'gap','owner':'none','source_ids':['transaction-insights']}]},
        {'heading':'Conversation starters','items':[
            {'text':'Ask how the condo search is going.','kind':'suggestion','owner':'rm','source_ids':['talking-points']},
            {'text':'Ask whether the time deposit proceeds could go toward a down payment.','kind':'suggestion','owner':'rm','source_ids':['milestones','talking-points']}]}],
        'sources':[{'id':'profile','title':'Client profile','kind':'record','record':{'name':'Michelle Ng'}},
            {'id':'talking-points','title':"Topics from the client's chats",'kind':'record','record':{'points':[{'line':'x'}]}}]}


def bundle(shared: bool) -> ev.Bundle:
    out=ev.Bundle()
    point={'point_id':str(uuid4()),'topic':'home_loan','stage':'planning','timeframe':'within_12m','concerns':[],'origin':'inferred',
        'first_seen':'2026-11-04','last_seen':'2026-11-04'}
    out.add(ev.talking_points_evidence({'shared':shared,'data':[point] if shared else [],'grant_id':str(uuid4()) if shared else None,'category':'chat_topics'},1002))
    out.add(ev.insights_evidence({'shared':False,'data':None,'category':'transaction_insights'},1002))
    return out


def saved(**recheck) -> dict:
    return {'brief_id':str(uuid4()),'version':1,'status':'draft','created_text':'Sat 7 Nov 2026, 9:00 am','content':b.store(reply()),
        'withdrawn':[],'removed_points':[],'stale':False,**recheck}


def test_a_saved_brief_keeps_no_item_or_record_from_a_consent_gated_read() -> None:
    content=b.store(reply())
    told=next(s for s in content['sections'] if s['heading']=='What the client has told us')
    assert [i['text'] for i in told['items']]==['Prefers video calls in the afternoon (recorded 20 Oct 2026).'] and told['gated_at']==1
    assert 'record' not in next(s for s in content['sources'] if s['id']=='talking-points')
    assert next(s for s in content['sources'] if s['id']=='profile')['record']=={'name':'Michelle Ng'}
    starters=next(s for s in content['sections'] if s['heading']=='Conversation starters')
    assert len(starters['items'])==2  # the model's checked points are kept; view() decides whether they still stand


def test_opening_the_brief_reads_her_topics_again_under_current_consent() -> None:
    viewed=b.view(saved(),bundle(shared=True))
    told=next(s for s in viewed['sections'] if s['heading']=='What the client has told us')
    assert told['items'][0]['kind']=='client_stated' and told['items'][1]['source_ids']==['talking-points']
    assert 'Property financing · planning' in told['items'][1]['text']
    assert viewed['withheld']==0 and viewed['consent']=={'chat_topics':True,'transaction_insights':False}
    assert {s['id'] for s in viewed['sources']}>={'profile','talking-points'}


def test_a_withdrawn_grant_renders_as_removed_by_client_and_its_suggestions_are_dropped() -> None:
    viewed=b.view(saved(withdrawn=[{'category':'chat_topics','on':'7 Nov 2026'}]),bundle(shared=False))
    texts=[i['text'] for s in viewed['sections'] for i in s['items']]
    assert 'Removed by client on 7 Nov 2026: topics from her chats.' in texts
    assert not any('Property financing · planning' in t for t in texts)
    assert 'Ask how the condo search is going.' not in texts and viewed['withheld']==1  # rested only on her topics
    assert 'Ask whether the time deposit proceeds could go toward a down payment.' in texts  # also rests on a record


def test_a_removed_topic_is_named_by_date_only_and_the_card_cites_every_item() -> None:
    viewed=b.view(saved(removed_points=[{'point_id':str(uuid4()),'on':'6 Nov 2026'}]),bundle(shared=True))
    texts=[i['text'] for s in viewed['sections'] for i in s['items']]
    assert 'Removed by client on 6 Nov 2026: 1 topic shown in an earlier version.' in texts
    card=b.card(uuid4(),viewed,'2026-11-07T09:00:00+08:00')
    labels={i.label for s in card.sections for i in s.items}
    assert labels<={'record','client_said','ai_suggestion','gap'} and card.status=='draft' and card.version==1
    assert all(i.sources for s in card.sections for i in s.items if i.label!='gap')
