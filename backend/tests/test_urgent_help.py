"""Workflow 1 (Alex, three problems in one message, impossible travel): deterministic rules and composition.

Sample drafts go straight through the validators; they never stand in for the real AI client.
Model planning and wording are covered by the opt-in real-provider browser and evaluation runs.
"""
import asyncio
import json
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
import pytest
from app.schemas.agents import Evidence
from app.schemas.chat import ChatRequest
from app.schemas.contracts import PlanTask,Principal,RequestContext,Task
from app.schemas.fraud import FraudProposal
from app.schemas.events import Source
from app.services import prompts, rm_handover
from app.services.agents import fraud,retail
from app.services.agents.answer import validate_draft
from app.services.agents.fraud import decision_for,evidence_for
from app.services.agents.fraud_records import eligible_proposals
from app.services.guardrail import assess_request
from tests import knowledge_fixtures
from app.services.orchestrator import agent_history,checked,overridden,priority,simple_task
from app.services.source_checks import check_sources,unit_matches
from tests.test_fraud import records

MESSAGE='theres a $2400 charge i dont recognise, my transfer to my landlord still says pending, and i got locked out of the app on my phone'


def personal(question: str=MESSAGE) -> ChatRequest:
    return ChatRequest(messages=[{'role':'user','content':question}],mode='personal')


@pytest.mark.parametrize('question',[MESSAGE,'theres a $2400 charge i dont recognise','i didnt make this payment',
    'I dont recognize this transaction','that charge is not mine','the payment on my card is not mine',
    'eh why is there a $2,400 charge?? i was in singapore the whole night','that charge was in LA, i wasnt even there'])
def test_informal_unrecognised_charges_are_flagged_before_any_model_call(question: str) -> None:
    risk=assess_request(personal(question))
    assert risk.priority=='P0' and 'fraud_suspected' in risk.risk_flags and risk.fixed_response is None


@pytest.mark.parametrize('question',['i got locked out of the app on my phone','my transfer to my landlord still says pending',
    'I recognise this payment','dont forget to make the payment','whats my credit card due date and min payment',
    'also i got a weird OTP last night and now my app is locked','my payment failed when i was at the mall'])
def test_other_workflow_questions_are_not_fraud(question: str) -> None:
    assert 'fraud_suspected' not in assess_request(personal(question)).risk_flags


def accounts(question: str,id_: str='t1',intent: str='open') -> PlanTask:
    return PlanTask(id=id_,agent='accounts',intent=intent,question=question)


def plan_for(tasks: list[PlanTask],message: str=MESSAGE) -> list[PlanTask]:
    request=personal(message)
    return sorted(checked(overridden(tasks,assess_request(request)),request),key=lambda t:t.priority)


def test_the_urgent_card_request_comes_first_and_the_lockout_goes_to_guide() -> None:
    """Build plan 2b: no Security agent. The disowned charge is the one P0 urgent task (fixed steps and an urgent RM request);
    the lockout is a Guide login_help task in message order (P1 is no longer used)."""
    issues=[accounts('my transfer to my landlord still says pending'),accounts('i got locked out of the app on my phone','t2'),
        accounts('whats my credit card due date','t3')]
    plan=plan_for(issues)
    assert [(t.agent,t.intent,t.priority) for t in plan]==[('relationship','open','P0'),('accounts','open','P3'),('guide','login_help','P3'),
        ('accounts','open','P3')]
    assert [t.question for t in plan]==[MESSAGE,*(issue.question for issue in issues)]
    assert plan[0].entities=={'urgent':True,'message':MESSAGE} and not any('urgent' in t.entities for t in plan[1:])
    routed=overridden([accounts('theres a $2400 charge i dont recognise'),*issues],assess_request(personal()))
    assert routed[0].entities['urgent'] is True and routed[0].question=='theres a $2400 charge i dont recognise' and priority(issues[0])=='P3'
    assert sum('urgent' in t.entities for t in routed)==1


def test_a_model_plan_cannot_make_a_task_urgent() -> None:
    """The planner's entity values are strings; only the rules set the bool marker that skips the model."""
    forged=PlanTask(id='t1',agent='relationship',intent='open',question='When is my next meeting?',entities={'urgent':'true'})
    plain=PlanTask(id='t1',agent='accounts',intent='open',question='What is my balance?')
    plan=checked(overridden([forged,plain.model_copy(update={'id':'t2'})],assess_request(personal('When is my next meeting? And my balance?'))),
        personal('When is my next meeting? And my balance?'))
    assert [t.priority for t in plan]==['P3','P3'] and not any(t.entities.get('urgent') is True for t in plan)


@pytest.mark.parametrize('claimed,recorded,expected',[(('$',2400),('SGD',2400),True),(('$',2400),('USD',2400),True),
    (('$',2400),('EUR',2400),False),(('$',2400),('SGD',-2400),False),(('SGD',2400),('USD',2400),False),(('SGD',5),('SGD',5),True)])
def test_bare_dollar_matches_only_dollar_currencies_with_the_same_value(claimed,recorded,expected) -> None:
    from decimal import Decimal
    assert unit_matches((claimed[0],Decimal(claimed[1])),(recorded[0],Decimal(recorded[1])))==expected


def test_dollar_answers_need_a_dollar_record_and_keep_strict_signs() -> None:
    record=json.dumps({'transactions':[{'amount':-2400.0,'currency':'SGD'}]})
    def check(answer: str,text: str) -> str | None:return check_sources(answer,['e'],[{'id':'e','text':text}])
    assert check('The charge is $-2,400.00.',record) is None
    assert check('The charge is $2,400.','Charge of SGD 2,400.') is None
    assert check('The charge is $2,400.','Charge of EUR 2,400.')=='unsupported_unit'
    assert check('The charge is $2,400.',record)=='unsupported_unit'
    assert check('SGD 100.','SGD -100.')=='unsupported_unit'


def test_demo_amount_finds_the_one_card_to_protect() -> None:
    value=records()
    expected=[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    assert eligible_proposals(MESSAGE,value)==expected
    assert eligible_proposals('theres a $2,400 charge i dont recognise',value)==expected
    assert eligible_proposals('a $64.20 charge',value)==[FraudProposal(card_id='card_10012',transaction_id='txn_100112')]
    blocked=value.model_copy(update={'cards':[card.model_copy(update={'status':'temporarily_blocked'}) for card in value.cards]})
    assert eligible_proposals(MESSAGE,blocked)==[]


def fraud_task() -> Task:
    return Task(task_id='t1',domain='fraud',intent='answer_question',entities={'question':'theres a $2400 charge i dont recognise'})


def fraud_draft(answer: str,proposals: list[dict[str,str]]) -> str:
    return json.dumps({'answer':answer,'source_ids':['customer-records'],'answer_kind':'banking','status':'needs_human',
        'clarification_question':'','proposals':proposals})


def test_flagged_charge_offers_the_single_eligible_block_without_a_model_proposal() -> None:
    evidence=[evidence_for(records())]
    pairs=[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    valid=fraud_draft('Contact your bank now about the synthetic charge of SGD -2,400.00.',[])
    assert decision_for(fraud_task(),valid,evidence,pairs,'test',flagged=True).proposal==pairs[0]
    assert decision_for(fraud_task(),valid,evidence,pairs,'test').proposal is None
    withheld=decision_for(fraud_task(),fraud_draft('The charge is EUR 9.',[]),evidence,pairs,'test',flagged=True)
    assert withheld.result.status=='error' and withheld.proposal==pairs[0] and withheld.result.text.startswith(fraud.URGENT)
    # CR12: several charges on ONE card ("the London charges") still give one offer (the newest); different cards do not.
    two=pairs+[FraudProposal(card_id='card_10011',transaction_id='txn_100113')]
    assert decision_for(fraud_task(),valid,evidence,two,'test',flagged=True).proposal==two[1]
    other=pairs+[FraudProposal(card_id='card_10012',transaction_id='txn_100113')]
    assert decision_for(fraud_task(),valid,evidence,other,'test',flagged=True).proposal is None


@pytest.mark.parametrize('claim',["I've reversed the charge.",'We have cancelled the transfer.','I have successfully disputed it.',
    'I have investigated the payment.','We have expedited your transfer.','I have canceled the payment.'])
def test_unsupported_action_claims_are_withheld(claim: str) -> None:
    evidence=knowledge_fixtures.evidence('transactions')
    cited=evidence[0].id
    task=Task(task_id='t2',domain='retail',intent='answer_question',entities={'question':'pending transfer'})
    raw=json.dumps({'answer':claim,'source_ids':[cited],'answer_kind':'banking','status':'ok','clarification_question':''})
    assert validate_draft(task,raw,evidence).status=='error'
    allowed=json.dumps({'answer':'Pending means processing is not complete; the bank can investigate an unfamiliar transaction.',
        'source_ids':[cited],'answer_kind':'banking','status':'ok','clarification_question':''})
    assert validate_draft(task,allowed,evidence).status=='ok'


def test_the_rm_hears_her_own_words_about_the_card_never_the_planners() -> None:
    """The urgent request's summary: the topic, what happened, her cards (last 4 and status) and her own redacted words."""
    summary=rm_handover.urgent_target('charge',PART_B,'card ending 4821 (active)')['summary']
    assert summary.startswith('[Urgent card] Charge the client does not recognise. Cards on record: card ending 4821 (active).')
    assert 'eh why is there a $2,400 charge' in summary and len(summary)<=rm_handover.SUMMARY_LIMIT


def test_personal_agents_use_the_v5_runner_and_the_moved_v4_prompts() -> None:
    from app.services import orchestrator
    from app.services.agents import retail,runner
    assert runner.PROMPTS=={'guide':'v2','accounts':'v1'} and orchestrator.PLANNER=='v10'
    assert retail.PROMPT=='v11'  # the computed-checks path keeps its released prompt
    assert set(runner.RULES)=={'guide','accounts','compliance','relationship'}


def retail_records(cards: list[dict[str,object]]):
    from app.schemas.retail import RetailRecords
    return RetailRecords.model_validate({'profile':{'name':'Alex Tan (DEMO)','preferred_language':'en','employment_status':'employed'},
        'accounts':[],'cards':cards,'loans':[],'transactions':[],'captured_at':datetime.now(timezone.utc)})


ALEX_CARD={'card_id':'card_10011','name':'Rewards Card','network':'visa','last4':'4821','status':'active','limit':8000.0,
    'outstanding':2464.2,'minimum_payment':50.0,'due_date':'2026-11-05'}
TUESDAY=date(2026,10,27)


def alex_retail():
    """Alex's retail snapshot after migration 0070 (migration 0004 seed)."""
    from app.schemas.retail import RetailRecords
    def row(txn: str,day: str,name: str,amount: float,status: str,card: bool) -> dict[str,object]:
        return {'txn_id':txn,'date':day,'description':name,'amount':amount,'currency':'SGD','status':status,
            'card_id':'card_10011' if card else None,'card_last4':'4821' if card else None,'is_overseas':card and name.isupper(),'failure_reason':None}
    return RetailRecords.model_validate({'profile':{'name':'Alex Tan (DEMO)','preferred_language':'en','employment_status':'employed'},
        'accounts':[{'account_id':'acc_10011','type':'savings','number':'****6012','currency':'SGD','balance':5230.45,'status':'active'}],
        'cards':[{**ALEX_CARD,'outstanding':2563.1}],'loans':[],'captured_at':datetime.now(timezone.utc),'transactions':[
            row('txn_100111','2026-10-17','TECHVAULT ELECTRONICS',-2400.0,'posted',True),row('txn_100116','2026-10-17','SUNSET GRILL LA',-86.4,'posted',True),
            row('txn_100112','2026-10-17','Transfer to landlord',-1800.0,'pending',False),
            row('txn_100115','2026-10-17','Hawker Delight, Tanjong Pagar',-12.5,'posted',True),row('txn_100114','2026-10-01','Employer Pte Ltd (DEMO)',5600.0,'posted',False)]})


def test_due_day_reader_resolves_the_customers_named_day_from_today() -> None:
    from app.services.agents.retail_checks import business_days,due_date
    assert due_date("my rent transfer still says pending and rent's due friday, will it make it?",TUESDAY)==date(2026,10,30)
    assert due_date('Will it go through by Friday when rent is due?',TUESDAY)==date(2026,10,30)
    assert due_date('it is due today',TUESDAY)==TUESDAY and due_date('due tomorrow',TUESDAY)==date(2026,10,28) and due_date('due tuesday',TUESDAY)==TUESDAY
    assert due_date('due 2026-11-02',TUESDAY)==date(2026,11,2) and due_date('due by 1 nov',TUESDAY)==date(2026,11,1)
    assert due_date('rent was due 20 oct',TUESDAY)==date(2026,10,20) and due_date('due 3 jan',TUESDAY)==date(2027,1,3)
    assert due_date('i paid it on friday',TUESDAY) is None and due_date('due soon',TUESDAY) is None and due_date('due 31 feb',TUESDAY) is None
    assert business_days(date(2026,10,17),TUESDAY)==7 and business_days(TUESDAY,TUESDAY)==0


def test_retail_checks_supply_the_deadline_and_fall_below_figures_so_the_model_computes_none() -> None:
    from decimal import Decimal
    from app.services.agents.retail_checks import checks
    records=alex_retail()
    evidence=checks(records,'my rent transfer still says pending, will it reach by friday?',TUESDAY,(Decimal('1000'),'SGD'))
    assert evidence is not None and evidence.id=='retail-checks' and 'txn_' not in evidence.text
    for fact in ['Today in Singapore is 2026-10-27 (Tuesday)','pending since 2026-10-17 (Saturday): 10 calendar days and 7 business days ago',
        "stated due day is 2026-10-30 (Friday), 3 days from today",'the balance would be SGD 3430.45','Both balances are above that threshold',
        'credit card ending 4821 (outstanding 2563.10 of a 8000.00 limit; card currency not recorded) and do not change a bank account balance']:
        assert fact in evidence.text
    cited=[{'id':evidence.id,'text':evidence.text}]
    answer=('Your SGD 1,800.00 transfer to your landlord has been pending since 2026-10-17, 7 business days ago, and rent is due on 2026-10-30, '
        'in 3 days. Do not send it again. If it completes, your savings balance would be SGD 3,430.45, above the SGD 1,000.00 fall-below threshold.')
    assert check_sources(answer,['retail-checks'],cited) is None
    assert check_sources(answer.replace('3,430.45','3,330.45'),['retail-checks'],cited)=='unsupported_figure'
    assert check_sources(answer+' It will arrive in 2 days.',['retail-checks'],cited)=='unsupported_figure'
    settled=records.model_copy(update={'transactions':[item for item in records.transactions if item.status!='pending']})
    assert checks(settled,'can i get a car loan',TUESDAY,None) is None and checks(records,'can i get a car loan',TUESDAY,None) is None
    assert checks(settled,'is my balance close to the fall-below fee',TUESDAY,(Decimal('6000'),'SGD')).text.count('below that threshold')==1


PART_B=('eh why is there a $2,400 charge?? i was in singapore the whole night. my rent transfer still says pending, will it reach by friday? '
    'also i got a weird OTP last night and now my app is locked')


def test_part_b_otp_and_locked_app_are_guide_help_not_a_second_urgent_task() -> None:
    risk=assess_request(personal(PART_B))
    assert risk.priority=='P0' and 'fraud_suspected' in risk.risk_flags and risk.fixed_response is None
    issues=[accounts('Why is there a $2,400 charge? I was in Singapore the whole night.','t1','transactions'),
        accounts('My rent transfer still says pending; will it reach by Friday?','t2','transfer_status'),
        accounts('I got a weird OTP last night and now my app is locked.','t3')]
    plan=plan_for(issues,PART_B)
    assert [(t.agent,t.intent,t.priority) for t in plan]==[('relationship','open','P0'),('accounts','transfer_status','P3'),
        ('guide','login_help','P3')]
    assert plan[0].entities=={'urgent':True,'message':PART_B}
    alone=plan_for([accounts('my app is locked')],'my app is locked')
    assert [(t.agent,t.intent,t.priority) for t in alone]==[('guide','login_help','P3')] and 'urgent' not in alone[0].entities
    changed=overridden([accounts('an unrecognised charge appeared after I changed my password')],assess_request(personal()))[0]
    assert changed.entities['urgent'] is True and changed.priority=='P0'


def test_a_charge_the_planner_left_in_retail_becomes_the_one_urgent_task() -> None:
    issues=[accounts('Why is there a $2,400 charge on my card?','t1','transactions'),
        accounts('Will my pending rent transfer reach by Friday?','t2','transfer_status'),accounts('I got a weird OTP and now my app is locked','t3')]
    plan=plan_for(issues,PART_B)
    assert [(t.agent,t.question) for t in plan][0]==('relationship',issues[0].question) and len(plan)==3
    assert sum('urgent' in t.entities for t in plan)==1 and [(t.agent,t.intent) for t in plan][1:]==[('accounts','transfer_status'),('guide','login_help')]


def test_the_demo_amount_targets_the_us_charge_and_the_rule_adds_the_one_alex_did_not_mention() -> None:
    from app.services.agents.fraud import travel_evidence
    from app.services.agents.fraud_records import dispute_for
    from tests.test_fraud import alex_records
    value=alex_records()
    pairs=eligible_proposals(PART_B,value)
    assert pairs==[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    assert dispute_for(value,pairs[0]).transaction_ids==['txn_100111','txn_100116']
    travel=travel_evidence(value,pairs[0].transaction_id)
    assert travel is not None and 'SUNSET GRILL LA' in travel.text and 'Hawker Delight' in travel.text


@pytest.mark.parametrize('question',['Why is there a 2,400 charge I dont recognise?','Why is this charge on my card? I was in Singapore all night.'])
def test_alexs_own_message_narrows_a_planner_question_that_matches_several_cards(question: str) -> None:
    from app.services.agents.fraud_records import scoped_proposals
    from tests.test_fraud import alex_records
    value=alex_records()
    assert eligible_proposals(question,value)==[]
    assert scoped_proposals(question,PART_B,value)==[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    assert scoped_proposals(question,'something is wrong',value)==[]


@pytest.mark.parametrize('pasted',['my OTP is 482913','eh my app is locked, the OTP: 482 913 came by sms','one-time code 4829 13'])
def test_a_pasted_one_time_code_gets_the_fixed_secrets_reply_without_any_model_call(pasted: str) -> None:
    from app.services import answers
    risk=assess_request(personal(pasted))
    assert risk.fixed_response is not None and risk.fixed_response.code=='secrets' and '482' not in risk.redacted_message
    fixed=asyncio.run(answers.make_answer(ChatRequest(messages=[{'role':'user','content':pasted}])))
    assert fixed['response_origin']=='safety' and fixed['prompt_version'] is None and '482' not in fixed['answer']


def test_each_agent_sees_only_its_own_issue_and_never_assistant_text() -> None:
    from app.schemas.chat import Message
    history=[Message(role='user',content='one'),Message(role='assistant',content='Your balance is SGD 1.00.'),Message(role='user',content='two'),
        Message(role='user',content='three'),Message(role='user',content=PART_B)]
    rent=accounts('Will my pending rent transfer arrive by Friday?','t2')
    assert [m.content for m in agent_history(history,rent,True)]==['two','three','Will my pending rent transfer arrive by Friday?']
    assert [m.content for m in agent_history(history,rent,False)]==['two','three',PART_B]
    assert all(m.role=='user' for m in agent_history(history,rent,True))
    follow=rent.model_copy(update={'entities':{'follow_up':True}})  # a reply to our own question keeps it, for the rules
    assert agent_history(history,follow,False)==history


def test_an_unexpected_code_is_guide_guidance_found_by_its_own_words() -> None:
    # Search for this wording is checked in the database (test_knowledge_database.py). Build plan 2b moved it to Guide.
    guide=prompts.load('guide','v2')['system']
    assert 'never say a login was detected' in guide and 'Never ask for' in guide and 'Never say a login was detected' not in guide


def test_card_payment_figures_are_supported_by_alexs_record_without_a_currency() -> None:
    from app.services.agents.retail import evidence_for
    evidence=[item.model_dump(exclude={'source'}) for item in evidence_for(retail_records([ALEX_CARD]))]
    answer='Your Rewards Card ending 4821 is due on 2026-11-05. Minimum payment 50.00, outstanding balance 2,464.20, credit limit 8,000.00 (currency not recorded).'
    assert check_sources(answer,['retail-cards'],evidence) is None
    assert check_sources(answer.replace('50.00','55.00'),['retail-cards'],evidence)=='unsupported_figure'
    assert check_sources('Minimum payment SGD 50.00.',['retail-cards'],evidence)=='unsupported_unit'


def test_card_figures_are_supported_with_their_recorded_currency() -> None:
    """0083 records the card currency, so \"SGD 316.20\" is supported; another currency on the same figure is not."""
    from app.services.agents.retail import evidence_for
    travel={**ALEX_CARD,'card_id':'card_10022','name':'Travel Card','last4':'5592','currency':'SGD','limit':10000.0,'outstanding':316.2}
    evidence=[item.model_dump(exclude={'source'}) for item in evidence_for(retail_records([travel]))]
    assert 'currency unrecorded' not in next(item for item in evidence_for(retail_records([travel])) if item.id=='retail-cards').source.title
    assert check_sources('Your Travel Card ending 5592 has an outstanding balance of SGD 316.20.',['retail-cards'],evidence) is None
    assert check_sources('Your Travel Card ending 5592 has an outstanding balance of USD 316.20.',['retail-cards'],evidence)=='unsupported_unit'
    assert 'currency recorded on that same card or loan record' in prompts.load('retail',retail.PROMPT)['system']


def test_two_cards_need_a_which_card_question_without_figures() -> None:
    from app.services.agents.retail import evidence_for
    second={**ALEX_CARD,'card_id':'card_10019','name':'Travel Card','last4':'7755','minimum_payment':30.0}
    evidence=evidence_for(retail_records([ALEX_CARD,second]))
    cards=next(item for item in evidence if item.id=='retail-cards')
    assert '4821' in cards.text and '7755' in cards.text
    task=Task(task_id='t1',domain='retail',intent='answer_question',entities={'question':'whats my credit card due date and min payment'})
    question='Which of your credit cards do you mean?'
    raw=json.dumps({'answer':question,'source_ids':[],'answer_kind':'conversation','status':'needs_clarification','clarification_question':question})
    assert validate_draft(task,raw,evidence).status=='needs_clarification'
    suffixed=raw.replace('credit cards do you mean','cards, ending 4821 or 7755, do you mean')
    assert validate_draft(task,suffixed,evidence).status=='error'
    assert 'more than one card record' in prompts.load('retail',retail.PROMPT)['system']


def test_car_loan_question_routes_to_retail_with_the_financing_article() -> None:
    # Car loans are not offered since 0115; the loans article says so (test_knowledge_checks.py, retrieval cases).
    issue=simple_task('can i get a car loan','English')
    assert issue is not None and (issue.agent,issue.intent)==('accounts','loans')
    assert 'never say or imply that this customer is eligible' in prompts.load('retail',retail.PROMPT)['system']


@pytest.mark.parametrize('raw',['not json',json.dumps({'answer':'Contact your bank.','source_ids':['customer-records']}),
    fraud_draft('Contact your bank now.',[{'card_id':'card_10031','transaction_id':'txn_100311'}])])
def test_malformed_flagged_fraud_draft_still_offers_the_record_based_block(raw: str) -> None:
    from pydantic import ValidationError
    from app.services.llm_gateway import LLMBadOutput
    evidence=[evidence_for(records())]
    pairs=[FraudProposal(card_id='card_10011',transaction_id='txn_100111')]
    decision=decision_for(fraud_task(),raw,evidence,pairs,'test',flagged=True)
    assert decision.proposal==pairs[0] and decision.result.status=='error' and decision.result.text.startswith(fraud.URGENT)
    with pytest.raises((ValidationError,LLMBadOutput)):decision_for(fraud_task(),raw,evidence,pairs,'test')
    # Charges on two different cards stay ambiguous, so a malformed draft is withheld with no offer (CR12).
    other=[*pairs,FraudProposal(card_id='card_10012',transaction_id='txn_100113')]
    with pytest.raises((ValidationError,LLMBadOutput)):decision_for(fraud_task(),raw,evidence,other,'test',flagged=True)
