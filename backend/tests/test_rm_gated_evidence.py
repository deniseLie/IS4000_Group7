"""Consent-gated RM reads (0106, 0107) become cited evidence or an explicit gap, never an empty record."""
import inspect
from app.services.agents.registry import RM_CALENDAR,RM_CLIENT,SPECS,TOOL_PERMISSION
from app.services.rm import evidence as ev
from app.services.tools import TOOLS

GAP={'denied':False,'shared':False,'category':'transaction_insights','customer_id':1007,'call_id':'c1','as_of':'2026-11-05T09:00:00+08:00','data':None}
TOPICS={**GAP,'category':'chat_topics','data':[]}
POINT={'point_id':'00000000-0000-4000-8000-100700000081','topic':'investments','stage':'exploring','timeframe':'within_3m','concerns':['fees'],
    'origin':'confirmed','first_seen':'2026-11-02','last_seen':'2026-11-04'}
GATED=('rm_transaction_insights','rm_talking_points')


def test_a_read_the_client_has_not_shared_is_a_consent_gap_not_an_empty_record() -> None:
    for build,source in [(ev.cash_evidence,'cash-events'),(ev.insights_evidence,'transaction-insights')]:
        item=build(GAP,1007)
        assert (item.id,item.kind,item.data['shared'])==(source,'consent',False)
        assert 'transaction insights' in item.data['meaning'] and 'large_movements' not in item.data
    item=ev.talking_points_evidence(TOPICS,1007)
    assert item.kind=='consent' and 'topics from their chats' in item.data['meaning'] and item.data['shared_by_client']==[]


def test_topics_she_shared_herself_still_reach_the_rm_without_the_chat_topics_grant() -> None:
    inferred={**POINT,'point_id':'00000000-0000-4000-8000-100700000082','topic':'home_loan','origin':'inferred'}
    item=ev.talking_points_evidence({**TOPICS,'data':[POINT,inferred,{**POINT,'quote':'I may invest my bonus'}]},1007)
    assert item.kind=='consent' and item.data['withheld']==2  # the inferred topic and the row with client text never show
    assert item.data['shared_by_client']==[{'topic':'investments','line':'Investments · exploring · within 3 months · asks about fees · shared by the client',
        'label':'client_said','first_seen':'2 Nov 2026','last_seen':'4 Nov 2026'}]
    assert 'I may invest' not in item.text


def test_shared_reads_keep_their_records() -> None:
    rows=[{'date_text':'22 Sep 2026','amount':120000,'currency':'SGD','direction':'incoming','recurring':False}]
    assert ev.cash_evidence({**GAP,'shared':True,'data':rows},1002).data==rows
    assert ev.cash_evidence({'data':[],'as_of':None},1002).data['large_movements']==0  # an ungated result still reads as before
    insights=ev.insights_evidence({**GAP,'shared':True,'grant_id':'g1','data':{'days':90,'monthly':[]}},1002)
    assert (insights.kind,insights.title)==('record','Transaction insights for the last 90 days')
    assert ev.talking_points_evidence({**TOPICS,'shared':True},1002).data['points']==0
    shared=ev.talking_points_evidence({**TOPICS,'shared':True,'data':[{**POINT,'origin':'inferred'}]},1002)
    assert shared.kind=='record' and shared.data['points'][0]['label']=='ai_suggestion' and 'withheld' not in shared.data


def test_the_new_rm_reads_are_registered_tools_of_their_agents() -> None:
    for name,spec in (('rm_transaction_insights',RM_CLIENT),('rm_talking_points',RM_CLIENT),('rm_meeting_requests',RM_CALENDAR)):  # build plan 3a
        assert name in TOOLS and name in TOOL_PERMISSION and name in spec.tools
    assert RM_CALENDAR.tools['rm_meeting_requests'].free_text  # purposes are the client's own words


def test_code_sets_the_purpose_of_a_gated_read_and_no_tool_loop_reaches_one() -> None:
    for name in GATED:
        assert inspect.signature(TOOLS[name]).parameters['purpose'].default is inspect.Parameter.empty
    assert all(not spec.loop_tools&set(GATED) for spec in SPECS.values())
