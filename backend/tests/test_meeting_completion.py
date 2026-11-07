"""Real gateway, SQL, canonical assistant brief and carried-forward commitments; synthetic human-entered minutes."""
import json,time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from pypdf import PdfReader
from app.schemas.meeting_lifecycle import MinutesContent
from tests.test_meeting_lifecycle import flow,start,post,view,headers,claim_transcript,export_body,approve_body,SOPHIE
from tests.test_meeting_preparation import store_reference_plan



def next_meeting(flow):
    later=uuid4();at=datetime.now(timezone.utc)+timedelta(days=3)
    with psycopg.connect(flow.uri) as db:
        db.execute("insert into rm.appointments(appointment_id,customer_id,advisor_id,starts_at,ends_at,format,status,purpose) values(%s,1002,%s,%s,%s,'video','confirmed','Review our outstanding commitments')",
            (later,SOPHIE,at,at+timedelta(minutes=30)))
    result=flow._replace(meeting=later)
    post(result,'rm','/start',{'notice_id':str(flow.notice),'purpose':'Review our outstanding commitments','outcome':'Agree next steps','command_id':str(uuid4())})
    return result


def test_schedule_can_read_a_proposable_meeting_beyond_the_default_window(flow,local_data_api):
    later=uuid4();at=datetime.now(timezone.utc)+timedelta(days=20)
    with psycopg.connect(flow.uri) as db:
        db.execute("insert into rm.appointments(appointment_id,customer_id,advisor_id,starts_at,ends_at,format,status,purpose) values(%s,1002,%s,%s,%s,'video','confirmed','Future calendar QA')",
            (later,SOPHIE,at,at+timedelta(minutes=30)))
    standard=flow.http.get('/rm/appointments',headers=headers(flow,'rm'))
    expanded=flow.http.get('/rm/appointments?future_days=40',headers=headers(flow,'rm'))
    assert standard.status_code==expanded.status_code==200,(standard.text,expanded.text)
    assert str(later) not in {row['appointment_id'] for row in standard.json()['data']}
    assert str(later) in {row['appointment_id'] for row in expanded.json()['data']}
    assert flow.http.get('/rm/appointments?future_days=40',headers=headers(flow,'client')).status_code==403
    assert flow.http.get('/rm/appointments?past_days=30&future_days=40',headers=headers(flow,'rm')).status_code==422
    assert flow.http.get('/rm/appointments?future_days=63',headers=headers(flow,'rm')).status_code==422


def test_assistant_workspace_share_one_brief_and_outstanding_actions_stay_current(flow,local_data_api):
    start(flow,'client');post(flow,'rm','/transcript-import',export_body())
    claim=claim_transcript(flow);context=flow.worker.context(claim)
    content=MinutesContent.model_validate({'title':'Review and next steps',
        'summary':[{'text':'The document is awaiting bank review.','cue_id':'cue1','quote':'The document is awaiting bank review.'}],
        'decisions':[],'actions':[{'action_id':str(uuid4()),'text':text,'cue_id':cue,'quote':quote,'owner':owner,
            'commitment':'agreed','condition':None,'due_on':None,'due_quote':None} for text,cue,quote,owner in
            [('Send updated details','cue2','I will send the updated details.','client'),('Check review status','cue3','I will check the review status.','rm')]],
        'client_recap':'The document is awaiting bank review. The agreed follow-ups are to send details and check the status.'})
    flow.worker.save(claim,context,content);flow.worker.finish(claim)
    draft=view(flow,'rm')['draft'];post(flow,'rm','/approve-minutes',approve_body(draft))
    post(flow,'rm','/followups',{**approve_body(draft),'action_ids':[str(action.action_id) for action in content.actions]})
    assert flow.http.get(f'/meetings/{flow.meeting}/recap',headers=headers(flow,'client')).json() is None
    post(flow,'rm','/share-recap',approve_body(draft))
    recap=flow.http.get(f'/meetings/{flow.meeting}/recap',headers=headers(flow,'client'));assert recap.status_code==200,recap.text
    assert [item['owner'] for item in recap.json()['followups']]==['client']
    pdf=flow.http.get(f'/meetings/{flow.meeting}/recap/pdf',headers=headers(flow,'client'),params={key:recap.json()[key] for key in ('document_id','version','content_hash')})
    assert pdf.status_code==200,pdf.text[:100]
    extracted=' '.join(' '.join(page.extract_text().split()) for page in PdfReader(BytesIO(pdf.content)).pages)
    assert content.client_recap in extracted
    next_flow=next_meeting(flow);later=next_flow.meeting
    from tests.test_meeting_preparation import source_frame
    frame=source_frame(next_flow,local_data_api)
    carried=[source for source in frame.sources if source.reference.startswith('meeting_followups:')]
    agreed=[source for source in carried if source.reference.startswith('meeting_followups:followup:')]
    assert len(agreed)==2 and {'rm','client'}=={source.text.split('Owner: ')[1].split('.')[0] for source in agreed}
    assert all('Current status: open.' in source.text and 'Due: not agreed.' in source.text for source in agreed)
    # Even if the writer selects another record, current commitments remain a mandatory cited section.
    row=store_reference_plan(next_flow,local_data_api,frame,[{'source_id':str(frame.sources[0].source_id),'kind':'record','question':None}])
    workspace=next_flow.http.get(f'/meetings/{later}/preparation',headers=headers(flow,'rm'))
    assert workspace.status_code==200,workspace.text
    mandatory=next(section for section in workspace.json()['card']['sections'] if section['heading']=='Outstanding follow-ups and RM tasks')
    assert {source.text for source in carried}=={item['text'] for item in mandatory['items']}
    brief_query={'version':row['version'],'content_hash':row['content_hash']}
    assert next_flow.http.get(f'/meetings/{later}/preparation/pdf',headers=headers(flow,'rm'),params=brief_query).status_code==200
    assert next_flow.http.get(f'/meetings/{later}/preparation/pdf',headers=headers(flow,'client'),params=brief_query).status_code==403
    response=flow.http.post('/rm/assistant',headers=headers(flow,'rm'),json={'message':'Brief me for this confirmed meeting','intent':'meeting_brief',
        'context':{'client_id':1002,'appointment_id':str(later)}})
    assert response.status_code==200,response.text
    answers=[json.loads(line.partition('data: ')[2]) for event in response.text.split('\r\n\r\n') if event.startswith('event: answer')
        for line in event.split('\r\n') if line.startswith('data: ')]
    assert len(answers)==1,response.text
    reply=answers[0];saved=reply['meeting_brief']
    assert saved['brief_id']==row['brief_id'] and saved['content_hash']==row['content_hash'] and saved['version']==row['version']
    assert [item['text'] for section in reply['sections'] for item in section['items']]==[item['text'] for section in workspace.json()['card']['sections'] for item in section['items']]
    assert flow.http.get(f'/meetings/{later}/preparation',headers=headers(flow,'client')).status_code==403
    action=next(action for action in content.actions if action.owner=='client')
    post(flow,'client',f'/followups/{action.action_id}/complete',{'evidence':'The requested details were sent.','command_id':str(uuid4())})
    current=source_frame(next_flow,local_data_api)
    assert current.source_hash!=frame.source_hash
    assert len([source for source in current.sources if source.reference.startswith('meeting_followups:followup:')])==1
    changed=next_flow.http.get(f'/meetings/{later}/preparation',headers=headers(flow,'rm'))
    assert changed.status_code==200 and changed.json()['sources_changed']
    rejected=next_flow.http.post(f'/meetings/{later}/preparation/decision',headers=headers(flow,'rm'),json={
        'version':row['version'],'revision':row['revision'],'content_hash':row['content_hash'],'outcome':'approve','note':None,'command_id':str(uuid4())})
    assert rejected.status_code==409,rejected.text


def test_revoked_native_brief_streams_the_current_saved_view_and_keeps_consent_request_cards(flow,local_data_api):
    from tests.test_meeting_preparation import source_frame
    flow=next_meeting(flow);frame=source_frame(flow,local_data_api)
    private=next(source for source in frame.sources if source.category=='transaction_insights')
    stored=store_reference_plan(flow,local_data_api,frame,[{'source_id':str(private.source_id),'kind':'record','question':None}])
    with psycopg.connect(flow.uri) as db:
        for category in ('transaction_insights','chat_topics'):
            grant=frame.grants_used[category];assert grant is not None
            db.execute('update consent.grants set revoked_at=clock_timestamp() where grant_id=%s',(grant,))
    response=flow.http.post('/rm/assistant',headers=headers(flow,'rm'),json={'message':'Open the saved brief for this meeting','intent':'meeting_brief',
        'context':{'client_id':1002,'appointment_id':str(flow.meeting)}})
    assert response.status_code==200,response.text
    events=[]
    for block in response.text.split('\r\n\r\n'):
        lines=block.split('\r\n')
        if lines and lines[0] in ('event: answer','event: section'):
            data=next((line[6:] for line in lines if line.startswith('data: ')),None)
            if data is not None:events.append((lines[0][7:],json.loads(data)))
    reply=next(data for event,data in events if event=='answer')
    sections=[data for event,data in events if event=='section']
    workspace=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'));assert workspace.status_code==200,workspace.text
    assert reply['meeting_brief']['brief_id']==stored['brief_id'] and reply['meeting_brief']['sources_changed']
    assert len(sections)==1 and sections[0]['sections']==reply['sections']
    assert private.text not in response.text and private.text not in workspace.text
    assert [(card['category'],card['reason'],card['meeting_id']) for card in reply['consent_asks']]==[
        ('transaction_insights','meeting_prep',str(flow.meeting)),('chat_topics','meeting_prep',str(flow.meeting))]
    assert [item['text'] for section in reply['sections'] for item in section['items']]==[
        item['text'] for section in workspace.json()['card']['sections'] for item in section['items']]


def test_an_unavailable_selected_meeting_never_substitutes_the_only_other_meeting(flow,local_data_api):
    from tests.test_meeting_preparation import source_frame
    future=next_meeting(flow);frame=source_frame(future,local_data_api)
    store_reference_plan(future,local_data_api,frame,[{'source_id':str(frame.sources[0].source_id),'kind':'record','question':None}])
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.appointments set status='cancelled' where customer_id=1002 and appointment_id<>%s",(future.meeting,))
    selected=str(uuid4())
    response=flow.http.post('/rm/assistant',headers=headers(flow,'rm'),json={'message':'Open the briefing for the selected meeting','intent':'meeting_brief',
        'context':{'client_id':1002,'appointment_id':selected}})
    assert response.status_code==200,response.text
    reply=next(json.loads(line[6:]) for event in response.text.split('\r\n\r\n') if event.startswith('event: answer')
        for line in event.split('\r\n') if line.startswith('data: '))
    assert reply['status']=='clarify' and 'meeting_brief' not in reply,response.text
    assert reply['context']['appointment_id']==selected and str(future.meeting) not in json.dumps(reply.get('meeting_brief'))


def test_the_new_briefing_screen_reads_the_same_saved_lifecycle_brief(flow,local_data_api):
    from tests.test_meeting_preparation import source_frame
    flow=next_meeting(flow);frame=source_frame(flow,local_data_api)
    saved=store_reference_plan(flow,local_data_api,frame,[{'source_id':str(frame.sources[0].source_id),'kind':'record','question':None}])
    response=flow.http.get(f'/rm/meetings/{flow.meeting}/brief',headers=headers(flow,'rm'))
    assert response.status_code==200,response.text
    brief=response.json()['brief']
    assert brief is not None,'The new briefing screen must reuse the existing saved lifecycle brief'
    assert brief['brief_id']==saved['brief_id'] and brief['version']==saved['version']
    body={'brief_id':brief['brief_id'],'version':brief['version'],'command_id':str(uuid4())}
    approved=flow.http.post(f'/rm/meetings/{flow.meeting}/brief/approve',headers=headers(flow,'rm'),json=body)
    assert approved.status_code==200,approved.text
    workspace=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm')).json()
    assert workspace['brief_id']==brief['brief_id'] and workspace['decision']=='approved'
    changed=flow.http.post(f'/rm/meetings/{flow.meeting}/brief/approve',headers=headers(flow,'rm'),json={**body,'brief_id':str(uuid4()),'command_id':str(uuid4())})
    assert changed.status_code==409,changed.text
    with psycopg.connect(flow.uri) as db:
        db.execute("update banking.customer_profiles set full_name=full_name||' QA' where customer_id=1002")
    stale=flow.http.get(f'/rm/meetings/{flow.meeting}/brief',headers=headers(flow,'rm')).json()['brief']
    assert stale['stale'] and stale['brief_status']=='draft'
    rejected=flow.http.post(f'/rm/meetings/{flow.meeting}/brief/approve',headers=headers(flow,'rm'),json={**body,'command_id':str(uuid4())})
    assert rejected.status_code==409,rejected.text


def test_lifecycle_read_waits_for_the_meeting_without_holding_the_audit_lock(flow):
    from tests.test_storyline_db import as_sophie
    start(flow,'client')
    with psycopg.connect(flow.uri) as editor,psycopg.connect(flow.uri) as reader,psycopg.connect(flow.uri,autocommit=True) as observer:
        editor.execute("set local statement_timeout='7s'");reader.execute("set local statement_timeout='7s'")
        editor.execute('select appointment_id from rm.appointments where appointment_id=%s for update',(flow.meeting,))
        as_sophie(editor);as_sophie(reader)
        reader_pid=reader.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as calls:
            pending=calls.submit(reader.execute,'select public.meeting_lifecycle_status(%s)',(flow.meeting,))
            try:
                deadline=time.monotonic()+3
                while True:
                    blockers=[row[0] for row in observer.execute('select unnest(pg_blocking_pids(%s))',(reader_pid,))]
                    if editor.info.backend_pid in blockers:break
                    if pending.done():raise AssertionError('The lifecycle reader must wait for the existing meeting transaction')
                    if time.monotonic()>deadline:raise AssertionError('The lifecycle read did not reach its meeting lock within three seconds')
                    time.sleep(0.01)
                assert editor.execute('select public.meeting_view(%s)',(flow.meeting,)).fetchone()[0]['meeting_id']==str(flow.meeting)
                editor.commit()
                assert pending.result(timeout=8).fetchone()[0]['meeting_id']==str(flow.meeting)
            finally:editor.rollback()
