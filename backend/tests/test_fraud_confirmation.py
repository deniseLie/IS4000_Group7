"""CR12 (0082): the charge list the client confirms, and reading her confirmation back. Pure rules; no model or database."""
from datetime import date
import pytest
from app.schemas.chat import Message
from app.schemas.fraud import SecurityTransaction
from app.services import fraud_reports


def charge(txn: str,day: int,merchant: str,amount: float) -> SecurityTransaction:
    return SecurityTransaction(txn_id=txn,date=date(2026,10,day),description=merchant,amount=amount,currency='SGD',status='posted',
        card_id='card_10021',card_last4='7310')


ASK=fraud_reports.ask_text('7310',[charge('txn_100215',27,'MAYFAIR AUDIO LONDON',-1.0),charge('txn_100216',27,'LUXEHAUS LONDON',-3850.0)])


def test_the_list_names_each_charge_and_asks_for_a_plain_yes() -> None:
    assert 'card ending 7310' in ASK and '27 Oct 2026, LUXEHAUS LONDON, SGD 3,850.00 (ref. 100216)' in ASK
    assert '“Yes, report it”' in ASK and 'fraud line on the back of your card' in ASK and 'TCK-' not in ASK


@pytest.mark.parametrize('reply',['Yes, report it','yes','Yes please report them','That’s right','Correct','go ahead'])
def test_a_plain_yes_confirms_exactly_the_listed_charges(reply: str) -> None:
    history=[Message(role='user',content='I do not recognise the London charges'),Message(role='assistant',content='Urgent steps…\n\n'+ASK),
        Message(role='user',content=reply)]
    found=fraud_reports.pending(history)
    assert [item.encoded() for item in found]==['7310:txn_100215,txn_100216']
    assert fraud_reports.Confirmation.decode(found[0].encoded())==found[0]


@pytest.mark.parametrize('reply',['No, only the second one','yes but not the 1 dollar one','What is my balance?','Not that card','wait'])
def test_anything_but_a_plain_yes_reports_nothing(reply: str) -> None:
    history=[Message(role='assistant',content=ASK),Message(role='user',content=reply)]
    assert fraud_reports.pending(history)==[]


def test_a_yes_to_some_other_question_reports_nothing() -> None:
    history=[Message(role='assistant',content='Would you like to know your card balance?'),Message(role='user',content='yes')]
    assert fraud_reports.pending(history)==[]


def test_lost_card_reply_quotes_the_reference_and_never_claims_a_block() -> None:
    sent=fraud_reports.LostCardReport(reference='TCK-001234').reply()
    assert 'TCK-001234' in sent and 'fraud team as urgent' in sent and 'My requests' in sent and 'is blocked' not in sent
    assert 'already have an open lost-card report' in fraud_reports.LostCardReport(reference='TCK-001234',reused=True).reply()
    assert 'nothing has been sent' in fraud_reports.LostCardReport(reference=None,failed=True).reply()


def test_lost_card_is_reported_only_after_she_confirms() -> None:
    """CR12: safety steps first; the urgent ticket waits for "Yes, report my card"; "No" reports nothing."""
    offer='Call the fraud line now…\n\n'+fraud_reports.LOST_OFFER
    history=lambda reply:[Message(role='user',content='i lost my card'),Message(role='assistant',content=offer),Message(role='user',content=reply)]
    assert fraud_reports.lost_pending(history('Yes, report my card'))==('i lost my card',True)
    assert fraud_reports.lost_pending(history('yes'))==('i lost my card',True)
    assert fraud_reports.lost_pending(history('no'))==('i lost my card',False)
    assert fraud_reports.lost_pending(history('what is my balance')) is None
