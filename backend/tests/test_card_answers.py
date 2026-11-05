"""Card figures from records, the which-card question and typo-tolerant matching (CR14, CR15). No model or database."""
from datetime import datetime,timezone
import pytest
from app.schemas.chat import Message
from app.schemas.retail import RetailRecords
from app.services import guardrail,limit_context,typos
from app.services.agents import card_answers
from app.services.orchestrator import simple_task

SIGNATURE={'card_id':'card_10021','name':'Signature Card','network':'mastercard','last4':'7310','status':'active','currency':'SGD',
    'limit':20000.0,'outstanding':5169.6,'minimum_payment':50.0,'due_date':'2026-11-20'}
TRAVEL={'card_id':'card_10022','name':'Travel Card','network':'visa','last4':'5592','status':'active','currency':'SGD',
    'limit':10000.0,'outstanding':316.2,'minimum_payment':50.0,'due_date':'2026-11-25'}
FEE={'txn_id':'txn_100240','date':'2026-10-05','description':'Annual card fee','amount':-196.2,'currency':'SGD','status':'posted',
    'card_id':'card_10022','card_last4':'5592','is_overseas':False,'failure_reason':None}
RECORDS=RetailRecords.model_validate({'profile':{'name':'Michelle Ng (DEMO)','preferred_language':'en','employment_status':'self_employed'},
    'accounts':[],'cards':[SIGNATURE,TRAVEL],'loans':[],'transactions':[FEE],'captured_at':datetime.now(timezone.utc)})


def ask(*messages: str):
    history=[Message(role='user' if index%2==0 else 'assistant',content=text) for index,text in enumerate(messages)]
    return card_answers.respond('t1',messages[-1],history,RECORDS)


@pytest.mark.parametrize('question',['whats my card balance?','wats my crad bal','how much do i owe on my credit card',
    'whats my credit card balance','card bal pls'])
def test_two_cards_and_no_card_named_asks_which_card(question: str) -> None:
    result=ask(question)
    assert result is not None and result.status=='needs_clarification' and result.question==result.text
    assert 'Signature Card ending 7310' in result.text and 'Travel Card ending 5592' in result.text and 'outstanding balance' in result.text


@pytest.mark.parametrize('reply,expected',[('travel',['SGD 316.20']),('the one ending 5592',['SGD 316.20']),
    ('Signature Card ending 7310',['SGD 5,169.60']),('both',['SGD 5,169.60','SGD 316.20']),('Both cards',['SGD 5,169.60','SGD 316.20']),
    ('signature pls',['SGD 5,169.60'])])
def test_her_reply_to_which_card_gets_the_figure(reply: str,expected: list[str]) -> None:
    asked=ask('whats my card balance?').text
    assert card_answers.awaiting_card([Message(role='assistant',content=asked),Message(role='user',content=reply)])=='balance'
    result=ask('whats my card balance?',asked,reply)
    assert result is not None and result.status=='ok' and all(item in result.text for item in expected)
    assert [item.source_id for item in result.citations]==['retail-cards']


@pytest.mark.parametrize('question,expected',[("what's my travel card balance",'outstanding balance of SGD 316.20'),
    ('credit limit for both cards','SGD 20,000.00'),('when is my signature card payment due','due on 20 Nov 2026'),
    ('min payment on card ending 5592','minimum payment on your Travel Card ending 5592 is SGD 50.00'),
    ('annual fee charge?','charged an annual fee of SGD 196.20 on 5 Oct 2026'),
    ('why was i charged an anual fee','Travel Card ending 5592 was charged an annual fee'),
    ('anual fee on my signature card','can’t see an annual fee charged on your Signature Card')])
def test_named_card_questions_are_answered_from_records(question: str,expected: str) -> None:
    result=ask(question)
    assert result is not None and result.status=='ok' and expected in result.text,result


@pytest.mark.parametrize('question',['whats my balance','i lost my card',
    'what did i spend on my card last month','i dont recognise a charge on my card','can you waive my annual fee','hello'])
def test_other_questions_are_left_to_their_own_flow(question: str) -> None:
    assert ask(question) is None


@pytest.mark.parametrize('typed,meaning',[('i lsot my crad','i lost my card'),('wats my blance','whats my balance'),
    ('I dont reconise this chrge',"i don't recognise this charge")])
def test_typos_are_normalised_for_matching_only(typed: str,meaning: str) -> None:
    assert typos.normalise(typed)==meaning


def test_typos_do_not_hide_urgency_or_change_routing() -> None:
    from app.schemas.chat import ChatRequest
    risk=guardrail.assess_request(ChatRequest(messages=[Message(role='user',content='help i lsot my crad')],mode='personal'))
    assert 'lost_card' in risk.risk_flags and risk.redacted_message=='help i lsot my crad'
    issue=simple_task('wats my crad bal','English')
    assert issue is not None and issue.agent=='accounts' and issue.question=='wats my crad bal'


# Transactions and fees from records (CR15) ---------------------------------------------------------------------------
from app.services.agents import transaction_answers
from app.services.agents.meeting_answers import matching_slot,slot_label

TRANSFER_FEE={**FEE,'txn_id':'txn_100239','date':'2026-10-03','description':'Telegraphic transfer fee','amount':-25.0,'card_id':None,'card_last4':None}
WITH_FEES=RetailRecords.model_validate({**RECORDS.model_dump(mode='json'),'transactions':[FEE,TRANSFER_FEE]})


@pytest.mark.parametrize('question,expected',[('why was i charged $25 on 3 oct','3 Oct 2026: Telegraphic transfer fee, SGD 25.00'),
    ('what is the 25 dollar fee','Telegraphic transfer fee, SGD 25.00'),('show my recent transactions','Annual card fee, SGD 196.20'),
    ('wat did i spend recently','Here are your most recent transactions'),('what fees have i been charged','These are the fees'),
    ('why was i charged 99 on 3 oct','can’t find a transaction matching 99.00 and 3 Oct')])
def test_transactions_and_fees_are_found_in_records(question: str,expected: str) -> None:
    result=transaction_answers.respond('t1',question,question,WITH_FEES)
    assert result is not None and result.status=='ok' and expected in result.text,result
    assert [item.source_id for item in result.citations]==['retail-transactions']


@pytest.mark.parametrize('question',['why is my transfer pending','i dont recognise a charge of $25','hello','whats my balance'])
def test_other_transaction_questions_keep_their_flow(question: str) -> None:
    assert transaction_answers.respond('t1',question,question,WITH_FEES) is None


def test_a_typed_or_tapped_meeting_time_finds_its_slot() -> None:
    from types import SimpleNamespace
    slot=SimpleNamespace(starts_at=datetime(2026,11,2,2,0,tzinfo=timezone.utc))  # 10:00 am Singapore
    assert slot_label(slot.starts_at)=='Mon 2 Nov, 10:00 am'
    assert matching_slot('Book Mon 2 Nov, 10:00 am',[slot]) is slot and matching_slot('book 2 nov 10am pls',[slot]) is slot
    assert matching_slot('Book Mon 2 Nov, 11:00 am',[slot]) is None


@pytest.mark.parametrize('question',['why was i charged $25 on 3 oct','show my recent transactions','what fees have i been charged',
    'wat did i spend recently'])
def test_charge_and_transaction_questions_route_to_records_not_fraud(question: str) -> None:
    issue=simple_task(question,'English')
    assert issue is not None and issue.agent=='accounts'
    assert simple_task('i dont recognise this $25 charge','English').entities.get('urgent') is True  # the urgent card task (2b)


def test_a_follow_up_stays_on_the_card_just_discussed() -> None:
    """CR14: "what about the due date?" after a Travel Card answer is about the Travel Card; a new topic is not."""
    answered=ask('whats my travel card balance').text
    follow=ask('whats my travel card balance',answered,'what about the due date?')
    assert follow is not None and follow.status=='ok' and 'Travel Card ending 5592' in follow.text and '25 Nov 2026' in follow.text
    assert ask('whats my travel card balance',answered,'whats my balance') is None  # the account, not a card


def test_a_handover_is_previewed_and_only_sent_on_her_choice() -> None:
    from app.services import rm_handover
    shown=rm_handover.preview('Can you waive my annual fee?',[Message(role='user',content='Can you waive my annual fee?')])
    assert rm_handover.PREVIEW_MARK in shown and '• Topic: Fees or rates' in shown and '“Send to my RM”' in shown
    reply=lambda text,offered=shown:rm_handover.pending_choice([Message(role='assistant',content=offered),Message(role='user',content=text)])
    assert reply('Send to my RM')==('Can you waive my annual fee?','send',None)
    assert reply('cancel')[1]=='cancel' and reply('what is my balance') is None
    existing=rm_handover.preview('Can you waive my annual fee?',[],'TCK-001050')
    assert reply('Add to TCK-001050',existing)[1:]==('add','TCK-001050') and reply('Open a new request',existing)[1]=='new'
    assert 'talk to a person' in rm_handover.WITHHELD


@pytest.mark.parametrize('question,amount',[('i wan to raise my card limit to 15000','SGD 15,000'),('increase my credit limit to $20k','SGD 20,000'),
    ('can i increase my card limit','')])
def test_card_limit_change_is_explained_without_a_model(question: str,amount: str) -> None:
    result=ask(question)
    assert result is not None
    if amount:
        assert result.status=='needs_clarification' and amount.replace('SGD ','') in result.text
        assert 'temporary or permanent' in result.text and result.question==result.text
    else:
        assert result.status=='ok' and 'Raise a card limit request' in result.text and 'approval isn’t guaranteed' in result.text
        assert 'steps of SGD 1,000' in result.text and '30 days' in result.text   # 0096 rules


@pytest.mark.parametrize('question,offered',[('i want to raise my card limit to 15500','SGD 15,000 or SGD 16,000'),
    ('i want to raise my card limit to 80000','SGD 50,000')])
def test_a_limit_the_bank_would_refuse_is_explained_before_the_form(question: str,offered: str) -> None:
    result=ask(question)
    assert result is not None and result.status=='needs_clarification'
    assert 'steps of SGD 1,000' in result.text and offered in result.text and 'Request ready' not in result.text


FIFTH=RECORDS.model_copy(update={'captured_at':datetime(2026,11,5,2,tzinfo=timezone.utc)})  # 5 Nov 2026 in Singapore


def chat(*user: str,records=FIFTH) -> list:
    """Her messages one by one, each answered with the rules reply before the next (C04 style)."""
    history,results=[],[]
    for text in user:
        history.append(Message(role='user',content=text))
        results.append(result:=card_answers.respond('t1',text,history,records))
        history.append(Message(role='assistant',content=result.text))
    return results


def test_c04_asks_for_every_missing_detail_at_once_then_is_ready() -> None:
    first,second=chat('Can you increase my credit card limit to 30k for my Japan trip?','Signature Card 7310, temporary until 30 Nov')
    assert first.status=='needs_clarification' and first.question==first.text
    # amount and reason she gave are kept; card and duration with the end date are asked in the same reply
    assert 'SGD 30,000' in first.text and 'travel as the reason' in first.text and 'reason for the change' not in first.text
    assert 'Which card is this for: Signature Card ending 7310, Travel Card ending 5592?' in first.text and 'temporary or permanent' in first.text
    assert 'until what date (up to 90 days)' in first.text and 'already has a limit' not in first.text
    assert second.status=='ok' and second.text.startswith('Request ready for your review: temporary change to SGD 30,000 on your '
        'Signature Card ending 7310 until 30 Nov 2026 for your travel.') and 'Raise a card limit request' in second.text
    # the QA1 case's own reply checks hold for this reply ("no limit has been changed" is not a claim that it changed)
    import json,pathlib,re
    case=next(c for c in json.loads((pathlib.Path(__file__).parents[1]/'evaluation/qa1_client_cases.json').read_text(encoding='utf-8')) if c['id']=='C04')
    assert all(re.search(p,second.text) for p in case['reply_has']) and not any(re.search(p,second.text) for p in case['reply_lacks'])
    assert re.search(case['reply_lacks'][1],'Your limit has been increased to SGD 30,000.')


def test_a_partial_answer_is_asked_only_for_what_is_still_missing() -> None:
    first,second,third,fourth=chat('i wan to raise my card limit to 15000','travel','permanent',"my daughter's wedding")
    assert all(item in first.text for item in ('Which card is this for','temporary or permanent','reason for the change'))
    assert second.text=='Still needed for your card limit request: Should it be temporary or permanent? If temporary, until what date '\
        '(up to 90 days)? What is the reason for the change?'   # "travel" after "Which card" names the Travel Card, not a reason
    assert third.text=='Still needed for your card limit request: What is the reason for the change?'
    assert fourth.status=='ok' and 'permanent change to SGD 15,000 on your Travel Card ending 5592.' in fourth.text
    assert 'for your travel' not in fourth.text


def test_a_temporary_end_date_must_be_within_90_days_and_real() -> None:
    _,late,fixed,_=chat('I want to increase my card limit to 25000','the signature card, temporary until 20 Mar','until 20 Jan','renovation')
    assert 'from tomorrow up to 90 days, so it can end on 3 Feb 2027 at the latest' in late.text and 'Signature' not in late.text
    assert fixed.text=='Still needed for your card limit request: What is the reason for the change?'
    assert chat('I want to increase my card limit to 25000','the signature card, temporary until 20 Mar','until 20 Jan','renovation')[-1].text\
        .startswith('Request ready for your review: temporary change to SGD 25,000 on your Signature Card ending 7310 until 20 Jan 2027.')
    _,bad=chat('increase my credit limit to $20k','Travel Card, temporary, 31 Feb')
    assert '31 Feb is not a date' in bad.text and 'Request ready' not in bad.text
    # a duration is not a reason, and stopping ends the intake
    first,=chat('I need a temporary limit increase on my travel card for a month')
    assert 'What new credit limit' in first.text and 'reason for the change' in first.text and 'Which card' not in first.text
    asked=[Message(role='user',content='I want to increase my card limit to 25000'),Message(role='assistant',content=first.text)]
    assert limit_context.active(asked+[Message(role='user',content='a family emergency')])
    assert not limit_context.active(asked+[Message(role='user',content='never mind')])


def test_asking_for_the_current_limit_is_pointed_out() -> None:
    result=ask('i want a permanent increase of my signature card limit to 20000')
    assert result is not None and 'already has a limit of SGD 20,000.00' in result.text


def test_waive_it_and_buttons_in_handover_context() -> None:
    from app.services import rm_handover
    assert rm_handover.needs_person('can you waive it') and rm_handover.topic_for('can you waive it')=='Fees or rates'
    history=[Message(role='user',content='my card was charged an annual fee'),Message(role='user',content='Confirm booking'),
        Message(role='user',content='can you waive it')]
    assert rm_handover.earlier_messages('can you waive it',history)==['my card was charged an annual fee']
