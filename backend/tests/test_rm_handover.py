"""RM handover rules (migration 0073): what goes to the relationship manager instead of the model."""
import pytest
from app.schemas.agents import PlannedIssue
from app.services import rm_handover
from app.services.orchestrator import prioritise,simple_issue


@pytest.mark.parametrize('question',['Should I invest in the new bond fund?','Can you recommend a good fund for my retirement?',
    'Which insurance policy should I buy?','Can you waive the annual fee on my card?','I want a better interest rate on my loan',
    'I want to make a complaint about the branch','Can I talk to my RM?','Please ask my relationship manager to call me back',
    'I need some financial planning advice','I want to transfer 500,000 SGD to my family trust',
    'Can you help me move funds from my holding company to my personal account?','I need to wire $2 million to Switzerland',
    'I need to make a large international transfer for a property purchase'])
def test_questions_for_a_person_go_to_the_rm(question: str) -> None:
    assert rm_handover.needs_person(question)
    assert simple_issue(question,'English')==PlannedIssue(domain='relationship',question=question)


@pytest.mark.parametrize('question',['What is my account balance?','What documents do I still need for my review?',
    'How do I reset my password?','What is the annual fee on my card?','When is my compliance review due?','Who is my relationship manager?',
    'Can I book a meeting with my RM next week?','I want to schedule a call with my relationship manager',
    'How do I make an international transfer?','Why is my transfer to Lim Interiors still pending?','My overseas payment failed, what do I do?',
    'What are the fees for overseas transfers?','Is my large transfer still pending?'])
def test_ordinary_questions_stay_with_the_assistant(question: str) -> None:
    assert not rm_handover.needs_person(question)


def test_handover_is_marked_on_the_task_but_never_on_fraud() -> None:
    issues=[PlannedIssue(domain='fraud',question='I did not make this charge, can you waive the fee?'),
        PlannedIssue(domain='retail',question='Can you waive my late fee?')]
    from app.schemas.contracts import RiskAssessment
    risk=RiskAssessment(redacted_message='x')
    tasks=prioritise({'issues':issues,'risk':risk})['plan'].tasks
    assert 'handover' not in tasks[0].entities and tasks[1].entities['handover']=='rm'


def test_reply_wording_is_plain_and_has_no_internal_codes() -> None:
    text=rm_handover.handed_over('TCK-000123')
    assert 'TCK-000123' in text and 'My requests' in text and '_' not in text
    assert 'not able to help' in rm_handover.SORRY and 'relationship manager' in rm_handover.SORRY
    assert rm_handover.summary_for('  should   I invest? ')=='[Investment or product advice] Client question for you: should I invest?'
    assert 'already have an open request' in rm_handover.handed_over('TCK-000123',reused=True)
    assert 'nothing has been sent' in rm_handover.Handover(reference=None,topic='Complaint',failed=True).reply()
    assert 'Talk to a person' in rm_handover.FAILED


@pytest.mark.parametrize('question,topic',[('Can you waive the annual fee on my card?','Fees or rates'),
    ('I want to complain about the fees you charged','Complaint'),('Should I invest in the new bond fund?','Investment or product advice'),
    ('I want to transfer 500,000 SGD to my family trust','Complex transfer'),('Please ask my relationship manager to call me back','Request to contact you')])
def test_each_handover_has_a_topic_for_matching_open_tickets(question: str,topic: str) -> None:
    assert rm_handover.topic_for(question)==topic
    assert rm_handover.topic_of(rm_handover.summary_for(question))==topic
    assert rm_handover.topic_of('Client question for you: an old ticket') is None


def test_summary_carries_the_clients_earlier_messages_within_the_ticket_limit() -> None:
    from app.schemas.chat import Message
    history=[Message(role='user',content='My card 4111 1111 1111 1111 was charged an annual fee'),
        Message(role='assistant',content='Your balance is SGD 5,169.60.'),Message(role='user',content='x '*400),
        Message(role='user',content='Can you waive the annual fee on my card?')]
    summary=rm_handover.summary_for('Can you waive the annual fee on my card?',history)
    assert summary.startswith('[Fees or rates] Client question for you: Can you waive the annual fee on my card?')
    assert 'Earlier in this chat:' in summary and len(summary)<=500
    assert '4111 1111 1111 1111' not in summary and '5,169.60' not in summary  # redacted; never the assistant's replies
