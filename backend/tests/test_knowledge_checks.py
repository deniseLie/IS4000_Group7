import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import re
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.schemas.chat import ChatRequest
from app.services import knowledge, answers
from app.services.source_checks import check_sources, numbers
from evaluation.run import evaluate, write_report

@pytest.mark.parametrize('question,expected',[
 ('fixed deposit early withdrawal maturity','sg-accounts'),
 ('credit card minimum payment interest','sg-card-payments'),
 ('secured unsecured loan collateral','sg-loan-types'),
 ('dispute merchant goods not delivered','sg-card-disputes'),
])
def test_topics_retrieve_primary_sources(question,expected):
    assert expected in [a.id for a in knowledge.search(question)]

@pytest.mark.parametrize('question,expected',[
 ('wats the monthly fee for a savings acc?','savings-fees'),
 ('i got locked out of the app on my phone','app-login-recovery'),
 ('can i get a car loan','car-loans'),
 ('theres a $2400 charge i dont recognise','unrecognised-charge'),
])
def test_workflow_one_questions_retrieve_demo_guidance(question,expected):
    assert knowledge.search(question)[0].id==expected
    article=knowledge.INDEX[expected]
    assert article.reference_url is None and not answers.URL.search(article.content)

@pytest.mark.parametrize('question,expected',[
 ('can i get a home loan','home-loans'),('what do i need to refinance my mortgage','home-loans'),
 ('how do i apply for a personal loan','personal-loans'),('can i borrow for a renovation with a cash loan','personal-loans'),
])
def test_loan_questions_retrieve_their_product_guidance(question,expected):
    # CR16 (0086): every DemoBank loan type has its own demo article; the bank decides and no terms are invented.
    assert knowledge.search(question)[0].id==expected
    article=knowledge.INDEX[expected]
    assert article.reference_url is None and not answers.URL.search(article.content)
    assert 'final decision' in article.content and 'not supplied in this demo' in article.content
    assert set(re.findall(r'[0-9]+',article.content))<={'21'}

def test_unclear_loan_question_names_the_loan_types_and_asks_which():
    assert [a.id for a in knowledge.search('i want a loan')]==['loans']
    text=knowledge.INDEX['loans'].content
    assert all(kind in text for kind in ('home loans','personal loans','loans to buy a new or used car')) and 'ask which one' in text
    assert 'cannot see loan applications' in text and 'relationship manager' in text

def test_part_b_guidance_has_no_invented_times_and_names_the_fraud_team():
    assert 'transfer-timing' in [a.id for a in knowledge.search("my rent transfer still says pending, will it make it")]
    for key in ('transfer-timing','account-security-after-fraud'):
        assert not re.search(r'[0-9]',knowledge.INDEX[key].content)
    assert 'fraud team' in knowledge.INDEX['unrecognised-charge'].content and 'not proof of fraud' in knowledge.INDEX['unrecognised-charge'].content

def test_guidance_says_only_the_bank_blocks_cards_and_where_reports_go():
    # 0081: card actions are the bank's; the assistant passes reports to the fraud team and other questions to the RM.
    for key in ('lost-card','unrecognised-charge','support'):
        text=knowledge.INDEX[key].content
        assert 'only after you confirm' not in text and 'dispute draft' not in text and 'can offer a temporary block' not in text
    assert 'cannot block, unblock or cancel a card; only the bank can' in knowledge.INDEX['lost-card'].content
    assert 'fraud team' in knowledge.INDEX['support'].content and 'relationship manager' in knowledge.INDEX['support'].content

@pytest.mark.parametrize('change',[
 {'review_due':'2000-01-01'}, {'checked_on':'2099-01-01'},
 {'reference_url':'https://www.moneysense.gov.sg.evil.invalid/page'},
 {'reference_url':'http://www.moneysense.gov.sg/page'},
 {'reference_url':'https://user@www.moneysense.gov.sg/page'},
 {'review_due':None}, {'checked_on':'invalid'},
])
def test_unusable_external_source_excluded(change,monkeypatch):
    article=replace(knowledge.INDEX['sg-accounts'],**change)
    assert not knowledge.is_current(article)
    monkeypatch.setattr(knowledge,'ARTICLES',[article])
    assert knowledge.search('fixed deposit')==[]


def test_review_date_boundary():
    article=knowledge.INDEX['sg-accounts']
    assert knowledge.is_current(article,date(2027,1,12))
    assert not knowledge.is_current(article,date(2027,1,13))


def test_expired_sources_excluded_for_other_languages(monkeypatch):
    monkeypatch.setattr(answers,'ARTICLES',[replace(knowledge.INDEX['sg-accounts'],review_due='2000-01-01')])
    assert answers.retrieve('银行账户',[],'Chinese')==[]


def test_source_details_show_provenance_and_scope():
    result=TestClient(app).get('/knowledge/sg-accounts').json()
    assert result['publisher']=='MoneySense'
    assert result['reference_url']=='https://www.moneysense.gov.sg/understanding-bank-accounts/'
    assert result['checked_on']=='2026-10-12'
    assert 'Not a DemoBank product policy' in result['notice']
    assert TestClient(app).get('/knowledge/accounts').json()['reference_url'] is None

@pytest.mark.parametrize('answer',['The fee is SGD 199.', 'The rate is 18.5%.', 'Wait 3 days.', 'The fee is １９９ dollars.', 'The fee is SGD199.', '费用是199元。'])
def test_unsupported_numbers_rejected(answer):
    assert check_sources(answer,['cards'],[{'id':'cards','text':'Fees unavailable.'}])=='unsupported_figure'


def test_only_cited_evidence_supports_numbers():
    evidence=[{'id':'cards','text':'Fees unavailable.'},{'id':'other','text':'SGD 199.'}]
    assert check_sources('The fee is SGD 199.',['cards'],evidence)=='unsupported_figure'


def test_supported_formatting_and_lists():
    assert numbers('1. Check your account.\n2. Contact support.')==set()
    assert numbers('SGD 1,234.50.')=={Decimal('1234.50')}
    assert check_sources('The recorded balance is SGD 1234.50.',['customer-records'],[{'id':'customer-records','text':'{"balance":1234.50,"currency":"SGD"}'}]) is None


def test_offline_report_has_no_live_calls(monkeypatch,tmp_path):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    report=asyncio.run(evaluate())
    assert report['summary']=={'pass':19,'fail':0,'error':0,'needs_human_review':0}
    assert report['mode']=='offline_validator_checks'
    write_report(report,tmp_path/'report.json')
    assert 'not answers generated by a live model' in (tmp_path/'report.html').read_text()
    assert json.loads((tmp_path/'report.json').read_text())['dataset_sha256']


def test_report_escapes_html(tmp_path):
    report={'mode':'offline','created_at':'test','summary':{},'limits':[], 'results':[{'id':'x','status':'pass','question':'<script>bad()</script>','answer':'<img src=x onerror=bad()>'}]}
    write_report(report,tmp_path/'escaped.json')
    content=(tmp_path/'escaped.html').read_text()
    assert '<script>' not in content and '&lt;script&gt;' in content
