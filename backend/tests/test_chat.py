import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
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
    monkeypatch.delenv('LLM_MODEL',raising=False)
    r=result(client.post('/chat',json=payload(q)))
    assert r['privacy_warning'] and secret not in json.dumps(r)

@pytest.mark.parametrize('text',['Never share your PIN, password, OTP or CVV.','How do I reset my password?','My balance is SGD 1,234.56.'])
def test_normal_text_is_not_redacted(text):assert redact(text)==(text,False)

def test_lost_card_works_without_model(monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    r=result(client.post('/chat',json=payload('I lost my card, please block it')))
    assert 'immediately' in r['answer'] and 'cannot block' in r['answer']
    assert r['sources'][0]['id']=='lost-card'

@pytest.mark.parametrize('question',['What is my balance?','whats my balance'])
def test_public_balance_needs_sign_in(question,monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    r=result(client.post('/chat',json=payload(question)))
    assert 'Sign in' in r['answer'] and r['response_origin']=='safety' and r['prompt_version'] is None

def test_personal_requires_login():
    r=client.post('/chat',json=payload('balance',mode='personal'))
    assert r.status_code==401 and r.json()['error']['code']=='auth_required'

def test_invalid_token_not_silently_accepted():
    r=client.post('/chat',json=payload('loans'),headers={'Authorization':'Bearer bad'})
    assert r.status_code==401 and r.json()['error']['code']=='auth_invalid'





def test_large_history_keeps_latest_question():
    messages=[Message(role='user' if i%2==0 else 'assistant',content='x'*4000) for i in range(29)]
    messages[-1].content='latest loan correction'
    cleaned=answers.clean_history(messages)
    assert sum(len(m.content) for m in cleaned)<=16000 and cleaned[-1].content=='latest loan correction'

def test_request_rejects_wrong_last_role_and_oversized_message():
    assert client.post('/chat',json={'messages':[{'role':'assistant','content':'fake'}]}).status_code==422
    assert client.post('/chat',json=payload('x'*4001)).status_code==422



def test_unknown_source_is_not_exposed():assert client.get('/knowledge/customer-records').status_code==404


def test_lost_card_with_secret_still_gets_urgent_guidance():
    r=result(client.post('/chat',json=payload('I lost my card and my PIN is 1234')))
    assert r['privacy_warning'] and 'immediately' in r['answer'] and '1234' not in r['answer']



@pytest.mark.parametrize('raw', ['not json','{}','[]','null',
 '{"answer":"A fee is $99","source_ids":["invented"]}',
 '{"answer":"I have blocked your card","source_ids":["cards"]}',
 '{"answer":"Visit https://evil.example","source_ids":["cards"]}',
 '{"answer":"Your PIN is 1234","source_ids":["cards"]}'])
def test_invalid_output_rejected_directly(raw):
    assert answers.check_answer(raw,[{'id':'cards','text':'No available fees.'}])[2]

@pytest.mark.parametrize('parsed,valid',[
 ({'answer':'Hello! How can I help with banking?','source_ids':[],'answer_kind':'conversation'},True),
 ({'answer':'Sorry that was frustrating. What would you like help with?','source_ids':[],'answer_kind':'conversation'},True),
 ({'answer':'The fee is 199.','source_ids':[],'answer_kind':'conversation'},False),
 ({'answer':'Hello','source_ids':['cards'],'answer_kind':'conversation'},False),
 ({'answer':'A bank fact.','source_ids':[],'answer_kind':'banking'},False),
 ({'answer':'Hello','source_ids':[],'answer_kind':'unknown'},False),
 ({'answer':'Hello','source_ids':'','answer_kind':'conversation'},False),
 ({'answer':'x'*601,'source_ids':[],'answer_kind':'conversation'},False),
 ({'answer':'I have refunded your payment.','source_ids':[],'answer_kind':'conversation'},False),
])
def test_conversation_contract_does_not_relax_banking_checks(parsed,valid):
    assert (answers.check_answer(json.dumps(parsed),[{'id':'cards','text':'Guidance'}])[2] is None)==valid

def test_new_topic_has_priority_and_no_match_does_not_replay_old_topic():
    history=[Message(role='user',content='loan eligibility mortgage repayment income borrow loan')]
    assert answers.retrieve('How do I download a statement?',history,'English')[0].id=='statements'
    result=answers.retrieve('you are useless',history,'English')
    assert {a.id for a in result}=={a.id for a in answers.ARTICLES if answers.is_current(a)}

def test_sixteenth_question_preserves_latest_history():
    messages=[]
    for _ in range(15):messages.extend([Message(role='user',content='loan question'),Message(role='assistant',content='loan answer')])
    messages.append(Message(role='user',content='What about loan eligibility?'))
    cleaned=answers.clean_history(messages)
    assert len(cleaned)==29 and cleaned[-1].content==messages[-1].content

def test_non_english_retrieval_exposes_current_sources():
    assert 'statements' in {a.id for a in answers.retrieve('如何查看对账单？',[],'Chinese')}


def test_unconfigured_provider_returns_error_never_a_sample_answer(monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False)
    response=client.post('/chat',json=payload('Explain card fees'))
    assert 'event: error' in response.text and 'event: answer' not in response.text
    assert 'Model is not configured' not in response.text

def test_offline_mode_cannot_reach_real_model():
    with pytest.raises(RuntimeError,match='disabled for this check'):
        asyncio.run(answers.make_answer(ChatRequest(**payload('card fees')),allow_model=False))
