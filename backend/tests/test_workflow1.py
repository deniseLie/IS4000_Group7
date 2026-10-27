"""Workflow 1 (Alex, three problems in one message): deterministic rules and composition.

Sample drafts go straight through the validators; they never stand in for the real AI client.
Model planning and wording are covered by the opt-in real-provider browser and evaluation runs.
"""
import asyncio
import json
from datetime import date,datetime,timedelta,timezone
from uuid import uuid4
import pytest
from app.schemas.agents import Evidence,PlannedIssue
from app.schemas.chat import ChatRequest
from app.schemas.contracts import AgentResult,Principal,RequestContext,Task,TaskPlan
from app.schemas.events import ActionRequiredEvent,AnswerSection,Source
from app.schemas.fraud import FraudProposal
from app.schemas.workflows import ResponseMeta
from app.services import prompts
from app.services.agents import fraud
from app.services.agents.answer import validate_draft
from app.services.agents.fraud import decision_for,evidence_for
from app.services.agents.fraud_records import eligible_proposals
from app.services.guardrail import assess_request
from app.services.knowledge import INDEX,source
from app.services.orchestrator import compose,decision_text,preview_answer,prioritise,route_issues,task_priority,task_prompt_id
from app.services.source_checks import check_sources,unit_matches
from tests.test_fraud import records

MESSAGE='theres a $2400 charge i dont recognise, my transfer to my landlord still says pending, and i got locked out of the app on my phone'


def personal(question: str=MESSAGE) -> ChatRequest:
    return ChatRequest(messages=[{'role':'user','content':question}],mode='personal')


@pytest.mark.parametrize('question',[MESSAGE,'theres a $2400 charge i dont recognise','i didnt make this payment',
    'I dont recognize this transaction','that charge is not mine','the payment on my card is not mine'])
def test_informal_unrecognised_charges_are_flagged_before_any_model_call(question: str) -> None:
    risk=assess_request(personal(question))
    assert risk.priority=='P0' and 'fraud_suspected' in risk.risk_flags and risk.fixed_response is None


@pytest.mark.parametrize('question',['i got locked out of the app on my phone','my transfer to my landlord still says pending',
    'I recognise this payment','dont forget to make the payment','whats my credit card due date and min payment'])
def test_other_workflow_questions_are_not_fraud(question: str) -> None:
    assert 'fraud_suspected' not in assess_request(personal(question)).risk_flags


def test_fraud_comes_first_then_the_lockout_then_ordinary_issues() -> None:
    issues=[PlannedIssue(domain='retail',question='my transfer to my landlord still says pending'),
        PlannedIssue(domain='retail',question='i got locked out of the app on my phone'),
        PlannedIssue(domain='retail',question='whats my credit card due date')]
    plan=prioritise({'risk':assess_request(personal()),'issues':issues})['plan']
    assert [(task.domain,task.priority) for task in plan.by_priority()]==[('fraud','P0'),('retail','P1'),('retail','P3'),('retail','P3')]
    assert [task.entities['question'] for task in plan.by_priority()][1:]==[issues[1].question,issues[0].question,issues[2].question]
    routed=route_issues([PlannedIssue(domain='retail',question='theres a $2400 charge i dont recognise'),*issues])
    assert routed[0].domain=='fraud' and task_priority(PlannedIssue(domain='retail',question=issues[0].question))=='P3'


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
    two=pairs+[FraudProposal(card_id='card_10011',transaction_id='txn_100113')]
    assert decision_for(fraud_task(),valid,evidence,two,'test',flagged=True).proposal is None


@pytest.mark.parametrize('claim',["I've reversed the charge.",'We have cancelled the transfer.','I have successfully disputed it.',
    'I have investigated the payment.','We have expedited your transfer.','I have canceled the payment.'])
def test_unsupported_action_claims_are_withheld(claim: str) -> None:
    article=INDEX['transactions']
    evidence=[Evidence(id=article.id,text=article.content,publisher=article.publisher,scope=article.notice,source=Source.model_validate(source(article)))]
    task=Task(task_id='t2',domain='retail',intent='answer_question',entities={'question':'pending transfer'})
    raw=json.dumps({'answer':claim,'source_ids':[article.id],'answer_kind':'banking','status':'ok','clarification_question':''})
    assert validate_draft(task,raw,evidence).status=='error'
    allowed=json.dumps({'answer':'Pending means processing is not complete; the bank can investigate an unfamiliar transaction.',
        'source_ids':[article.id],'answer_kind':'banking','status':'ok','clarification_question':''})
    assert validate_draft(task,allowed,evidence).status=='ok'


def state() -> dict[str,object]:
    request=personal()
    tasks=[Task(task_id='t1',domain='fraud',intent='answer_question',priority='P0',entities={'question':'charge'}),
        Task(task_id='t2',domain='retail',intent='answer_question',priority='P3',entities={'question':'transfer'}),
        Task(task_id='t3',domain='retail',intent='answer_question',priority='P3',entities={'question':'app'})]
    results=[AgentResult(task_id=task.task_id,agent='x',status='needs_human' if task.domain=='fraud' else 'ok',answer_kind='conversation',
        text=f'Answer {task.task_id}.') for task in tasks]
    return {'request':request,'history':request.messages,'history_count':1,'plan':TaskPlan(tasks=tasks),'results':results,
        'evidence':[[],[],[]],'metadata':[ResponseMeta(origin='ai',prompt_id=None)]*3,'decision_sections':[]}


def test_preview_shows_every_section_with_the_block_offer_on_the_urgent_one() -> None:
    action=ActionRequiredEvent(command_id=uuid4(),type='card.temporary_block',summary='Review a temporary block for synthetic card ending 4821',
        expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))
    preview=preview_answer(state(),{'t1':action})
    assert [section.task_id for section in preview.sections or []]==['t1','t2','t3']
    assert [section.pending_action for section in preview.sections or []]==[action,None,None]
    assert all(section.decision is None for section in preview.sections or [])


@pytest.mark.parametrize('command_state,receipt,first',[('succeeded','succeeded','Your card ending 4821 is temporarily blocked.'),
    ('rejected',None,'You chose not to block the card ending 4821; it stays active.'),
    ('expired',None,'The temporary-block proposal expired without changing your card.'),
    ('failed','failed','The temporary block failed because the card changed. Your card was not changed.')])
def test_decision_is_merged_into_the_urgent_section(command_state: str,receipt: str | None,first: str) -> None:
    text=decision_text(command_state,receipt,'4821')
    assert text.startswith(first)
    if receipt=='succeeded':assert 'replacement card' in text and 'did not reverse' in text and 'urgent priority' in text
    if command_state=='rejected':assert 'official fraud line' in text and 'handoff' not in text
    current=state()
    current['decision_sections']=[AnswerSection(task_id='t1_decision',domain='fraud',priority='P0',answer=text,
        sources=[Source(id='action-receipt-t1',title='Recorded synthetic action decisions',url=None,version='v1')],needs_human=True,response_origin='safety')]
    answer=compose(current)['answer']
    sections=answer.sections or []
    assert [section.task_id for section in sections]==['t1','t2','t3']
    assert sections[0].decision==text and sections[0].answer=='Answer t1.' and sections[0].needs_human
    assert 'action-receipt-t1' in [item.id for item in sections[0].sources] and text in answer.answer
    assert all(section.pending_action is None for section in sections)


def test_personal_retail_uses_the_new_prompt_version() -> None:
    task=Task(task_id='t1',domain='retail',intent='answer_question',entities={'question':'car loan'})
    assert task_prompt_id(task,personal())==prompts.load('retail','v4')['id']
    assert prompts.current('planner')['version']=='v2'


def retail_records(cards: list[dict[str,object]]):
    from app.schemas.retail import RetailRecords
    return RetailRecords.model_validate({'profile':{'name':'Alex Tan (DEMO)','preferred_language':'en','employment_status':'employed'},
        'accounts':[],'cards':cards,'loans':[],'transactions':[],'captured_at':datetime.now(timezone.utc)})


ALEX_CARD={'card_id':'card_10011','name':'Rewards Card','network':'visa','last4':'4821','status':'active','limit':8000.0,
    'outstanding':2464.2,'minimum_payment':50.0,'due_date':'2026-11-05'}
TUESDAY=date(2026,10,27)


def alex_retail():
    """Alex's retail snapshot after migration 0064 (docs/synthetic-data-spec.md)."""
    from app.schemas.retail import RetailRecords
    def row(txn: str,day: str,name: str,amount: float,status: str,card: bool) -> dict[str,object]:
        return {'txn_id':txn,'date':day,'description':name,'amount':amount,'currency':'SGD','status':status,
            'card_id':'card_10011' if card else None,'card_last4':'4821' if card else None,'is_overseas':card and 'MERCHANT' in name,'failure_reason':None}
    return RetailRecords.model_validate({'profile':{'name':'Alex Tan (DEMO)','preferred_language':'en','employment_status':'employed'},
        'accounts':[{'account_id':'acc_10011','type':'savings','number':'****6012','currency':'SGD','balance':5230.45,'status':'active'}],
        'cards':[{**ALEX_CARD,'outstanding':2470.19}],'loans':[],'captured_at':datetime.now(timezone.utc),'transactions':[
            row('txn_100111','2026-10-17','ONLINE MERCHANT 8831',-2400.0,'posted',True),row('txn_100112','2026-10-17','Transfer to landlord',-1800.0,'pending',False),
            row('txn_100115','2026-10-12','ONLINE MERCHANT 8831',-1.0,'posted',True),row('txn_100114','2026-10-01','Employer Pte Ltd (DEMO)',5600.0,'posted',False)]})


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
    evidence=checks(records,"my rent transfer still says pending and rent's due friday, will it make it?",TUESDAY,(Decimal('1000'),'SGD'))
    assert evidence is not None and evidence.id=='retail-checks' and 'txn_' not in evidence.text
    for fact in ['Today in Singapore is 2026-10-27 (Tuesday)','pending since 2026-10-17 (Saturday): 10 calendar days and 7 business days ago',
        "stated due day is 2026-10-30 (Friday), 3 days from today",'the balance would be SGD 3430.45','Both balances are above that threshold',
        'credit card ending 4821 (outstanding 2470.19 of a 8000.00 limit; card currency not recorded) and do not change a bank account balance']:
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


PART_B=("there's a $2,400 charge i don't recognise and i think there were smaller weird ones before it. my rent transfer still says "
    "pending and rent's due friday, will it make it? also locked out of the app, is that related??")


def test_part_b_lockout_is_linked_security_help_not_a_second_fraud_task() -> None:
    issues=[PlannedIssue(domain='fraud',question="There's a $2,400 charge I don't recognise, plus smaller charges before it"),
        PlannedIssue(domain='retail',question='My rent transfer is still pending and rent is due Friday; will it arrive on time?'),
        PlannedIssue(domain='fraud',question='I am locked out of the app; is that related to the unrecognised charges?')]
    routed=route_issues(issues)
    assert [issue.domain for issue in routed]==['fraud','retail','retail']
    plan=prioritise({'risk':assess_request(personal(PART_B)),'issues':routed})['plan'].by_priority()
    assert [(task.domain,task.priority) for task in plan]==[('fraud','P0'),('retail','P1'),('retail','P3')]
    assert plan[1].entities['linked']=='card_fraud' and 'linked' not in plan[2].entities and 'linked' not in plan[0].entities
    alone=prioritise({'risk':assess_request(personal('i got locked out of the app')),'issues':[PlannedIssue(domain='retail',question='i got locked out of the app')]})
    assert 'linked' not in alone['plan'].tasks[0].entities and alone['plan'].tasks[0].priority=='P1'
    assert route_issues([PlannedIssue(domain='retail',question='an unrecognised charge appeared after I changed my password')])[0].domain=='fraud'


def test_linked_lockout_gets_the_security_guidance_by_id() -> None:
    from app.services.orchestrator import linked_evidence
    task=Task(task_id='t2',domain='retail',intent='answer_question',priority='P1',entities={'question':'locked out','linked':'card_fraud'})
    ctx=RequestContext(session_id=uuid4(),principal=Principal(kind='customer',user_id=uuid4(),customer_id=1001))
    assert [item.id for item in asyncio.run(linked_evidence(task,ctx))]==['account-security-after-fraud']
    assert asyncio.run(linked_evidence(task.model_copy(update={'entities':{'question':'locked out'}}),ctx))==[]
    assert 'reset the password in the official app first' in prompts.load('retail','v4')['system']


def test_card_payment_figures_are_supported_by_alexs_record_without_a_currency() -> None:
    from app.services.agents.retail import evidence_for
    evidence=[item.model_dump(exclude={'source'}) for item in evidence_for(retail_records([ALEX_CARD]))]
    answer='Your Rewards Card ending 4821 is due on 2026-11-05. Minimum payment 50.00, outstanding balance 2,464.20, credit limit 8,000.00 (currency not recorded).'
    assert check_sources(answer,['retail-cards'],evidence) is None
    assert check_sources(answer.replace('50.00','55.00'),['retail-cards'],evidence)=='unsupported_figure'
    assert check_sources('Minimum payment SGD 50.00.',['retail-cards'],evidence)=='unsupported_unit'


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
    assert 'more than one card record' in prompts.load('retail','v4')['system']


def test_car_loan_question_routes_to_retail_with_the_car_loan_article() -> None:
    from app.services.knowledge import search
    from app.services.orchestrator import simple_issue
    issue=simple_issue('can i get a car loan','English')
    assert issue is not None and issue.domain=='retail'
    assert search('can i get a car loan')[0].id=='car-loans'
    assert 'final decision' in INDEX['car-loans'].content and 'never say or imply that this customer is eligible' in prompts.load('retail','v4')['system']


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
    with pytest.raises((ValidationError,LLMBadOutput)):decision_for(fraud_task(),raw,evidence,pairs*2,'test',flagged=True)
