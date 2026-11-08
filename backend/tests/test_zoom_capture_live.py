"""Real My Notes/Codex replay over native JWT/PostgREST; signed event is deliberately synthetic.

Uses only the previously registered disposable meeting manifest. This validates
the complete new ingestion code without claiming a fresh live webhook delivery
or two independently authenticated speakers. Both-owner follow-ups use explicit
RM-reviewed roleplay notes, never invented automatic speaker identities.
"""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
import httpx,pytest
from app.core.config import settings
from app.schemas.zoom_capture import FetchPermit
from app.services.meetings.zoom import Zoom,refreshed_token
from app.services.meetings.my_notes import MyNotesReader
from app.services.meetings.worker import dispatch
from tests.test_meeting_lifecycle import flow,start,post,view,headers,approve_body,claim_transcript
from tests.test_zoom_capture import ingress,send,fetch_claim

pytestmark=pytest.mark.skipif(os.getenv('TEST_ZOOM_CAPTURE_LIVE')!='1',reason='Explicit registered Zoom manifest, own-user grant and live Codex opt-in required')


def test_real_api_fetch_to_review_and_both_followup_owners(flow,local_data_api,ingress,monkeypatch):
    directory=Path(os.environ['TEST_ZOOM_OAUTH_DIRECTORY'])
    recorded=json.loads(Path(os.environ['TEST_ZOOM_MEETING_MANIFEST']).read_text())
    monkeypatch.setattr(settings,'zoom_oauth_directory',directory)
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    start(flow,'client')
    token=refreshed_token(directory);provider=Zoom(token)
    try:owner=provider.owner()
    finally:provider.close()
    assert owner.id==recorded['host_id'],'The authorised grant is not the recorded synthetic host'
    # Explicit replay fixture discovery: no unrelated notes are enumerated.
    lookup=FetchPermit(meeting_id=flow.meeting,job_id=uuid4(),provider_id=recorded['provider_id'],account_id=owner.account_id,
        host_id=owner.id,original_uuid=recorded['occurrence_uuid'],note_id='replay-fixture-discovery',revision=1,consent_epoch=1,source_exists=False)
    reader=MyNotesReader(token)
    try:
        notes=reader.notes(lookup)
        assert len(notes.notes)==1,'Review the registered source if its note count changes'
    finally:reader.close()
    appointment=flow.http.get(f'/meetings/{flow.meeting}/appointment',headers=headers(flow,'rm')).json()
    binding={'p_meeting_id':str(flow.meeting),'p_provider_id':recorded['provider_id'],'p_join_url':recorded['join_url'],
        'p_starts_at':appointment['starts_at'],'p_account_id':owner.account_id,'p_host_id':owner.id,'p_original_uuid':recorded['occurrence_uuid']}
    registered=httpx.post(local_data_api.url+'/rpc/meeting_zoom_link_bind',headers=headers(flow,'rm'),json=binding)
    assert registered.status_code==200,'Registered replay fixture binding failed'
    event={'event':'my_notes.transcript_generated','event_ts':1,'payload':{'account_id':owner.account_id,'operator_id':owner.id,
        'object':{'meeting_id':recorded['occurrence_uuid'],'note_id':notes.notes[0].note_id}}}
    assert send(ingress,event).status_code==200
    fetch=fetch_claim(flow);asyncio.run(dispatch(flow.worker,fetch))
    ready=claim_transcript(flow);source=flow.worker.context(ready)
    assert source.cues and all(cue.speaker=='unmapped' for cue in source.cues)
    asyncio.run(dispatch(flow.worker,ready))
    first=view(flow,'rm')['draft']
    assert first['content']['actions'] and all(action['owner']=='unassigned' for action in first['content']['actions'])
    assert view(flow,'rm')['source_provenance']['origin']=='zoom_my_notes_api'
    assert 'draft' not in view(flow,'client')
    post(flow,'rm','/approve-minutes',approve_body(first))
    refused=flow.http.post(f'/meetings/{flow.meeting}/followups',headers=headers(flow,'rm'),
        json={**approve_body(first),'action_ids':[action['action_id'] for action in first['content']['actions']]})
    assert refused.status_code==422
    assert len(source.cues)==2,'Recheck explicit roleplay assertions if the recorded source changes'
    reviewed=[{**cue.model_dump(mode='json'),'speaker':role,'start_seconds':None}
        for cue,role in zip(source.cues,('rm','client'),strict=True)]
    post(flow,'rm','/notes',{'revision':source.revision,'cues':reviewed,'command_id':str(uuid4())})
    post(flow,'rm','/generate-notes-draft',{'command_id':str(uuid4())})
    draft=view(flow,'rm')['draft'];actions=draft['content']['actions']
    agreed=[action for action in actions if action['commitment']=='agreed' and action['owner']!='unassigned']
    conditional=[action for action in actions if action['commitment']=='conditional']
    assert {action['owner'] for action in agreed}=={'rm','client'} and conditional
    body=approve_body(draft);assert post(flow,'rm','/approve-minutes',body)==post(flow,'rm','/approve-minutes',body)
    assert view(flow,'client')['shared_recap'] is None
    invalid=flow.http.post(f'/meetings/{flow.meeting}/followups',headers=headers(flow,'rm'),json={**approve_body(draft),'action_ids':[action['action_id'] for action in conditional]})
    assert invalid.status_code==422
    post(flow,'rm','/followups',{**approve_body(draft),'action_ids':[action['action_id'] for action in agreed]})
    post(flow,'rm','/share-recap',approve_body(draft))
    for action in agreed:
        post(flow,action['owner'],f'/followups/{action["action_id"]}/complete',{'evidence':'Synthetic replay action completed','command_id':str(uuid4())})
    assert view(flow,'rm')['open_followups']==0
    assert len(view(flow,'client')['followups'])==1 and view(flow,'client')['shared_recap']
