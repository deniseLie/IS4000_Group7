"""Meeting seam (0105): the shared models in app/schemas/meetings.py round-trip, reject unknown fields and keep their
rules, match the names in the migration, and the meetings API refuses while MEETINGS_ENABLED is false.
Both sides (client/consent/copilot and meeting lifecycle) keep this file green."""
import asyncio
import re
from pathlib import Path
from typing import get_args
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.core.config import Settings, settings
from app.schemas.contracts import Principal, RequestContext
from app.schemas.meetings import (ActionItem, BriefCard, BriefDocument, BriefItem, BriefSection, ConsentCategory, Decision,
    MeetingEvent, MeetingEventType, MeetingRef, MeetingStatus, MinutesDraft, TalkingPoint, TalkingTopic)
from app.services import meetings

U=str(uuid4())
WHEN='2026-11-11T06:30:00+00:00'
ITEM={'label':'record','text':'Compliance review due 30 Nov 2026; proof of address sent to the bank.','sources':[f'rm.client_profile:{U}']}
GAP={'label':'gap','text':'Chat topics: not shared by the client.'}
SECTION={'heading':'Compliance review','items':[ITEM,GAP]}
DOCUMENT={'document_id':U,'kind':'term_sheet','title':'Home loan: indicative terms','visibility':'internal','status':'draft'}
ACTION={'text':'Send the home loan application form','owner':'rm','due':'2026-11-14','quote':"I'll send you the application form by Friday",'at_seconds':754}
POINT={'point_id':U,'topic':'home_loan','stage':'planning','timeframe':'within_12m','concerns':[],'origin':'inferred',
    'first_seen':'2026-11-02','last_seen':'2026-11-05'}
EXAMPLES={
    MeetingRef:{'meeting_id':U,'customer_id':1002,'advisor_id':U,'starts_at':WHEN,'ends_at':'2026-11-11T07:00:00+00:00','format':'video',
        'purpose':'Home loan options','topics':['home_loan','compliance_review']},
    MeetingEvent:{'event_id':7,'type':'meeting.scheduled','meeting_id':U,'payload':{'starts_at':WHEN,'format':'video'},'dedupe_key':f'command:{U}','created_at':WHEN},
    BriefItem:ITEM,
    BriefSection:SECTION,
    BriefDocument:DOCUMENT,
    BriefCard:{'meeting_id':U,'version':1,'status':'draft','sections':[SECTION],'documents':[DOCUMENT],
        'consent':{'transaction_insights':True,'chat_topics':False},'withheld':1,'as_of':WHEN},
    ActionItem:ACTION,
    MinutesDraft:{'meeting_id':U,'version':1,'status':'draft','source':'transcript','summary':'Discussed home loan options and the open compliance review.',
        'decisions':['Michelle will apply after her review is complete.'],'action_items':[ACTION],'client_recap':None,'withheld':0},
    Decision:{'outcome':'changes','note':'Add a question about the sale agreement.'},
    MeetingStatus:{'meeting_id':U,'stage':'brief_review','updated_at':WHEN,'waiting_for':'rm','brief_version':1,'minutes_version':None,'error_code':None},
    TalkingPoint:POINT,
}


@pytest.mark.parametrize('model,data',EXAMPLES.items(),ids=lambda x:getattr(x,'__name__',''))
def test_examples_round_trip(model,data):
    obj=model.model_validate(data)
    assert model.model_validate_json(obj.model_dump_json())==obj


@pytest.mark.parametrize('model,data',[
    (MeetingEvent,{**EXAMPLES[MeetingEvent],'type':'meeting.started'}),
    (MeetingEvent,{**EXAMPLES[MeetingEvent],'client_text':'hello'}),
    (BriefItem,{'label':'record','text':'A fact with no source'}),
    (BriefCard,{**EXAMPLES[BriefCard],'consent':{'transactions':True}}),
    (BriefCard,{**EXAMPLES[BriefCard],'version':0}),
    (ActionItem,{**ACTION,'quote':''}),
    (Decision,{'outcome':'changes'}),
    (MeetingStatus,{**EXAMPLES[MeetingStatus],'error_code':'Provider down'}),
    (TalkingPoint,{**POINT,'quote':'I want to buy a condo next year'}),
    (TalkingPoint,{**POINT,'topic':'term_sheet'}),
    (TalkingPoint,{**POINT,'concerns':['rates','rates']}),
    (TalkingPoint,{**POINT,'last_seen':'2026-11-01'}),
],ids=['event_type','extra_field','uncited_record','consent_category','version','empty_quote','changes_without_note','error_code',
    'point_text','point_topic','point_concern_twice','point_dates'])
def test_rejects_unknown_fields_values_and_broken_rules(model,data):
    with pytest.raises(ValidationError):model.model_validate(data)


def test_gaps_need_no_source():
    assert BriefItem.model_validate(GAP).sources==[]


def test_names_match_the_migration():
    folder=Path(__file__).parents[1]/'supabase'/'migrations'
    sql=(folder/'0105_meeting_seam.sql').read_text()
    quoted=lambda text:set(re.findall(r"'([a-z_.]+)'",text))
    assert quoted(re.search(r'event_type in \(([^)]*)\)',sql)[1])==set(get_args(MeetingEventType))
    assert quoted(re.search(r'p_category not in \(([^)]*)\)',sql)[1])==set(get_args(ConsentCategory))
    assert quoted(re.search(r'unnest\(array\[([^\]]*)\]\)',sql)[1])==set(get_args(ConsentCategory))
    topics=re.search(r'function rm\.meeting_topics\(\).*?array\[([^\]]*)\]',(folder/'0106_meeting_requests.sql').read_text(),re.S)[1]
    assert set(get_args(TalkingTopic))<=quoted(topics)  # one topic list; talking points use its product codes


def test_a_talking_point_carries_no_client_text_and_renders_one_line():
    assert set(TalkingPoint.model_fields)=={'point_id','topic','stage','timeframe','concerns','origin','first_seen','last_seen'}
    inferred=TalkingPoint.model_validate(POINT)
    assert inferred.line()=='Property financing · planning · within 12 months · inferred from chat' and inferred.brief_label()=='ai_suggestion'
    shared=TalkingPoint.model_validate({**POINT,'topic':'fixed_deposit','stage':'concern','timeframe':'unknown','concerns':['rates','fees'],'origin':'confirmed'})
    assert shared.line()=='Time deposit · concern · asks about rates, fees · shared by the client' and shared.brief_label()=='client_said'


def test_meetings_are_off_by_default_and_every_call_says_so(monkeypatch):
    assert Settings.model_fields['meetings_enabled'].default is False
    monkeypatch.setattr(settings,'meetings_enabled',False)
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='customer'))
    event=MeetingEvent.model_validate(EXAMPLES[MeetingEvent])
    decision=Decision(outcome='approve')
    for call in (meetings.on_event(event),meetings.prepare(uuid4(),ctx),meetings.decide(uuid4(),'brief',decision,ctx),meetings.status(uuid4(),ctx)):
        with pytest.raises(meetings.MeetingsUnavailable,match='switched off'):asyncio.run(call)
    assert meetings.MeetingsUnavailable.code=='meetings_unavailable'
