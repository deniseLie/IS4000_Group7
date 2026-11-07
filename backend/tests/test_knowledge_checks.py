"""Approved guidance content and the answer validators.

Article content and retrieval are read from the database (TEST_DATABASE_LOCAL=1; the in-memory copy was removed on
4 Nov 2026, migration 0102). Validator checks pass sample strings directly and need no database.
"""
import asyncio
from decimal import Decimal
import json
import re
from typing import TYPE_CHECKING
import pytest
from app.services import answers
from app.services.source_checks import check_sources, numbers
from app.services.typos import for_search
from evaluation.run import evaluate, write_report
from tests.database_support import SqlRow

if TYPE_CHECKING:
    from psycopg import Connection


def found(db: 'Connection[SqlRow]', question: str) -> list[str]:
    rows=db.execute("select article_id from public.kb_search(%s,'{}'::text[],4)",(for_search(question),)).fetchall()
    return list(dict.fromkeys(row[0] for row in rows))


def article(db: 'Connection[SqlRow]', article_id: str) -> tuple[str,str | None]:
    """The published text of one article (its sections in order) and its reference URL."""
    rows=db.execute('select body,reference_url from public.kb_get_article(%s)',(article_id,)).fetchall()
    assert rows,f'{article_id} has no published, in-review section'
    return ' '.join(row[0] for row in rows),rows[0][1]


@pytest.mark.parametrize('question,expected',[
 ('fixed deposit early withdrawal maturity','sg-accounts'),
 ('credit card minimum payment interest','sg-card-payments'),
 ('secured unsecured loan collateral','sg-loan-types'),
 ('dispute merchant goods not delivered','sg-card-disputes'),
])
def test_topics_retrieve_primary_sources(database,question,expected):
    assert expected in found(database,question)

@pytest.mark.parametrize('question,expected',[
 ('wats the monthly fee for a savings acc?','savings-fees'),
 ('i got locked out of the app on my phone','app-login-recovery'),
 ('can i get a car loan','loans'),
 ('theres a $2400 charge i dont recognise','unrecognised-charge'),
])
def test_workflow_one_questions_retrieve_demo_guidance(database,question,expected):
    assert found(database,question)[0]==expected
    text,url=article(database,expected)
    assert url is None and not answers.URL.search(text)

@pytest.mark.parametrize('question,expected',[
 ('can i get a home loan','home-loans'),('what do i need to refinance my mortgage','home-loans'),
 ('can i borrow against my investments','lombard-financing'),('what is a lombard loan','lombard-financing'),
 ('how do i apply for a personal loan','loans'),('can i borrow for a renovation with a cash loan','loans'),
])
def test_loan_questions_retrieve_their_product_guidance(database,question,expected):
    # 0115: private-client financing (property financing, Lombard); personal and car loans are not offered. The bank
    # decides and no terms are invented. Ranking floors are in test_retrieval_eval.py; here the article only has to reach the model.
    assert expected in found(database,question)
    text,url=article(database,expected)
    assert url is None and not answers.URL.search(text)
    assert 'final decision' in text or 'Only the bank can decide' in text
    if expected!='loans':assert 'not supplied in this demo' in text
    assert set(re.findall(r'[0-9]+',text))<={'1','2','3','4'}

def test_unclear_loan_question_names_the_financing_types_and_asks_which(database):
    assert 'loans' in found(database,'i want a loan')
    text,_=article(database,'loans')
    assert all(kind in text for kind in ('property financing','borrowing against your investments','does not offer personal loans or car loans'))
    assert 'ask which one' in text and 'cannot see applications' in text and 'relationship manager' in text

def test_part_b_guidance_has_no_invented_times_and_names_the_fraud_team(database):
    assert 'transfer-timing' in found(database,"my rent transfer still says pending, will it make it")
    for key in ('transfer-timing','account-security-after-fraud'):
        assert not re.search(r'[0-9]',article(database,key)[0])
    text,_=article(database,'unrecognised-charge')
    assert 'fraud team' in text and 'not proof of fraud' in text

def test_guidance_says_only_the_bank_blocks_cards_and_where_reports_go(database):
    # 0081: the assistant passes reports to the fraud team and other questions to the RM. 0096: the client freezes her
    # own card from her card controls; only the bank cancels one or unfreezes one it froze.
    text={key:article(database,key)[0] for key in ('lost-card','unrecognised-charge','support','card-controls','card-limit-change')}
    for key in ('lost-card','unrecognised-charge','support'):
        assert 'only after you confirm' not in text[key] and 'dispute draft' not in text[key] and 'can offer a temporary block' not in text[key]
    assert 'Freeze card' in text['lost-card'] and 'Only the bank cancels a card' in text['card-controls']
    assert 'steps of SGD 1,000' in text['card-limit-change'] and '60 days' in text['card-controls']
    assert 'fraud team' in text['support'] and 'relationship manager' in text['support']

def test_fixed_safety_replies_follow_the_approved_articles(database):
    # The fixed replies keep their own text and citations so they never wait for the database; neither may drift.
    for key,source in answers.PINNED.items():
        rows=database.execute('select cite,title,publisher from public.kb_get_article(%s)',(key,)).fetchall()
        assert (source['id'],source['title'],source['publisher']) in rows,key
    assert answers.LOST_CARD in article(database,'lost-card')[0]
    assert answers.HUMAN_SUPPORT in article(database,'support')[0]

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


def test_offline_report_has_no_live_calls(database,monkeypatch,tmp_path):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    report=asyncio.run(evaluate(db=database))
    assert report['summary']=={'pass':19,'fail':0,'error':0,'needs_human_review':0},[r for r in report['results'] if r['status']!='pass']
    assert report['mode']=='offline_validator_checks'
    write_report(report,tmp_path/'report.json')
    assert 'not answers generated by a live model' in (tmp_path/'report.html').read_text()
    assert json.loads((tmp_path/'report.json').read_text())['dataset_sha256']


def test_offline_report_needs_a_database():
    with pytest.raises(ValueError):asyncio.run(evaluate())


def test_report_escapes_html(tmp_path):
    report={'mode':'offline','created_at':'test','summary':{},'limits':[], 'results':[{'id':'x','status':'pass','question':'<script>bad()</script>','answer':'<img src=x onerror=bad()>'}]}
    write_report(report,tmp_path/'escaped.json')
    content=(tmp_path/'escaped.html').read_text()
    assert '<script>' not in content and '&lt;script&gt;' in content
