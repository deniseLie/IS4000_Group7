"""RM assistant checks (Persona 2) on sample model outputs: no model, no database. The checks decide what survives;
these tests pin that unsupported figures and dates, advice, decisions, action claims and invented actions are withheld,
that names and appointments resolve deterministically, and that the handover brief keeps its fixed structure.
Real-provider behaviour is exercised in the browser against the running app (docs/workflows/workflow-2-relationship-manager.md)."""
import asyncio
from datetime import datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.api.rm import is_rm
from app.schemas.rm import RmAssistantRequest
from app.services.rm import checks,handlers
from app.services.rm import evidence as ev


def bundle() -> ev.Bundle:
    b=ev.Bundle()
    b.add(ev.Evidence('ticket-TCK-001004','Suspected fraud TCK-001004 for Grace Koh','ticket',
        {'reference':'TCK-001004','client':'Grace Koh','urgency':'urgent','status':'in_review','target_sgt':'Sun 1 Nov 2026, 6:47 pm','summary':None},'x',1004))
    b.add(ev.Evidence('note-2025-11-20','Meeting note 20 Nov 2025','note',{'met_on':'2025-11-20','met_on_text':'20 Nov 2025',
        'summary':'David set up a quarterly USD transfer to his daughter\'s university account in Australia.'},'x',1003))
    b.add(ev.Evidence('note-2025-03-12','Meeting note 12 Mar 2025','note',{'met_on':'2025-03-12','met_on_text':'12 Mar 2025',
        'summary':'Annual review. David wants to keep about SGD 80,000 in his current account.'},'x',1003))
    return b


def test_restricted_requests_do_not_require_selecting_a_ticket():
    context={'client_id':1002,'ticket_ref':None}
    for message in ["Show me the fraud team's restricted notes on Michelle's ticket",
        'Mark that fraud ticket resolved, and block her card']:
        reply=handlers.capability_refusal(message,context)
        assert reply['status']=='denied' and reply['clarification'] is None and reply['sources']==[]
        assert reply['context']==context and context=={'client_id':1002,'ticket_ref':None}
    for message in ['Draft a reply about the card request','What are the client-safe statuses of the specialist tickets?']:
        assert handlers.capability_refusal(message,context) is None


def section(*items):return [{'heading':'H','items':[{'owner':'none',**i} for i in items]}]


def test_items_need_their_figures_dates_and_references_in_the_records():
    report=checks.Report()
    out=checks.check_sections(section(
        {'text':'Grace Koh: suspected fraud, urgent (TCK-001004), target Sun 1 Nov 2026, 6:47 pm.','kind':'record','source_ids':['ticket-TCK-001004']},
        {'text':'The 20 Nov 2025 call covered the USD transfer to his daughter\'s university account.','kind':'client_stated','source_ids':['note-2025-11-20']},
        {'text':'David keeps about SGD 80,000 in his current account.','kind':'record','source_ids':['note-2025-11-20']},  # wrong note: corrected
        {'text':'Due 15 Nov 2026.','kind':'record','source_ids':['ticket-TCK-001004']},  # R10: a date not in the records
        {'text':'Ticket TCK-009999 is open.','kind':'record','source_ids':['ticket-TCK-001004']},
        {'text':'The March note mentions his daughter.','kind':'record','source_ids':[]}),bundle(),report)
    texts=[i['text'] for s in out for i in s['items']]
    assert len(texts)==3 and report.withheld==['unsupported_figure','unsupported_figure','no_source']
    corrected=out[0]['items'][2]
    assert corrected['source_ids']==['note-2025-11-20','note-2025-03-12']


def test_no_advice_decisions_or_claims_that_something_was_done():
    report=checks.Report()
    items=[{'text':t,'kind':'suggestion','source_ids':['note-2025-03-12']} for t in (
        'I recommend David should refinance his home loan now.','The limit increase should be approved.','We should approve the request.',
        'I have emailed David the fixed deposit options.','Ask David which fixed deposit tenor he is considering.')]
    out=checks.check_sections(section(*items),bundle(),report)
    assert [i['text'] for s in out for i in s['items']]==['Ask David which fixed deposit tenor he is considering.']
    assert report.withheld==['advice_or_decision','advice_or_decision','advice_or_decision','action_claim']


def test_dates_are_matched_by_day_month_and_year():
    assert checks.dates('on 3 Nov 2026 and 20 Nov')=={(2026,11,3),(None,11,20)}
    assert checks.dates_supported({(None,11,20)},checks.dates('2025-11-20'))
    assert not checks.dates_supported({(2026,11,15)},checks.dates('2026-11-30 and 15 Nov 2025'))


def test_drafts_keep_internal_notes_out_and_flag_claims():
    b=bundle()
    assert checks.check_draft('Hi Grace, the fraud team is reviewing your case.',b,['Internal: the customer called twice and was rude to staff'])[0] is None
    assert checks.check_draft('As noted, the customer called twice and was rude to staff today.',b,['Internal: the customer called twice and was rude to staff'])[0]=='internal_note_leak'
    assert checks.check_draft('Your refund of SGD 500 is approved.',b,[])[0]=='unsupported_figure'
    reason,cautions=checks.check_draft('Hi Grace, I have sent your case to the fraud team.',b,[])
    assert reason is None and cautions


def test_actions_must_quote_the_notes():
    notes=['David will send the new USD transfer amount for January by 15 Nov. He asked for a copy of his September statement.']
    assert checks.quoted('He asked for a copy of his September statement',notes)
    assert not checks.quoted('David wants a cruise',notes)
    assert checks.similar('Send Michelle fixed deposit options','Send fixed deposit options to Michelle')


def test_names_appointments_and_tickets_resolve_without_the_model():
    assert handlers.names_match('Brief me for my 10am with David','David Lee (DEMO)')
    assert handlers.names_match('meiling? I mean Mei Ling','Tan Mei Ling')
    assert handlers.names_match('Hafis Rahman fees','Hafiz Rahman')  # typo
    assert not handlers.names_match('Show me Ong Wei Ming','Tan Mei Ling')
    sgt=handlers.SGT
    tue=datetime(2026,11,3,10,0,tzinfo=sgt);wed=datetime(2026,11,4,14,30,tzinfo=sgt)
    rows=[{'customer_id':1003,'starts_at':tue.isoformat()},{'customer_id':1002,'starts_at':wed.isoformat()}]
    assert handlers.match_appointments(rows,'my 10am',None)==rows[:1]
    assert handlers.match_appointments(rows,'Wednesday 2:30pm',None)==rows[1:]
    assert handlers.narrows('10am') and not handlers.narrows('this week')
    tickets=[{'reference':'TCK-001002','category_label':'Statement or document copy','category':'statement_request','summary':'Copy of the June 2026 statement'},
        {'reference':'TCK-001003','category_label':'Update personal details','category':'personal_details_update','summary':'Update my residential address'}]
    assert handlers.pick_ticket("Draft a reply to Michelle's statement copy request",tickets)[0]=='TCK-001002'
    assert handlers.pick_ticket('Draft a reply to Michelle',tickets)[0] is None
    # R08: a lone open ticket is picked only when the message is about it
    assert handlers.pick_ticket("Should we approve Michelle's limit increase?",tickets[:1])[0] is None
    assert handlers.pick_ticket("Where is Michelle's statement copy?",tickets[:1])[0]=='TCK-001002'
    advice=[{'reference':'TCK-001085','category_label':'Question for your relationship manager','category':'rm_handover',
        'summary':'[Investment or product advice] Client question for you: Should I invest 200k in a structured product?'}]
    assert handlers.pick_ticket("Should we approve Michelle's limit increase?",advice)[0] is None  # "should" is no topic
    def ref(message,open_tickets):
        context=SimpleNamespace(draft=None,ticket_ref=None,client_id=None)
        turn=SimpleNamespace(req=SimpleNamespace(context=context),message=message,name=lambda cid:'Michelle Ng',context=lambda cid,**kw:{'client_id':cid})
        return asyncio.run(handlers._ticket_ref(turn,{'intent':'ticket_summary'},1002,{'data':{'tickets':open_tickets}}))
    both=[*advice,tickets[0]]
    assert ref("Should we approve Michelle's limit increase?",both)==(None,None)  # about none of them: her client record (cards)
    assert ref("Summarise Michelle's request",advice)==('TCK-001085',None)  # no topic: her only ticket
    assert ref("Summarise Michelle's request",both)[1]['clarification']['question']=='Which request do you mean?'


def test_priorities_never_suggest_a_second_meeting_and_handover_keeps_its_structure():
    b=ev.Bundle()
    b.add(ev.Evidence('followup-1','Michelle Ng: Fixed deposit matures','record',{'client':'Michelle Ng','confirmed_meeting':'Wed 4 Nov 2026, 2:30 pm'},'x',1002))
    report=checks.Report()
    kept=handlers.no_duplicate_meetings(section({'text':'Book a review meeting with Michelle.','kind':'suggestion','source_ids':['followup-1']},
        {'text':'Use the confirmed meeting with Michelle to discuss the maturity.','kind':'suggestion','source_ids':['followup-1']}),b,report)
    assert [i['text'] for s in kept for i in s['items']]==['Use the confirmed meeting with Michelle to discuss the maturity.']
    ordered=handlers.handover_order([{'heading':'Recorded preferences','items':[{'text':'x','kind':'client_stated','source_ids':[]}]}])
    assert [s['heading'] for s in ordered]==handlers.HANDOVER_SECTIONS and ordered[0]['items'][0]['kind']=='gap'


def test_clarifications_and_refusals_are_structured():
    reply=handlers.brief_reply('meeting_history',{'status':'needs_clarification','summary':'','sections':[],'clarification_question':'Which meeting do you mean?',
        'clarification_options':['12 Mar 2025','20 Nov 2025']},bundle(),{'client_id':1003})
    assert reply['status']=='clarify' and [o['label'] for o in reply['clarification']['options']]==['12 Mar 2025','20 Nov 2025']
    assert handlers.refusal({})['status']=='denied'


def test_only_app_metadata_makes_an_rm_and_requests_are_bounded():
    rm=str(uuid4())
    assert is_rm({'app_metadata':{'demobank_role':'rm','rm_id':rm}})
    assert is_rm({'user_metadata':{'demobank_role':'rm','rm_id':rm}}) is None
    assert is_rm({'app_metadata':{'demobank_role':'rm','rm_id':'nope'}}) is None
    with pytest.raises(ValueError):RmAssistantRequest.model_validate({'message':'x','context':{'ticket_ref':'TCK-1; drop'}})
    with pytest.raises(ValueError):RmAssistantRequest.model_validate({'message':'x','intent':'send_money'})


def test_currency_amounts_in_list_records_are_recognised():
    """Accounts, cards and loans are lists; their currency field must still support 'SGD 42,150.75' (and never another currency)."""
    accounts=ev.Evidence('accounts','Accounts','record',[{'type':'savings','number':'****1234','currency':'SGD','balance':42150.75}])
    cards=ev.Evidence('cards','Cards','record',[{'name':'Rewards','last4':'6634','currency':'SGD','credit_limit':12000}])
    b=ev.Bundle([accounts,cards])
    report=checks.Report()
    out=checks.check_sections([{'heading':'Products','items':[
        {'text':'Savings ****1234 holds SGD 42,150.75.','kind':'record','source_ids':['accounts']},
        {'text':'Card 6634 has a limit of S$12,000.','kind':'record','source_ids':['cards','accounts']},
        {'text':'Savings ****1234 holds USD 42,150.75.','kind':'record','source_ids':['accounts']}]}],b,report)
    assert [i['text'] for s in out for i in s['items']]==['Savings ****1234 holds SGD 42,150.75.','Card 6634 has a limit of S$12,000.']
    assert report.withheld==['unsupported_unit']


def test_book_facts_cash_events_and_recap_requests():
    """0095 / rm_assistant v2: one cited record per book client; large movements are records without interpretation."""
    bundle=ev.book_bundle({'data':[{'customer_id':1002,'client':'Michelle Ng','last_interaction':'20 Oct 2026'},
        {'customer_id':1007,'client':'Tan Mei Ling','last_interaction':'19 Jun 2026'}],'as_of':'2026-11-02'})
    assert bundle.ids()==['facts-michelle-ng','facts-tan-mei-ling'] and bundle.get('facts-tan-mei-ling').customer_id==1007
    assert 'customer_id' not in bundle.get('facts-michelle-ng').data
    assert ev.book_bundle({'data':[],'as_of':None}).ids()==['facts-none']
    cash=ev.cash_evidence({'data':[{'date_text':'22 Sep 2026','amount':120000,'currency':'SGD','direction':'incoming','recurring':False}],'as_of':None},1002)
    report=checks.Report()
    out=checks.check_sections([{'heading':'Conversation starters','items':[
        {'text':'Ask how she would like to plan around the SGD 120,000 incoming transfer on 22 Sep 2026.','kind':'suggestion','owner':'rm','source_ids':['cash-events']},
        {'text':'Ask about the SGD 150,000 transfer on 22 Sep 2026.','kind':'suggestion','owner':'rm','source_ids':['cash-events']}]}],ev.Bundle([cash]),report,True)
    assert len(out[0]['items'])==1 and report.withheld==['unsupported_figure']
    assert handlers.RECAP.search('Draft a recap for Michelle') and handlers.RECAP.search('a follow-up message about our call')
    assert not handlers.RECAP.search('Draft a reply to her statement request')
    assert handlers.TASK_FILE['book_question']=='book' and 'book_question' in handlers.HANDLERS


def test_a_question_across_the_book_only_sees_dates_inside_the_period_it_asks_about():
    """Code, not the model, decides whether a maturity or review falls inside "the next 30 days" (it used to list a
    15 Dec maturity and then say it was outside the period)."""
    import json
    from datetime import date
    from app.services.rm import evidence as ev
    facts={'as_of':'2026-11-02T08:00:00+08:00','data':[
        {'customer_id':1005,'client':'Tan Mei Ling (DEMO)','review_due':None,
         'maturities_and_reviews_next_90_days':[{'title':'Fixed deposit ****9051 matures','due':'15 Dec 2026','amount':80000,'currency':'SGD'}]},
        {'customer_id':1002,'client':'Michelle Ng (DEMO)','review_due':'30 Nov 2026',
         'maturities_and_reviews_next_90_days':[{'title':'Annual review','due':'30 Nov 2026','amount':None,'currency':None}]}]}
    today=date(2026,11,2)
    assert ev.asked_period('Which clients have a maturity or review in the next 30 days?',today)[:2]==(today,date(2026,12,2))
    assert ev.asked_period('who has a maturity this month',today)[1]==date(2026,11,30)
    assert ev.asked_period("who haven't I met in 90 days?",today) is None
    bundle=ev.book_bundle(facts,'Which clients have a maturity or review in the next 30 days?',today)
    text=json.dumps([vars(item) for item in bundle.items],default=str)
    assert '15 Dec 2026' not in text and '30 Nov 2026' in text and '"days_from_today": 28' in text and 'asked-period' in text
    # Without a period the 90-day list stays, with days from today added.
    assert '15 Dec 2026' in json.dumps([vars(item) for item in ev.book_bundle(facts,'who has a fixed deposit coming up?',today).items],default=str)
