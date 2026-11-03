"""Safe status routing and active card-limit context boundaries."""
from app.services import client_intents
from app.services import limit_context
from app.schemas.chat import Message

def test_existing_request_status_is_not_a_new_card_request():
    for text in [
        'Has the bank approved my credit limit increase?',
        'Has my card limit change been approved?',
        'What is the status of my request?',
        'What is the status of TCK-001040?',
        'Was my credit limit change rejected?',
    ]:
        assert client_intents.request_status(text),text
    for text in ['I want my credit limit increased','How do I get approved for a credit limit increase?',
                 'What does a temporary increase mean?']:
        assert not client_intents.request_status(text),text

def test_approval_question_breaks_previous_limit_workflow():
    history=[Message(role='user',content='I want to increase my card limit to SGD 15000'),
             Message(role='assistant',content='Which card is this for?'),
             Message(role='user',content='Has the bank approved my credit limit increase?')]
    assert not limit_context.active(history)


def test_status_answer_uses_owned_records_and_never_infers_approval(monkeypatch):
    import asyncio
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from app.services.agents import request_status_answers as status
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    async def owned(_ctx):
        return SimpleNamespace(as_of=now,data=[SimpleNamespace(reference='TCK-001040',category='card_limit_change',
            category_label='Card limit change',owner_team='Cards and credit team',status='resolved',created_at=now)])
    monkeypatch.setattr(status.customer_data,'get_ticket_records',owned)
    answer,evidence=asyncio.run(status.respond('t1','Has the bank approved my credit limit increase?',object()))
    assert answer is not None and evidence
    assert 'TCK-001040' in answer.text
    assert 'Resolved' in answer.text
    assert 'does not confirm' in answer.text
    assert 'has been approved' not in answer.text
    assert answer.citations[0].source_id=='retail-tickets'

def test_multiple_requests_require_reference(monkeypatch):
    import asyncio
    from datetime import datetime,timezone,timedelta
    from types import SimpleNamespace
    from app.services.agents import request_status_answers as status
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    async def owned(_ctx):
        return SimpleNamespace(as_of=now,data=[SimpleNamespace(reference=f'TCK-0010{n}',category='card_limit_change',
            category_label='Card limit change',owner_team='Cards and credit team',status='in_review',created_at=now+timedelta(minutes=n))
            for n in (40,41)])
    monkeypatch.setattr(status.customer_data,'get_ticket_records',owned)
    answer,_=asyncio.run(status.respond('t1','What is the status of my card limit increase?',object()))
    assert 'Which reference do you mean?' in answer.text
    answer,_=asyncio.run(status.respond('t1','What is the status of TCK-001040?',object()))
    assert 'TCK-001040' in answer.text and 'TCK-001041' not in answer.text
