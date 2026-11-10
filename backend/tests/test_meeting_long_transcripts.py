"""Long source boundaries and durable model progress over real native SQL/JWT; real model run is separately opt-in."""
import asyncio,json,os
from uuid import uuid4
import psycopg,pytest
from psycopg.types.json import Jsonb
from app.schemas.meeting_lifecycle import EvidenceClaim,MinutesContent
from app.schemas.meeting_chunks import MinutesSelection
from app.services.meetings.writer import PROMPT,chunks,selected_summaries,selected_decisions,claim_groups
from app.services.meetings.graph import process_transcript
from app.services.prompts import load
from tests.test_meeting_lifecycle import flow,start,post,headers,view,claim_transcript,approve_body


def test_sql_checks_explicit_dates_without_reducing_the_recap_limit(database):
    quote='I will send the checklist on 19 November 2026.'
    cues=[{'cue_id':'cue1','speaker':'rm','start_seconds':None,'text':quote}]
    action={'action_id':str(uuid4()),'text':'Send the checklist','cue_id':'cue1','quote':quote,'owner':'rm',
        'commitment':'agreed','condition':None,'due_on':'2026-11-19','due_quote':'19 November 2026'}
    content={'title':'Review','summary':[{'text':'Checklist discussed.','cue_id':'cue1','quote':quote}],
        'decisions':[],'actions':[action],'client_recap':'We discussed sending the checklist. '*20}
    validate='select meetings.validate_content(%s,%s)'
    database.execute(validate,(Jsonb(cues),Jsonb(content)))
    with pytest.raises(psycopg.errors.InvalidParameterValue,match='exact calendar-date evidence'), database.transaction():
        database.execute(validate,(Jsonb(cues),Jsonb({**content,'actions':[{**action,'due_on':'2026-11-20'}]})))


@pytest.mark.parametrize('indices,error',[
    ((0,1),None),((0,2),None),
    ((0,),'omitted a distinct supporting statement'),
    ((1,2),'repeats a supporting statement'),
    ((0,3),'unknown summary index')])
def test_overview_covers_distinct_statements_without_repeating_the_same_quote(indices,error):
    claims=tuple(EvidenceClaim(text=text,cue_id='cue'+str(index),quote=quote) for index,(text,quote) in enumerate([
        ('A review remains pending.','The review is pending.'),
        ('The agenda covers document questions.','We agreed to discuss the document questions.'),
        ('Document questions are on the agenda.','We agreed to discuss the document questions.')],1))
    selection=MinutesSelection(title='Document discussion',summary_indices=indices,decision_indices=())
    if error is not None:
        with pytest.raises(ValueError,match=error):selected_summaries(claims,claim_groups(claims),selection)
    else:
        selected=selected_summaries(claims,claim_groups(claims),selection)
        assert {item.quote for item in selected}=={item.quote for item in claims}


@pytest.mark.parametrize('indices,error',[((0,2),None),((1,2),None),((0,1),'one decision per distinct'),((0,),'one decision per distinct'),((0,3),'unknown decision index')])
def test_decision_selection_preserves_each_distinct_statement_and_rejects_duplicates(indices,error):
    claims=tuple(EvidenceClaim(text=text,cue_id='cue'+str(index),quote=quote) for index,(text,quote) in enumerate([
        ('Discuss the document questions.','We agreed to discuss the document questions.'),
        ('The document questions are agreed agenda.','We agreed to discuss the document questions.'),
        ('Compare the available options.','We agreed to compare the available options.')],1))
    selection=MinutesSelection(title='Document discussion',summary_indices=(0,),decision_indices=indices)
    if error is not None:
        with pytest.raises(ValueError,match=error):selected_decisions(claims,claim_groups(claims),selection)
    else:
        selected=selected_decisions(claims,claim_groups(claims),selection)
        assert {item.quote for item in selected}=={item.quote for item in claims}


def utf8_export(target: int) -> str:
    words=['測試內容' for _ in range(64)]
    def size():
        return len(json.dumps([{'cue_id':'cue'+str(index),'speaker':'rm','start_seconds':None,'text':text} for index,text in enumerate(words,1)],ensure_ascii=False).encode())
    for index in range(len(words)):
        room=min(2000-len(words[index]),(target-size())//3)
        words[index]+='測'*room
    remaining=target-size();assert 0<=remaining<3
    words[-1]+='x'*remaining
    assert size()==target
    return '\n'.join('RM: '+text for text in words)


@pytest.mark.parametrize('size',[127999,128000,128001])
def test_utf8_intake_boundary_is_atomic_at_the_real_http_sql_boundary(flow,size):
    start(flow,'client')
    body={'revision':1,'filename':'synthetic-long.txt','text':utf8_export(size),'speakers':[{'label':'RM','role':'rm'}],
        'coverage':'partial','command_id':str(uuid4())}
    result=flow.http.post(f'/meetings/{flow.meeting}/transcript-import',headers=headers(flow,'rm'),json=body)
    assert result.status_code==(200 if size<=128000 else 422),result.text
    with psycopg.connect(flow.uri) as db:
        rows=db.execute('select octet_length(cues::text) from meetings.sources where meeting_id=%s',(flow.meeting,)).fetchall()
        assert rows==([(size,)] if size<=128000 else [])


def test_completed_chunk_survives_claim_replacement_and_is_purged_on_withdrawal(flow):
    start(flow,'client')
    post(flow,'rm','/transcript-import',{'revision':1,'filename':'synthetic.txt','text':'\n'.join('RM: The review is pending.' for _ in range(180)),
        'speakers':[{'label':'RM','role':'rm'}],'coverage':'partial','command_id':str(uuid4())})
    old=claim_transcript(flow);context=flow.worker.context(old);selected=chunks(context.cues)
    assert sum(map(len,selected))==180 and len(selected)==3
    ids=tuple(cue.cue_id for cue in selected[0]);prompt=load('minutes_writer',PROMPT)['id']
    slot=flow.worker.chunk(old,0,prompt,ids)
    # A deliberately human-entered structured draft tests storage only; it is not a simulated model result.
    content=MinutesContent.model_validate({'title':'Storage review','summary':[{'cue_id':ids[0],'text':'Review pending.','quote':'The review is pending.'}],
        'decisions':[],'actions':[],'client_recap':'Review pending.'})
    flow.worker.save_chunk(old,context,0,prompt,slot.chunk_hash,ids,content,None,None,500)
    with psycopg.connect(flow.uri) as db:db.execute("update meetings.events set available_at=clock_timestamp()-interval '1 second' where event_id=%s",(old.event_id,))
    fresh=flow.worker.claim();assert fresh.claim_token!=old.claim_token
    assert flow.worker.chunk(fresh,0,prompt,ids).content==content
    with pytest.raises(psycopg.Error) as stale:flow.worker.save_chunk(old,context,0,prompt,slot.chunk_hash,ids,content,None,None,500)
    assert stale.value.sqlstate=='P1002'
    post(flow,'client','/transcript-revoke',{'command_id':str(uuid4())})
    with psycopg.connect(flow.uri) as db:assert db.execute('select count(*) from meetings.source_chunks where source_id=%s',(context.source_id,)).fetchone()==(0,)
    with pytest.raises(psycopg.Error):flow.worker.chunk(fresh,0,prompt,ids)


@pytest.mark.skipif(os.getenv('TEST_MEETING_LONG_LIVE')!='1',reason='Real Codex long-meeting generation opt-in required')
def test_real_long_meeting_processes_late_commitments_and_approved_recap(flow,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');start(flow,'client')
    lines=['RM: The document is submitted and awaiting bank review.']
    lines.extend('RM: We are reviewing the same pending document without making a new decision.' for _ in range(178))
    lines[80]='Client: I will send my updated contact details.'
    lines[-1]='RM: I will check the document review status and contact the client. If the bank requests another copy, I will ask the client to upload it.'
    post(flow,'rm','/transcript-import',{'revision':1,'filename':'long-written-test.txt','text':'\n'.join(lines),
        'speakers':[{'label':'RM','role':'rm'},{'label':'Client','role':'client'}],'coverage':'partial','command_id':str(uuid4())})
    claim=claim_transcript(flow);asyncio.run(process_transcript(flow.worker,claim))
    draft=view(flow,'rm')['draft'];actions=draft['content']['actions']
    assert {action['owner'] for action in actions if action['commitment']=='agreed'}=={'rm','client'}
    assert any(action['commitment']=='conditional' for action in actions)
    with psycopg.connect(flow.uri) as db:
        assert db.execute('select count(*) from meetings.source_chunks where source_id=(select current_source from meetings.workflows where meeting_id=%s)',(flow.meeting,)).fetchone()==(3,)
    post(flow,'rm','/approve-minutes',approve_body(draft));post(flow,'rm','/share-recap',approve_body(draft))
    assert view(flow,'client')['shared_recap']==draft['content']['client_recap']
