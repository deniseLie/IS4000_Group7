"""Regression coverage for v8 request-status record access and fallbacks.

The old v7 tests stubbed the data service directly and missed a denied
`tickets.list` permission plus the JSON-vs-typed record shape mismatch.
"""
import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.schemas.contracts import Principal, RequestContext
from app.services.authorization import decide
from app.services import customer_data
from app.services.agents import request_status_answers
from app.services import orchestrator
from app.schemas.agents import PlannedIssue
from app.schemas.contracts import Task
from app.schemas.chat import ChatRequest,Message
from app.schemas.contracts import RiskAssessment


def customer(agent):
    return RequestContext(session_id=uuid4(), agent=agent,
        principal=Principal(kind='customer',user_id=uuid4(),customer_id=1002,jwt='synthetic-test-token'))


def test_retail_can_read_only_customer_ticket_records():
    ctx=customer('retail')
    assert decide(ctx.principal,'tickets.list',{'customer_id':1002},ctx).allow
    assert not decide(ctx.principal,'tickets.list',{'customer_id':1001},ctx).allow
    for tool in ('ticket.create','tickets.open_rm','tickets.follow_up','customer.get_loans.all'):
        assert not decide(ctx.principal,tool,None,ctx).allow
    guest=Principal(kind='guest')
    guest_ctx=RequestContext(session_id=uuid4(),principal=guest)
    assert not decide(guest,'tickets.list',None,guest_ctx).allow


def test_status_uses_typed_ticket_snapshot_not_json(monkeypatch):
    now=datetime(2026,10,31,tzinfo=timezone.utc)
    async def snapshot(_ctx):
        row=SimpleNamespace(reference='TCK-001040',category='card_limit_change',
            category_label='Card limit change',owner_team='Cards and credit team',
            status='resolved',created_at=now)
        return SimpleNamespace(as_of=now,data=[row])
    async def unexpected(_ctx):
        raise AssertionError('JSON transport response should not be used by the status handler')
    monkeypatch.setattr(customer_data,'get_ticket_records',snapshot)
    monkeypatch.setattr(customer_data,'get_tickets',unexpected)
    result,evidence=asyncio.run(request_status_answers.respond('t1',
        'Has the bank approved my credit limit increase?',customer('retail')))
    assert result.status=='ok' and result.text.count('TCK-001040')==1
    assert 'does not confirm' in result.text and 'Cards and credit team' in result.text
    assert [item.id for item in evidence]==['retail-tickets']


def test_status_read_failure_never_asks_for_new_limit_or_claims_approval(monkeypatch):
    from app.services import client_intents
    from app.services.llm_gateway import LLMError
    from app.schemas.workflows import ResponseMeta
    req=SimpleNamespace(mode='personal')
    question='Has the bank approved my credit limit increase?'
    task=Task(task_id='t1',domain='retail',intent='answer_question',priority='P3',entities={'question':question})
    state={'request':req}
    async def failed(*args,**kwargs):
        raise PermissionError('simulated ticket permissions unavailable')
    monkeypatch.setattr(orchestrator,'generate_task',failed)
    monkeypatch.setattr(orchestrator,'log_event',lambda *args,**kwargs:None)
    monkeypatch.setattr(orchestrator.audit,'add',lambda *args,**kwargs:None)
    answer=asyncio.run(orchestrator.dispatch_task(task,state,customer('retail')))
    assert answer['result'].status=='ok'
    assert 'My Requests' in answer['result'].text
    assert 'new credit limit' not in answer['result'].text
    assert 'approved' not in answer['result'].text.lower()
    assert answer['result'].citations==[]
    assert answer['metadata'].origin=='safety'
