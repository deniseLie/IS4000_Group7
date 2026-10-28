"""RM handover rules (migration 0073): what goes to the relationship manager instead of the model."""
import pytest
from app.schemas.agents import PlannedIssue
from app.services import rm_handover
from app.services.orchestrator import prioritise,simple_issue


@pytest.mark.parametrize('question',['Should I invest in the new bond fund?','Can you recommend a good fund for my retirement?',
    'Which insurance policy should I buy?','Can you waive the annual fee on my card?','I want a better interest rate on my loan',
    'I want to make a complaint about the branch','Can I talk to my RM?','Please ask my relationship manager to call me back',
    'I need some financial planning advice'])
def test_questions_for_a_person_go_to_the_rm(question: str) -> None:
    assert rm_handover.needs_person(question)
    assert simple_issue(question,'English')==PlannedIssue(domain='relationship',question=question)


@pytest.mark.parametrize('question',['What is my account balance?','What documents do I still need for my review?',
    'How do I reset my password?','What is the annual fee on my card?','When is my compliance review due?','Who is my relationship manager?'])
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
    assert rm_handover.summary_for('  should   I invest? ')=='Client question for you: should I invest?'
