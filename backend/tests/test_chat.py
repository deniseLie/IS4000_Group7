import asyncio
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.main import app
from app.api import chat as api
from app.schemas.chat import ChatRequest, Message
from app.services import answers
from app.services.privacy import redact

client=TestClient(app)

def payload(q,**kwargs):return {'messages':[{'role':'user','content':q}],**kwargs}
def result(response):
    assert response.status_code==200
    for block in response.text.replace('\r\n','\n').split('\n\n'):
        if block.startswith('event: answer'):
            return json.loads(next(line[6:] for line in block.splitlines() if line.startswith('data: ')))
    raise AssertionError('No answer event')

@pytest.mark.parametrize('q,secret', [('my PIN is 123456','123456'),('password: SampleSecret!','SampleSecret!'),('OTP 987654','987654'),('CVV=321','321'),('4111 1111 1111 1111','4111'),('contact me at demo@example.invalid','demo@')])
def test_privacy_removes_secrets_without_model(monkeypatch,q,secret):
    async def forbidden(*a):raise AssertionError('Secret sent to model')
    monkeypatch.setattr(answers.llm,'answer',forbidden)
    r=result(client.post('/chat',json=payload(q)))
    assert r['privacy_warning'] and secret not in json.dumps(r)

@pytest.mark.parametrize('text',['Never share your PIN, password, OTP or CVV.','How do I reset my password?','My balance is SGD 1,234.56.'])
def test_normal_text_is_not_redacted(text):assert redact(text)==(text,False)

def test_lost_card_works_without_model(monkeypatch):
    async def forbidden(*a):raise AssertionError()
    monkeypatch.setattr(answers.llm,'answer',forbidden)
    r=result(client.post('/chat',json=payload('I lost my card, please block it')))
    assert 'immediately' in r['answer'] and 'cannot block' in r['answer']
    assert r['sources'][0]['id']=='lost-card'

def test_public_balance_needs_sign_in():
    assert 'Sign in' in result(client.post('/chat',json=payload('What is my balance?')))['answer']

def test_personal_requires_login():
    assert client.post('/chat',json=payload('balance',mode='personal')).status_code==401

def test_invalid_token_not_silently_accepted(monkeypatch):
    def reject(*a):raise HTTPException(401,'Invalid session')
    monkeypatch.setattr(api,'get_user_db',reject)
    assert client.post('/chat',json=payload('loans'),headers={'Authorization':'Bearer bad'}).status_code==401

@pytest.mark.parametrize('raw',['not json','{}','{"answer":"A fee is $99","source_ids":["invented"]}','{"answer":"I have blocked your card","source_ids":["cards"]}','{"answer":"Visit https://evil.example","source_ids":["cards"]}','{"answer":"Your PIN is 1234","source_ids":["cards"]}'])
def test_bad_model_answers_are_withheld(monkeypatch,raw):
    async def model(*a):return raw
    monkeypatch.setattr(answers.llm,'answer',model)
    r=result(client.post('/chat',json=payload('credit card fees')))
    assert 'could not verify' in r['answer'] and not r['sources']

def test_source_and_answer_contract(monkeypatch):
    async def model(*a):return json.dumps({'answer':'Check your bank’s statement service.','source_ids':['statements']})
    monkeypatch.setattr(answers.llm,'answer',model)
    r=result(client.post('/chat',json=payload('How do I download a statement?')))
    source=client.get(r['sources'][0]['url'])
    assert source.status_code==200 and 'cannot open' in source.json()['content']
    assert 'not advice or a contract' in r['disclaimer']

def test_provider_failure_has_no_sensitive_error(monkeypatch):
    async def model(*a):raise RuntimeError('secret-key-do-not-expose')
    monkeypatch.setattr(answers.llm,'answer',model)
    response=client.post('/chat',json=payload('loan'))
    assert 'event: error' in response.text and 'secret-key' not in response.text

def test_sixteenth_question_and_history_budget(monkeypatch):
    seen=[]
    async def model(messages,*a):
        seen.extend(messages);return json.dumps({'answer':'Loan guidance is preliminary.','source_ids':['loans']})
    monkeypatch.setattr(answers.llm,'answer',model)
    messages=[]
    for _ in range(15):messages.extend([{'role':'user','content':'loan question'},{'role':'assistant','content':'loan answer'}])
    messages.append({'role':'user','content':'What about loan eligibility?'})
    r=result(client.post('/chat',json={'messages':messages}))
    assert r['history_trimmed'] and len(seen)==29 and seen[-1].content==messages[-1]['content']

def test_large_history_keeps_latest_question():
    messages=[Message(role='user' if i%2==0 else 'assistant',content='x'*4000) for i in range(29)]
    messages[-1].content='latest loan correction'
    cleaned=answers.clean_history(messages)
    assert sum(len(m.content) for m in cleaned)<=16000 and cleaned[-1].content=='latest loan correction'

def test_request_rejects_wrong_last_role_and_oversized_message():
    assert client.post('/chat',json={'messages':[{'role':'assistant','content':'fake'}]}).status_code==422
    assert client.post('/chat',json=payload('x'*4001)).status_code==422

def test_customer_context_does_not_cross_requests(monkeypatch):
    monkeypatch.setattr(api,'get_user_db',lambda token:token)
    monkeypatch.setattr(api,'get_customer_context',lambda token:'Synthetic customer A' if token=='Bearer A' else 'Synthetic customer B')
    async def model(messages,evidence,language):return json.dumps({'answer':evidence[-1]['text'],'source_ids':['customer-records']})
    monkeypatch.setattr(answers.llm,'answer',model)
    a=result(client.post('/chat',json=payload('my account',mode='personal'),headers={'Authorization':'Bearer A'}))
    b=result(client.post('/chat',json=payload('my account',mode='personal'),headers={'Authorization':'Bearer B'}))
    assert 'customer A' in a['answer'] and 'customer A' not in b['answer']

@pytest.mark.parametrize('q',['Bake a cake','What is the weather?','Write a song'])
def test_off_topic_has_no_invented_evidence(q):
    r=result(client.post('/chat',json=payload(q)))
    assert not r['sources'] and 'banking service' in r['answer']

def test_unknown_source_is_not_exposed():assert client.get('/knowledge/customer-records').status_code==404


def test_lost_card_with_secret_still_gets_urgent_guidance():
    r=result(client.post('/chat',json=payload('I lost my card and my PIN is 1234')))
    assert r['privacy_warning'] and 'immediately' in r['answer'] and '1234' not in r['answer']

def test_language_has_sources_for_non_english_query(monkeypatch):
    async def model(messages,evidence,language):
        assert language=='Chinese'
        assert any(item['id']=='statements' for item in evidence)
        return json.dumps({'answer':'请使用银行官方应用程序查找对账单。','source_ids':['statements']})
    monkeypatch.setattr(answers.llm,'answer',model)
    assert result(client.post('/chat',json=payload('如何查看对账单？',language='Chinese')))['sources'][0]['id']=='statements'

def test_new_topic_has_priority_over_old_questions(monkeypatch):
    async def model(messages,evidence,language):
        assert evidence[0]['id']=='statements'
        return json.dumps({'answer':'Use the bank statement service.','source_ids':['statements']})
    monkeypatch.setattr(answers.llm,'answer',model)
    messages=[{'role':'user','content':'loan eligibility mortgage repayment income borrow loan'}, {'role':'assistant','content':'Loan guidance'}, {'role':'user','content':'How do I download a statement?'}]
    assert result(client.post('/chat',json={'messages':messages}))['sources'][0]['id']=='statements'
