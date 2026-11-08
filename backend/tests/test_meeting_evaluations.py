"""Opt-in real native API + Codex writer + distinct-model judge. Exports synthetic rows only, never hosted records."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
import pytest
from tests.test_meeting_lifecycle import flow,start,post,view,headers
from evaluation.meetings import EvaluationRow,cases,checks,cues_for,judge
from app.schemas.meeting_lifecycle import MinutesContent
from app.services.llm_gateway import model_name
from app.services.prompts import load
from app.services.meetings.writer import PROMPT

pytestmark=pytest.mark.skipif(os.getenv('TEST_MEETING_EVAL_LIVE')!='1',reason='Real synthetic Codex meeting evaluation opt-in required')


def store(row):
    if destination:=os.getenv('TEST_MEETING_EVAL_OUTPUT'):
        path=Path(destination);previous=json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps([item for item in previous if item['case']['id']!=row.case.id]+[row.model_dump(mode='json')],indent=2))


@pytest.mark.parametrize('case',cases(),ids=lambda case:case.id)
def test_synthetic_meeting_quality_through_the_real_api_and_independent_judge(flow,case,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');start(flow,'client')
    post(flow,'rm','/notes',{'revision':1,'cues':[item.model_dump(mode='json') for item in cues_for(case)],'command_id':str(uuid4())})
    response=flow.http.post(f'/meetings/{flow.meeting}/generate-notes-draft',headers=headers(flow,'rm'),json={'command_id':str(uuid4())},timeout=300)
    content=MinutesContent.model_validate(view(flow,'rm')['draft']['content']) if response.status_code==200 else None
    result=checks(case,content) if content else ()
    judge_requested=content is not None and os.getenv('TEST_MEETING_JUDGE')=='1'
    row=EvaluationRow(case=case,content=content,error='judge_not_completed' if judge_requested else None if content else str(response.status_code)+': '+response.text,
        checks=result,judge=None,producer=model_name(),prompt_id=load('minutes_writer',PROMPT)['id'],judge_model=os.getenv('JUDGE_MODEL') if judge_requested else None)
    store(row)  # A timeout must replace an older passing result before the external judge starts.
    graded=asyncio.run(judge(case,content)) if judge_requested else None
    store(row.model_copy(update={'judge':graded,'error':None if content else row.error}))
    assert response.status_code==200,response.text
    assert all(item.score==1 for item in result),[(item.key,item.comment) for item in result if item.score!=1]
    assert graded is None or min(graded.faithfulness,graded.privacy,graded.actions,graded.usefulness)==1,graded
