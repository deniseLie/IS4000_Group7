"""Opt-in real native API + real writer + distinct-model judge. Exports synthetic rows only, never hosted records."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
import pytest
from tests.test_meeting_lifecycle import flow,start,post,view,headers
from evaluation.meetings import EvaluationRow,cases,checks,cues_for,judge
from app.schemas.meeting_lifecycle import MinutesContent
from app.services.llm_gateway import model_name,transport
from app.services.prompts import load
from app.services.meetings.writer import PROMPT

WRITER=os.getenv('TEST_MEETING_TRANSPORT','codex')  # the writer and judge's transport: codex, claude or api (OpenRouter)
pytestmark=pytest.mark.skipif(os.getenv('TEST_MEETING_EVAL_LIVE')!='1',reason='Real synthetic meeting evaluation opt-in required')


def store(row):
    if destination:=os.getenv('TEST_MEETING_EVAL_OUTPUT'):
        path=Path(destination);previous=json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps([item for item in previous if item['case']['id']!=row.case.id]+[row.model_dump(mode='json')],indent=2))


@pytest.mark.parametrize('case',cases(),ids=lambda case:case.id)
def test_synthetic_meeting_quality_through_the_real_api_and_independent_judge(flow,case,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT',WRITER);start(flow,'client')
    post(flow,'rm','/notes',{'revision':1,'cues':[item.model_dump(mode='json') for item in cues_for(case)],'command_id':str(uuid4())})
    response=flow.http.post(f'/meetings/{flow.meeting}/generate-notes-draft',headers=headers(flow,'rm'),json={'command_id':str(uuid4())},timeout=300)
    content=MinutesContent.model_validate(view(flow,'rm')['draft']['content']) if response.status_code==200 else None
    result=checks(case,content) if content else ()
    judge_requested=content is not None and os.getenv('TEST_MEETING_JUDGE')=='1'
    row=EvaluationRow(case=case,content=content,error='judge_not_completed' if judge_requested else None if content else str(response.status_code)+': '+response.text,
        checks=result,judge=None,producer=model_name(),prompt_id=load('minutes_writer',PROMPT)['id'],judge_model=os.getenv('JUDGE_MODEL') if judge_requested else None,transport=transport())
    store(row)  # A timeout must replace an older passing result before the external judge starts.
    graded=asyncio.run(judge(case,content)) if judge_requested else None
    store(row.model_copy(update={'judge':graded,'error':None if content else row.error}))
    assert response.status_code==200,response.text
    assert all(item.score==1 for item in result),[(item.key,item.comment) for item in result if item.score!=1]
    assert graded is None or min(graded.faithfulness,graded.privacy,graded.actions,graded.usefulness)==1,graded


def test_rehearsal_deadlines_survive_minutes_generation_and_persistence(flow,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT',WRITER);start(flow,'client')
    cues=[{'cue_id':'cue1','speaker':'rm','start_seconds':None,
        'text':'I will send the document checklist on Thursday, 19 November 2026.'},
        {'cue_id':'cue2','speaker':'client','start_seconds':None,
        'text':'I will upload synthetic proof of address on Friday, 20 November 2026.'}]
    post(flow,'rm','/notes',{'revision':1,'cues':cues,'command_id':str(uuid4())})
    response=flow.http.post(f'/meetings/{flow.meeting}/generate-notes-draft',headers=headers(flow,'rm'),json={'command_id':str(uuid4())},timeout=300)
    assert response.status_code==200,response.text
    actions=MinutesContent.model_validate(view(flow,'rm')['draft']['content']).actions
    assert {(item.owner,item.due_on.isoformat()) for item in actions if item.due_on}=={('rm','2026-11-19'),('client','2026-11-20')}
