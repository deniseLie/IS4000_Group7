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
    assert 'v3' in prompt


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
