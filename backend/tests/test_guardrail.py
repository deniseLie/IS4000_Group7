"""Input assessment, fixed-route integration and shared redaction counts without any AI substitute."""
import asyncio
import json
import pytest
from app.schemas.chat import ChatRequest
from app.services.answers import make_answer
from app.services.guardrail import assess_request


def request(question: str, mode: str) -> ChatRequest:
    return ChatRequest(messages=[{'role': 'user', 'content': question}], mode=mode)


@pytest.mark.parametrize('question', [
    'An unfamiliar card charge appeared', 'I do not recognise this payment',
    "There is a charge I don't recognise", 'I didn’t make this transfer',
    'I see an unauthorized transaction', 'A suspicious payment appeared',
])
def test_suspected_fraud_gets_priority_without_claiming_an_action(question):
    risk = assess_request(request(question, 'public'))
    assert risk.priority == 'P0' and 'fraud_suspected' in risk.risk_flags
    assert risk.fixed_response is None


@pytest.mark.parametrize('question', [
    'What is transaction fraud?', 'I recognise this payment',
    'My transfer is pending', 'How do I dispute a fee?', 'I forgot my password',
])
def test_general_questions_are_not_automatically_fraud(question):
    risk = assess_request(request(question, 'public'))
    assert risk.priority == 'P3' and risk.risk_flags == [] and risk.fixed_response is None


def test_counts_types_and_input_immutability():
    req = request('PIN: 1234; PIN: 4321; CVV=321; OTP 987654; password: example-secret; S1234567D; demo@example.invalid', 'public')
    before = req.model_dump_json()
    risk = assess_request(req)
    assert {r.type: r.count for r in risk.redactions} == {'pin': 2, 'cvv': 1, 'otp': 1, 'password': 1, 'nric': 1, 'email': 1}
    assert req.model_dump_json() == before
    assert risk.priority == 'P1' and risk.fixed_response.code == 'secrets'
    assert '1234' not in risk.redacted_message and 'example-secret' not in risk.redacted_message


def test_history_secrets_are_counted_but_old_topics_do_not_set_current_urgency():
    req = ChatRequest(messages=[{'role': 'user', 'content': 'I lost my card; PIN 1234'},
        {'role': 'assistant', 'content': 'Contact official support.'}, {'role': 'user', 'content': 'Thanks'}])
    risk = assess_request(req)
    assert risk.fixed_response.code == 'secrets' and risk.priority == 'P1'
    assert risk.risk_flags == ['secret_shared'] and risk.redactions[0].count == 1


@pytest.mark.parametrize('question,code', [
    ('I lost my card and PIN 1234', 'secrets'), ('My card was stolen', 'lost_card'),
    ('Please connect me to a human', 'human_support'), ('What is my balance?', 'guest_balance'),
])
def test_fixed_routes_integrate_without_model_calls(question, code):
    req = request(question, 'public')
    assert assess_request(req).fixed_response.code == code
    result = asyncio.run(make_answer(req, allow_model=False))
    assert result['response_origin'] == 'safety' and result['prompt_version'] is None
    assert '1234' not in json.dumps(result)
    if 'card' in question:assert 'immediately' in result['answer']


def test_personal_mode_does_not_get_guest_balance_response():
    assert assess_request(request('What is my balance?', 'personal')).fixed_response is None


def test_lost_card_and_fraud_take_priority_over_a_shared_secret():
    risk = assess_request(request('I lost my card. An unfamiliar charge appeared. PIN: 1234', 'personal'))
    assert risk.priority == 'P0' and set(risk.risk_flags) == {'lost_card', 'fraud_suspected', 'secret_shared'}
    assert risk.fixed_response.code == 'secrets'
