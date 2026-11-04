"""Consent-gated RM reads (0106) become cited evidence or an explicit gap, never an empty record."""
from app.services.agents.registry import RM_INSIGHT,TOOL_PERMISSION
from app.services.rm import evidence as ev
from app.services.tools import TOOLS

GAP={'denied':False,'shared':False,'category':'transaction_insights','customer_id':1007,'call_id':'c1','as_of':'2026-11-05T09:00:00+08:00','data':None}


def test_a_read_the_client_has_not_shared_is_a_consent_gap_not_an_empty_record() -> None:
    for build,source in [(ev.cash_evidence,'cash-events'),(ev.insights_evidence,'transaction-insights')]:
        item=build(GAP,1007)
        assert (item.id,item.kind,item.data['shared'])==(source,'consent',False)
        assert 'transaction insights' in item.data['meaning'] and 'large_movements' not in item.data
    item=ev.talking_points_evidence({**GAP,'category':'chat_topics'},1007)
    assert item.kind=='consent' and 'topics from their chats' in item.data['meaning']


def test_shared_reads_keep_their_records() -> None:
    rows=[{'date_text':'22 Sep 2026','amount':120000,'currency':'SGD','direction':'incoming','recurring':False}]
    assert ev.cash_evidence({**GAP,'shared':True,'data':rows},1002).data==rows
    assert ev.cash_evidence({'data':[],'as_of':None},1002).data['large_movements']==0  # an ungated result still reads as before
    insights=ev.insights_evidence({**GAP,'shared':True,'grant_id':'g1','data':{'days':90,'monthly':[]}},1002)
    assert (insights.kind,insights.title)==('record','Transaction insights for the last 90 days')
    assert ev.talking_points_evidence({**GAP,'shared':True,'category':'chat_topics','data':[]},1002).data['points']==0


def test_the_new_rm_reads_are_registered_tools_of_the_client_agent() -> None:
    for name in ('rm_transaction_insights','rm_talking_points','rm_meeting_requests'):
        assert name in TOOLS and name in TOOL_PERMISSION and name in RM_INSIGHT.tools
    assert RM_INSIGHT.tools['rm_meeting_requests'].free_text  # purposes are the client's own words
