import json
import pytest
from app.services.source_checks import check_sources
from app.services.answers import check_answer


def check(answer,text,**metadata):
    return check_sources(answer,['evidence'],[{'id':'evidence','text':text,**metadata}])


@pytest.mark.parametrize('answer,text',[
    ('The balance is SGD 100.','The balance is USD 100.'),
    ('The fee is 20%.','The fee is SGD 20.'),
    ('The fee is SGD 20.','The rate is 20%.'),
    ('The balance is SGD 100.',json.dumps({'accounts':[{'balance':100,'currency':'USD'}]})),
    ('The balance is SGD 100.',json.dumps({'accounts':[{'balance':100,'currency':'USD'},{'balance':200,'currency':'SGD'}]})),
])
def test_matching_digits_do_not_authorize_changed_units(answer,text):
    assert check(answer,text)=='unsupported_unit'


@pytest.mark.parametrize('answer,text',[
    ('The balance is USD 1,234.50.','The balance is USD 1234.50.'),
    ('The balance is 100 SGD.','The balance is SGD 100.'),
    ('The rate is 20 percent.','The rate is 20%.'),
    ('The recorded balance is SGD 1234.50.',json.dumps({'accounts':[{'balance':'1234.50','currency':'SGD'}]})),
    ('The recorded balance is SGD 100.', 'Synthetic records:\n'+json.dumps({'accounts':[{'balance':100,'currency':'SGD'}]})),
    ('The fee depends on your bank.', 'Fees vary by bank.'),
])
def test_supported_units_and_qualitative_answers_pass(answer,text):
    assert check(answer,text) is None


def test_uncited_source_cannot_authorize_a_currency():
    assert check_sources('SGD 100.',['usd'],[{'id':'usd','text':'USD 100.'},{'id':'sgd','text':'SGD 100.'}])=='unsupported_unit'


def test_record_currency_does_not_authorize_dates_or_other_record_numbers():
    assert check('SGD 2026.',json.dumps({'balance':100,'currency':'SGD','payment_due_date':'2026-10-20'}))=='unsupported_unit'


@pytest.mark.parametrize('answer',[
    'Our policy says early withdrawal may incur a fee.',
    'Under our card terms, interest applies.',
    "DemoBank’s policy is that a fee may apply.",
])
def test_external_education_cannot_be_claimed_as_bank_policy(answer):
    assert check(answer,'Interest and fees may apply.',publisher='MoneySense')=='unsupported_attribution'


def test_external_publisher_attribution_is_accepted():
    assert check('MoneySense explains that interest may apply.','Interest may apply.',publisher='MoneySense') is None


def test_source_unit_rejection_reaches_answer_validation():
    raw=json.dumps({'answer':'The balance is SGD 100.','source_ids':['records'],'answer_kind':'banking'})
    assert check_answer(raw,[{'id':'records','text':'USD 100.'}])==(None,None,'source_check')


def test_negative_amount_cannot_be_relabelled_as_positive():
    assert check('SGD 100.','SGD -100.')=='unsupported_unit'


def test_bank_policy_disclaimer_is_not_an_attribution_claim():
    assert check("This is not DemoBank’s policy. Confirm your own bank's terms.",
                 'Terms depend on your bank.',publisher='MoneySense') is None
