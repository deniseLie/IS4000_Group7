import asyncio
import pytest
from app.schemas.chat import ChatRequest
from app.services.answers import make_answer
from app.services.safety import is_lost_card


@pytest.mark.parametrize('question',[
    'I lost my card', 'My debit card was stolen', 'Someone stole my bank card',
    'I misplaced my credit card', "I can't find my card", 'I can’t find my debit card',
    'My card has been stolen', 'My card is missing', 'I dropped my ATM card',
    'My card went missing', 'My cards were stolen',
    'I need help with a missing card', 'My card got stolen',
    'My card was just stolen', 'I lost my new debit card',
    'I lost my card and my PIN is 1 2 3 4',
])
def test_emergency_variants_work_without_ai(question):
    request=ChatRequest(messages=[{'role':'user','content':question}])
    result=asyncio.run(make_answer(request,allow_model=False))
    assert result['response_origin']=='safety' and result['needs_human']
    assert 'immediately' in result['answer'] and 'cannot block' in result['answer']
    assert 'lost-card' in [s['id'] for s in result['sources']]
    assert '1 2 3 4' not in result['answer']


@pytest.mark.parametrize('question',[
    'My card payment is missing from my statement',
    'I lost interest on my deposit. How do I pay a credit card bill?',
    'My credit card statement is missing', 'How do credit cards work?',
])
def test_non_emergencies_are_not_routed_to_lost_card(question):
    assert not is_lost_card(question)
