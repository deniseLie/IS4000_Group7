"""Durable preparation -> correction -> approval -> owner follow-ups on real SQL/JWT and the parent graph."""
import asyncio
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import httpx,psycopg,pytest
from tests.test_meeting_lifecycle import flow,headers,post,view,export_body,approve_body,claim_transcript
from tests.test_meeting_preparation import source_frame,store_reference_plan
from app.schemas.meeting_lifecycle import MinutesContent
from app.services.meetings.lifecycle import dispatch
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.graph import process_transcript


def lifecycle(flow,role):
    response=flow.http.get(f'/meetings/{flow.meeting}/lifecycle',headers=headers(flow,role))
    assert response.status_code==200,response.text
    return response.json()


def human_minutes():
    return MinutesContent.model_validate({'title':'Review and next steps','summary':[{'text':'The document is awaiting bank review.','quote':'The document is awaiting bank review.','cue_id':'cue1'}],
        'decisions':[],'actions':[{'action_id':str(uuid4()),'text':text,'cue_id':cue,'quote':quote,'owner':owner,'commitment':'agreed','condition':None,'due_on':None,'due_quote':None}
        for text,cue,quote,owner in [('Send updated details','cue2','I will send the updated details.','client'),('Check review status','cue3','I will check the review status.','rm')]],
        'client_recap':'The document is awaiting bank review. You will send updated details and your RM will check the review status.'})


def test_parent_graph_waits_for_rm_then_corrects_and_tracks_the_complete_journey(flow,local_data_api):
    assert lifecycle(flow,'rm')['stage']=='scheduled'
    assert flow.http.get(f'/meetings/{flow.meeting}/lifecycle',headers=headers(flow,'outsider')).status_code==403
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':'Review agreed questions','outcome':'Agree next steps','command_id':str(uuid4())})
    with psycopg.connect(flow.uri) as db:
        db.execute("select meetings.enqueue_v2('brief.requested',%s,'{\"revision\":1}',%s)",(flow.meeting,'brief:'+str(uuid4())))
    while claim:=flow.worker.claim():asyncio.run(dispatch(flow.worker,claim))
    assert lifecycle(flow,'rm')['stage']=='preparing'
    frame=source_frame(flow,local_data_api);source=frame.sources[0]
    brief=store_reference_plan(flow,local_data_api,frame,[{'source_id':str(source.source_id),'kind':'record','question':None}])
    assert lifecycle(flow,'rm')['stage']=='brief_review'
    post(flow,'rm','/preparation/decision',{'version':brief['version'],'revision':brief['revision'],'content_hash':brief['content_hash'],'outcome':'approve','note':None,'command_id':str(uuid4())})
    assert lifecycle(flow,'rm')['stage']=='brief_approved'
    for role in ('rm','client'):post(flow,role,'/notice-seen',{'notice_id':str(flow.notice),'revision':1})
    now=datetime.now(timezone.utc)
    for path,at in [('/documented-start',now-timedelta(minutes=3)),('/documented-end',now-timedelta(minutes=1))]:post(flow,'rm',path,{'revision':1,'at':at.isoformat(),'command_id':str(uuid4())})
    assert lifecycle(flow,'rm')['stage']=='awaiting_transcript' and lifecycle(flow,'rm')['waiting_for']=='rm'
    post(flow,'rm','/transcript-import',export_body());claim=claim_transcript(flow);context=flow.worker.context(claim)
    content=human_minutes();flow.worker.save(claim,context,content);flow.worker.finish(claim)
    original=view(flow,'rm')['draft'];assert lifecycle(flow,'rm')['stage']=='minutes_review'
    review={**approve_body(original),'outcome':'changes','note':'Keep the review status clear and retain both agreed owners.'}
    first=post(flow,'rm','/minutes/review',review);assert first['error_code']=='minutes_changes_requested'
    assert post(flow,'rm','/minutes/review',review)==first
    refused=flow.http.post(f'/meetings/{flow.meeting}/approve-minutes',headers=headers(flow,'rm'),json=approve_body(original));assert refused.status_code==409,refused.text
    assert flow.http.post(f'/meetings/{flow.meeting}/minutes/regenerate',headers=headers(flow,'client'),json=approve_body(original)).status_code==403
    regeneration=post(flow,'rm','/minutes/regenerate',approve_body(original));assert regeneration['source_kind']=='transcript'
    replacement=flow.worker.claim();assert replacement.event_id==regeneration['event_id']
    corrected_context=flow.worker.context(replacement);assert corrected_context.review_note==review['note']
    assert corrected_context.review_id is not None
    saved=flow.worker.save(replacement,corrected_context,content)
    restarted=WorkerDatabase(flow.worker.uri)
    assert str(restarted.context(replacement).draft_id)==saved['draft_id']
    with pytest.raises(psycopg.ProgrammingError,match='draft inputs are stale'):
        restarted.save(replacement,corrected_context.model_copy(update={'source_hash':'0'*64}),content)
    assert restarted.save(replacement,corrected_context,content)==saved
    assert str(asyncio.run(process_transcript(restarted,replacement)))==saved['draft_id']
    current=view(flow,'rm')['draft'];assert current['version']==original['version']+1
    post(flow,'rm','/approve-minutes',approve_body(current))
    assert lifecycle(flow,'client')['stage']=='following_up'
    post(flow,'rm','/followups',{**approve_body(current),'action_ids':[str(item.action_id) for item in content.actions]})
    post(flow,'rm','/share-recap',approve_body(current))
    assert lifecycle(flow,'rm')['waiting_for']=='rm'
    for owner in ('rm','client'):
        for item in content.actions:
            if item.owner==owner:post(flow,owner,f'/followups/{item.action_id}/complete',{'evidence':'Completed the synthetic agreed action','command_id':str(uuid4())})
        assert lifecycle(flow,'rm')['waiting_for']==('client' if owner=='rm' else None)
    assert lifecycle(flow,'client')['stage']=='done'
    restarted=WorkerDatabase(flow.worker.uri);restarted.verify_identity();assert restarted.claim() is None
    assert lifecycle(flow,'rm')==lifecycle(flow,'client')
    with psycopg.connect(flow.uri) as db:
        checkpoint=db.execute('select stage,last_event_id from meetings.threads where meeting_id=%s',(flow.meeting,)).fetchone()
        assert checkpoint[0]=='done' and checkpoint[1] is not None


def test_rejected_minutes_cannot_regenerate_or_mutate_approved_effects(flow,local_data_api):
    from tests.test_meeting_lifecycle import start
    start(flow,'client');post(flow,'rm','/transcript-import',export_body())
    claim=claim_transcript(flow);context=flow.worker.context(claim);flow.worker.save(claim,context,human_minutes());flow.worker.finish(claim)
    current=view(flow,'rm')['draft']
    rejected=post(flow,'rm','/minutes/review',{**approve_body(current),'outcome':'reject','note':'This document must not be used.'})
    assert rejected['error_code']=='minutes_rejected'
    assert flow.http.post(f'/meetings/{flow.meeting}/minutes/regenerate',headers=headers(flow,'rm'),json=approve_body(current)).status_code==409


@pytest.mark.parametrize('outcome',['changes','reject'])
def test_legacy_notes_writes_cannot_bypass_review_and_reviewed_save_replays(flow,local_data_api,outcome):
    from tests.test_meeting_lifecycle import start
    start(flow,'client')
    lines=export_body()['text'].splitlines()
    cues=[{'cue_id':'cue'+str(index),'speaker':line.split(':',1)[0].lower(),'start_seconds':None,'text':line.split(':',1)[1].strip()} for index,line in enumerate(lines,1)]
    post(flow,'rm','/notes',{'revision':1,'cues':cues,'command_id':str(uuid4())})
    context=httpx.post(local_data_api.url+'/rpc/meeting_notes_context',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting)})
    assert context.status_code==200,context.text
    current=context.json();assert 'review_id' not in current and 'review_note' not in current
    body={'p_meeting_id':str(flow.meeting),'p_source_id':current['source_id'],'p_source_hash':current['source_hash'],
        'p_revision':current['revision'],'p_consent_epoch':current['consent_epoch'],'p_content':human_minutes().model_dump(mode='json'),'p_command_id':str(uuid4())}
    initial=httpx.post(local_data_api.url+'/rpc/meeting_save_generated_draft',headers=headers(flow,'rm'),json=body);assert initial.status_code==200,initial.text
    original=view(flow,'rm')['draft']
    post(flow,'rm','/minutes/review',{**approve_body(original),'outcome':outcome,'note':'Keep the document pending and retain the two agreed operational follow-ups.'})
    for name in ('meeting_save_generated_draft','meeting_save_notes_draft'):
        denied=httpx.post(local_data_api.url+'/rpc/'+name,headers=headers(flow,'rm'),json={**body,'p_command_id':str(uuid4())})
        assert denied.status_code==400 and denied.json()['code']=='P1001',denied.text
    review=flow.http.get(f'/meetings/{flow.meeting}/minutes/review',headers=headers(flow,'rm'));assert review.status_code==200,review.text
    reviewed={**body,'p_command_id':str(uuid4()),'p_review_id':review.json()['review_id']}
    replacement=httpx.post(local_data_api.url+'/rpc/meeting_save_reviewed_draft',headers=headers(flow,'rm'),json=reviewed)
    if outcome=='reject':
        assert replacement.status_code==400 and replacement.json()['code']=='P1001',replacement.text
        assert view(flow,'rm')['draft']['draft_id']==original['draft_id']
    else:
        assert replacement.status_code==200,replacement.text
        replay=httpx.post(local_data_api.url+'/rpc/meeting_save_reviewed_draft',headers=headers(flow,'rm'),json=reviewed)
        assert replay.status_code==200 and replay.json()==replacement.json(),replay.text
        assert view(flow,'rm')['draft']['version']==original['version']+1
