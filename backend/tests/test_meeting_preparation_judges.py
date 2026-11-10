"""Real native preparation agents and a distinct Codex judge; synthetic records only, opt-in publication separately."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
import httpx,psycopg,pytest
from tests.test_meeting_lifecycle import flow,headers,post
from tests.test_meeting_preparation import source_frame
from app.schemas.meeting_preparation import PreparedBrief
from app.schemas.meeting_packs import PackInputs,PackView
from app.services.agents.registry import PROPOSAL_DRAFTER
from app.services.llm_gateway import model_name
from app.services.prompts import load
from app.services.meetings import preparation
from app.services.meetings.preparation import PROMPT
from evaluation.meeting_preparation import JUDGE_RUBRIC_VERSION,BriefInput,PackInput,PreparationResult,brief_checks,pack_checks,judged_brief,judged_pack
from evaluation.meetings import Check

pytestmark=pytest.mark.skipif(os.getenv('TEST_MEETING_PREP_JUDGE')!='1',reason='Explicit real synthetic preparation/judge opt-in required')

@pytest.fixture
def rendered_frames(monkeypatch):
    """Grade the API's actual response against the same authorised read used to render it."""
    render=preparation.render
    frames={}
    def capture(frame,stored):
        prepared=render(frame,stored)
        frames[prepared.card.as_of]=frame
        return prepared
    monkeypatch.setattr(preparation,'render',capture)
    return frames


def opened(flow):
    purpose='Discuss fixed deposit options and outstanding review documents'
    with psycopg.connect(flow.uri) as db:db.execute("update rm.appointments set purpose=%s,topics=array['fixed_deposit','compliance_review'] where appointment_id=%s",(purpose,flow.meeting))
    post(flow,'client','/start',{'notice_id':str(flow.notice),'purpose':purpose,'outcome':'Clarify the available terms and next steps','command_id':str(uuid4())})


def recorded(row):
    path=Path(os.environ['TEST_MEETING_PREP_EVAL_OUTPUT']);before=json.loads(path.read_text()) if path.exists() else []
    current=row.model_copy(update={'judge_rubric_version':JUDGE_RUBRIC_VERSION})
    path.write_text(json.dumps([item for item in before if item['case_id']!=row.case_id]+[current.model_dump(mode='json')],indent=2))


def test_real_brief_writer_grounding_and_audience_judge(flow,monkeypatch,rendered_frames):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');opened(flow)
    response=flow.http.post(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'),timeout=240);assert response.status_code==200,response.text
    prepared=PreparedBrief.model_validate(response.json());frame=rendered_frames[prepared.card.as_of];checks=brief_checks(frame,prepared)
    attempt=PreparationResult(case_id='B01',agent='brief_writer',rubric='Prepare a cited internal RM brief relevant to the agreed fixed deposit and document agenda, using only currently authorised readers and labelled questions/gaps.',
        evidence=BriefInput(frame=frame,output=prepared),checks=checks,judge=None,error='judge_not_completed',producer=model_name(),prompt_id=load('brief_writer',PROMPT)['id'],judge_model=os.environ['JUDGE_MODEL'])
    recorded(attempt);judge=asyncio.run(judged_brief(frame,prepared,attempt.rubric));recorded(attempt.model_copy(update={'judge':judge,'error':None}))
    assert all(item.score==1 for item in checks)
    assert min(judge.faithfulness,judge.privacy,judge.actions,judge.usefulness)==1,judge


def test_real_proposal_drafter_catalogue_and_audience_judge(flow,local_data_api,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');opened(flow)
    amounts=[{'label':'Explicit RM discussion amount','currency':'SGD','amount':'6500.25'}]
    source=httpx.post(local_data_api.url+'/rpc/meeting_pack_inputs',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting),'p_amounts':amounts});assert source.status_code==200,source.text
    frame=PackInputs.model_validate(source.json())
    response=flow.http.post(f'/meetings/{flow.meeting}/pack/draft',headers=headers(flow,'rm'),json={'amounts':amounts,'command_id':str(uuid4())},timeout=240);assert response.status_code==200,response.text
    pack=PackView.model_validate(response.json());checks=pack_checks(frame,pack)
    attempt=PreparationResult(case_id='P01',agent='proposal_drafter',rubric='Draft a useful client-safe discussion pack with exact approved catalogue terms and the explicitly entered amount, without internal context, transaction execution or automatic sharing.',
        evidence=PackInput(frame=frame,output=pack),checks=checks,judge=None,error='judge_not_completed',producer=model_name(),prompt_id=load(PROPOSAL_DRAFTER.prompt,PROPOSAL_DRAFTER.version)['id'],judge_model=os.environ['JUDGE_MODEL'])
    recorded(attempt);judge=asyncio.run(judged_pack(frame,pack,attempt.rubric));recorded(attempt.model_copy(update={'judge':judge,'error':None}))
    assert all(item.score==1 for item in checks)
    assert min(judge.faithfulness,judge.privacy,judge.actions,judge.usefulness)==1,judge


def test_real_brief_after_withdrawal_uses_only_remaining_authorised_evidence(flow,local_data_api,monkeypatch,rendered_frames):
    monkeypatch.setenv('CHAT_TRANSPORT','codex');opened(flow);before=source_frame(flow,local_data_api)
    withheld=[item.text for item in before.sources if item.category in ('chat_topics','transaction_insights')]
    assert withheld
    with psycopg.connect(flow.uri) as db:
        for category in ('chat_topics','transaction_insights'):
            grant=before.grants_used[category]
            assert grant is not None
            db.execute('update consent.grants set revoked_at=clock_timestamp() where grant_id=%s',(grant,))
    response=flow.http.post(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'),timeout=240);assert response.status_code==200,response.text
    prepared=PreparedBrief.model_validate(response.json());frame=rendered_frames[prepared.card.as_of]
    assert not frame.consent['chat_topics'] and not frame.consent['transaction_insights']
    assert not any(item.category in ('chat_topics','transaction_insights') for item in frame.sources)
    assert not any(item.point.origin=='inferred' for item in frame.talking_points)
    maturity=next(section for section in prepared.card.sections if section.heading=='Fixed deposit maturity')
    assert all(item.text.startswith('Time deposit maturity: ') and 'Amount:' not in item.text and '****' not in item.text for item in maturity.items)
    checks=brief_checks(frame,prepared)+(Check(key='brief_revoked_records_withheld',score=int(not any(text in response.text for text in withheld)),comment='Withdrawn aggregate facts are absent from the regenerated RM brief'),)
    attempt=PreparationResult(case_id='B02',agent='brief_writer',rubric='Prepare a useful internal brief after chat-topic and transaction-insight consent is withdrawn. Retain current permitted records, show gaps, and do not reconstruct withdrawn facts or inferred interests.',
        evidence=BriefInput(frame=frame,output=prepared),checks=checks,judge=None,error='judge_not_completed',producer=model_name(),prompt_id=load('brief_writer',PROMPT)['id'],judge_model=os.environ['JUDGE_MODEL'])
    recorded(attempt);judge=asyncio.run(judged_brief(frame,prepared,attempt.rubric));recorded(attempt.model_copy(update={'judge':judge,'error':None}))
    assert all(item.score==1 for item in checks)
    assert min(judge.faithfulness,judge.privacy,judge.actions,judge.usefulness)==1,judge


def test_real_service_only_pack_has_practical_questions_without_a_product_pitch(flow,local_data_api,monkeypatch):
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    purpose='Clarify the next steps for the documents awaiting bank review'
    with psycopg.connect(flow.uri) as db:db.execute("update rm.appointments set purpose=%s,topics=array['compliance_review'] where appointment_id=%s",(purpose,flow.meeting))
    post(flow,'rm','/start',{'notice_id':str(flow.notice),'purpose':purpose,'outcome':'Agree what information is needed and who will follow up','command_id':str(uuid4())})
    source=httpx.post(local_data_api.url+'/rpc/meeting_pack_inputs',headers=headers(flow,'rm'),json={'p_meeting_id':str(flow.meeting),'p_amounts':[]});assert source.status_code==200,source.text
    frame=PackInputs.model_validate(source.json());assert not frame.catalogue
    response=flow.http.post(f'/meetings/{flow.meeting}/pack/draft',headers=headers(flow,'rm'),json={'amounts':[],'command_id':str(uuid4())},timeout=240);assert response.status_code==200,response.text
    pack=PackView.model_validate(response.json())
    checks=pack_checks(frame,pack)+(Check(key='service_only_no_products',score=int(not pack.content.products and not pack.content.stated_amounts),comment='A document-service agenda does not invent a product or discussion amount'),)
    attempt=PreparationResult(case_id='P02',agent='proposal_drafter',rubric='Prepare a useful client-safe document-service meeting pack with practical questions and next steps, without inventing products, amounts, bank acceptance, or private compliance findings.',
        evidence=PackInput(frame=frame,output=pack),checks=checks,judge=None,error='judge_not_completed',producer=model_name(),prompt_id=load(PROPOSAL_DRAFTER.prompt,PROPOSAL_DRAFTER.version)['id'],judge_model=os.environ['JUDGE_MODEL'])
    recorded(attempt);judge=asyncio.run(judged_pack(frame,pack,attempt.rubric));recorded(attempt.model_copy(update={'judge':judge,'error':None}))
    assert all(item.score==1 for item in checks)
    assert min(judge.faithfulness,judge.privacy,judge.actions,judge.usefulness)==1,judge


def test_real_briefing_route_opens_the_agreed_meeting_and_retains_the_recorded_collateral_alert(flow,monkeypatch,rendered_frames):
    monkeypatch.setenv('CHAT_TRANSPORT','codex')
    with psycopg.connect(flow.uri) as db:
        db.execute("update rm.appointments set purpose='Review the recorded portfolio and collateral shortfall and agree questions for the bank',topics=array['home_loan','investments'] where appointment_id=%s",(flow.meeting,))
        db.execute('select rm.demo_lombard_scenario()')
    initial=flow.http.get(f'/rm/meetings/{flow.meeting}/brief',headers=headers(flow,'rm'))
    assert initial.status_code==200 and initial.json()['brief'] is None,initial.text
    response=flow.http.post(f'/rm/meetings/{flow.meeting}/brief',headers=headers(flow,'rm'),json={'regenerate':False},timeout=240)
    assert response.status_code==200,response.text
    current=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'))
    assert current.status_code==200,current.text
    prepared=PreparedBrief.model_validate(current.json());frame=rendered_frames[prepared.card.as_of]
    assert response.json()['brief']['brief_id']==str(prepared.brief_id) and response.json()['brief']['version']==prepared.card.version
    for heading,prefix in (('Recorded collateral alert','Recorded Lombard borrowing:'),
        ('Recorded investment portfolio','Recorded investment portfolio:')):
        section=next(section for section in prepared.card.sections if section.heading==heading)
        source=next(source for source in frame.sources if source.text.startswith(prefix))
        assert any(item.text==source.text and item.sources==[source.reference] for item in section.items)
    timeframe=next(item for item in frame.talking_points if item.point.topic=='home_loan' and item.point.origin=='inferred')
    assert any(item.text==timeframe.point.line() and item.label=='ai_suggestion' and item.sources==[timeframe.reference]
        for section in prepared.card.sections for item in section.items)
    assert not flow.http.get(f'/meetings/{flow.meeting}',headers=headers(flow,'rm')).json()['recording_allowed']
    assert flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'client')).status_code==403
    assert timeframe.grant_id is not None
    with psycopg.connect(flow.uri) as db:
        db.execute('update consent.grants set revoked_at=clock_timestamp() where grant_id=%s',(timeframe.grant_id,))
    after=flow.http.get(f'/meetings/{flow.meeting}/preparation',headers=headers(flow,'rm'))
    assert after.status_code==200 and after.json()['sources_changed']
    assert timeframe.point.line() not in after.text
    checks=brief_checks(frame,prepared)
    attempt=PreparationResult(case_id='B03',agent='brief_writer',rubric='Prepare one cited internal RM briefing for an agreed portfolio and property-financing agenda. Retain the current recorded collateral shortfall without issuing or executing a bank credit decision; do not start capture or share the internal brief.',
        evidence=BriefInput(frame=frame,output=prepared),checks=checks,judge=None,error='judge_not_completed',producer=model_name(),prompt_id=load('brief_writer',PROMPT)['id'],judge_model=os.environ['JUDGE_MODEL'])
    recorded(attempt);judge=asyncio.run(judged_brief(frame,prepared,attempt.rubric));recorded(attempt.model_copy(update={'judge':judge,'error':None}))
    assert all(item.score==1 for item in checks)
    assert min(judge.faithfulness,judge.privacy,judge.actions,judge.usefulness)==1,judge
