"""Client compliance: clear open-ended help and retain source-checked detailed answers.

These tests are isolated from Supabase and the live LLM. Hosted end-to-end
compliance evaluation remains necessary before a demo.
"""
import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.schemas.contracts import AgentResult, Task
from app.services.agents import compliance


@pytest.mark.parametrize('question', [
    'I need help with my compliance documents.',
    'Can you help me with my compliance review?',
    'I would like some guidance on my KYC documents.',
    'I need assistance with compliance.',
])
def test_general_compliance_help_needs_clarification(question):
    assert compliance.general_help(question)


@pytest.mark.parametrize('question', [
    'What documents are outstanding for my compliance review?',
    'I need help uploading my compliance documents.',
    'What is my compliance review status?',
    'I need help fixing a rejected compliance document.',
    'What is the due date for my compliance review?',
    'I need help with my bank statement.',
])
def test_specific_compliance_questions_are_not_swallowed_by_generic_help(question):
    assert not compliance.general_help(question)


def test_vague_help_does_not_read_private_records_or_call_llm(monkeypatch):
    load=AsyncMock(side_effect=AssertionError('no records needed for this clarification'))
    llm=AsyncMock(side_effect=AssertionError('no LLM needed for this clarification'))
    monkeypatch.setattr(compliance,'load',load)
    monkeypatch.setattr(compliance,'checked_model_answer',llm)
    task=Task(task_id='t1',domain='compliance',intent='answer_question',
        entities={'question':'I need help with my compliance documents.'})
    result,evidence,prompt=asyncio.run(compliance.respond(task,[],'English',SimpleNamespace(),[]))
    assert result.status=='needs_clarification'
    assert result.answer_kind=='conversation'
    assert result.question==result.text
    assert result.citations==[] and evidence==[]
    assert 'upload' in result.text and 'status' in result.text
    assert not load.await_count and not llm.await_count
    assert prompt.startswith(compliance.PROMPT+'+')


def test_specific_compliance_question_uses_record_evidence_and_checked_model(monkeypatch):
    from app.schemas.contracts import AgentResult
    snapshot=SimpleNamespace(data=None,as_of=datetime(2026,10,31,tzinfo=timezone.utc))
    load=AsyncMock(return_value=snapshot)
    checked=AsyncMock(return_value=AgentResult(task_id='t1',agent='compliance',status='needs_human',
                                            answer_kind='conversation',text='No verified record was available.'))
    monkeypatch.setattr(compliance,'load',load)
    monkeypatch.setattr(compliance,'checked_model_answer',checked)
    task=Task(task_id='t1',domain='compliance',intent='answer_question',
        entities={'question':'What is my compliance review status?'})
    result,evidence,_=asyncio.run(compliance.respond(task,[],'English',SimpleNamespace(),[]))
    assert result.status=='needs_human'
    assert len(evidence)==4
    assert {item.id for item in evidence}=={'compliance-case','compliance-items','compliance-history','compliance-requests'}
    assert checked.await_count==1
    assert len(checked.await_args.args[3])==4
    assert load.await_count==1


def test_an_open_review_answer_ends_with_its_recorded_due_date():
    """C07: code adds the due date (cited to the case) when the model left it out; never twice, never once closed."""
    from datetime import date
    from uuid import uuid4
    from app.schemas.contracts import Citation
    from app.schemas.customer_data import ComplianceCase
    case = ComplianceCase(case_id=uuid4(), case_type='periodic_review', title='Periodic review', notice_text='Please send four documents.',
        notice_date=date(2026, 10, 1), due_date=date(2026, 11, 30), status='awaiting_client_documents',
        status_updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc), requested_items=[], status_history=[], info_requests=[])
    ok = AgentResult(task_id='t1', agent='compliance', status='ok', text='You still need to send your proof of address.',
        citations=[Citation(source_id='compliance-items')])
    added = compliance.with_due_date(ok, case)
    assert added.text.endswith('\n\nYour review is due on 30 Nov 2026.')
    assert [c.source_id for c in added.citations] == ['compliance-items', 'compliance-case']
    for said in ('It is due on 30 November 2026.', 'Send it by Nov 30.', 'Due 30th Nov.'):
        same = ok.model_copy(update={'text': said})
        assert compliance.with_due_date(same, case) == same
    assert compliance.with_due_date(ok, case.model_copy(update={'status': 'completed'})) == ok
    assert compliance.with_due_date(ok, case.model_copy(update={'due_date': None})) == ok
    asked = AgentResult(task_id='t1', agent='compliance', status='needs_clarification', answer_kind='conversation', text='Which one?', question='Which one?')
    assert compliance.with_due_date(asked, case) == asked
