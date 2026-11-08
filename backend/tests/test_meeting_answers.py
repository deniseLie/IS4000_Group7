"""Meeting requests in the chat (RM copilot build plan 2a): drafts from records, one meeting.request offer, fixed outcome
wording and the ledger shapes of the three meeting actions. No model or database (the open-request count is stubbed)."""
import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from app.schemas.actions import ActionOutcome,CreateAction
from app.schemas.chat import Message
from app.schemas.contracts import OfferProposal,Principal,RequestContext
from app.schemas.meeting_requests import CounterReplyResult,MeetingRequestResult,MeetingRequestTarget
from app.services import authorization,orchestrator,prompts
from app.services.actions.registry import EXECUTORS
from app.services.agents import meeting_answers,registry

SGT=ZoneInfo('Asia/Singapore')
RM=uuid4()
NOW=datetime(2026,11,5,1,0,tzinfo=timezone.utc)  # Thu 5 Nov 2026, 9:00 am Singapore


def sgt(day: int,hour: int,minute: int=0) -> datetime:return datetime(2026,11,day,hour,minute,tzinfo=SGT).astimezone(timezone.utc)


# Two free times a weekday (10:00 am and 2:30 pm Singapore) from Thu 5 Nov to Fri 13 Nov, plus one too soon to offer.
SLOTS=[SimpleNamespace(advisor_id=RM,starts_at=sgt(day,hour,minute)) for day in (5,6,9,10,11,12,13) for hour,minute in ((10,0),(14,30))]


def records(current: bool=True,slots: list | None=None):
    assignment=SimpleNamespace(current=SimpleNamespace(rm_id=RM,name='Sophie Lim (DEMO)') if current else None)
    return SimpleNamespace(assignment=SimpleNamespace(data=assignment),slots=SimpleNamespace(data=SLOTS if slots is None else slots))


def context() -> RequestContext:
    return RequestContext(session_id=uuid4(),received_at=NOW,principal=Principal(kind='guest'))


@pytest.fixture(autouse=True)
def no_open_requests(monkeypatch):
    async def none(ctx):return 0
    monkeypatch.setattr(meeting_answers,'open_requests',none)


def ask(*messages: str,intent: str | None='meeting_request',rec=None):
    history=[Message(role='user' if i%2==0 else 'assistant',content=text) for i,text in enumerate(messages)]
    return asyncio.run(meeting_answers.respond('t1',messages[-1],history,rec or records(),context(),intent))


def local(value: str) -> datetime:return datetime.fromisoformat(value).astimezone(SGT)


def test_a_meeting_ask_drafts_one_request_with_fixed_topics_and_free_times() -> None:
    reply=ask('Can I meet Sophie next week about a home loan?')
    assert reply.result.status=='ok' and [c.source_id for c in reply.result.citations]==['relationship-slots','relationship-assignment']
    proposal=reply.proposal
    assert isinstance(proposal,OfferProposal) and proposal.type=='meeting.request' and proposal.summary==meeting_answers.SUMMARY
    target=proposal.target
    assert target['advisor_id']==str(RM) and target['purpose']=='Can I meet Sophie next week about a home loan?'
    assert target['topics']==['home_loan','term_sheet'] and target['format']=='video'
    times=[local(t) for t in target['preferred_times']]
    assert len(times)==3 and len({t.date() for t in times})==3 and all(9<=t.day<=13 for t in times)  # next week, three days
    text=reply.result.text
    assert 'Sophie Lim' in text and '(DEMO)' not in text and meeting_answers.DRAFTED in text
    assert '• Topics: Property financing, Loan terms (term sheet)' in text and '• Format: video call' in text
    assert 'booked' not in text.lower() and 'sent' not in text.lower()
    MeetingRequestTarget.model_validate(target)  # the shape SQL normalises and validates again at offer time


@pytest.mark.parametrize('question,fmt,topics',[
    ('Could I have a phone call with my RM about investments?','phone',['investments']),
    ('I want to book an in-person meeting with my advisor about my fixed deposit','in_person',['fixed_deposit']),
    ('Can I book a meeting with my RM?','video',['other'])])
def test_format_defaults_to_video_and_topics_to_other(question: str,fmt: str,topics: list[str]) -> None:
    target=ask(question).proposal.target
    assert (target['format'],target['topics'])==(fmt,topics)


def test_the_named_time_is_used_when_free_and_replaced_with_a_note_when_not() -> None:
    exact=ask('Can I book a meeting with my RM on 10 Nov at 10am?').proposal.target['preferred_times']
    assert [local(t) for t in exact]==[datetime(2026,11,10,10,0,tzinfo=SGT)]
    afternoon=ask('Can I meet my RM on Tuesday afternoon about my card?').proposal.target
    assert [local(t) for t in afternoon['preferred_times']]==[datetime(2026,11,10,14,30,tzinfo=SGT)] and afternoon['topics']==['card_review']
    busy=ask('Can I meet my RM on 10 Nov at 7pm?')
    assert busy.result.text.startswith('That time isn’t free with your relationship manager') and len(busy.proposal.target['preferred_times'])==3


def test_times_too_soon_are_never_offered() -> None:
    times=[datetime.fromisoformat(t) for t in ask('Can I book a meeting with my RM?').proposal.target['preferred_times']]
    assert min(times)>NOW+meeting_answers.LEAD
    none=ask('Can I book a meeting with my RM?',rec=records(slots=[SimpleNamespace(advisor_id=RM,starts_at=NOW+timedelta(minutes=90))]))
    assert none.result.text==meeting_answers.NO_TIMES and none.proposal is None


def test_a_reply_to_the_draft_changes_only_what_she_named() -> None:
    first='Can I meet Sophie next week about a home loan?'
    draft=ask(first)
    by_phone=ask(first,draft.result.text,'by phone please')
    assert by_phone.proposal.target['format']=='phone' and by_phone.proposal.target['purpose']==first
    assert by_phone.proposal.target['preferred_times']==draft.proposal.target['preferred_times']
    other=ask(first,draft.result.text,'other times please')
    assert not set(other.proposal.target['preferred_times'])&set(draft.proposal.target['preferred_times'])
    about=ask(first,draft.result.text,'it’s about my fixed deposit')
    assert about.proposal.target['topics']==['fixed_deposit'] and about.proposal.target['purpose']=='it’s about my fixed deposit'


def test_only_replies_about_the_draft_come_back_to_it() -> None:
    draft=ask('Can I meet Sophie next week about a home loan?').result.text
    for reply in ['by phone','in person','Thursday afternoon','tue 10 nov 2pm','other times','it’s about my insurance']:
        history=[Message(role='user',content='x'),Message(role='assistant',content=draft),Message(role='user',content=reply)]
        assert meeting_answers.awaiting(history),reply
        planned=orchestrator.follow_up(reply,history)
        assert (planned.agent,planned.intent)==('relationship','meeting_request')
    for reply in ['whats my card balance','show my recent transactions','when is my next meeting?']:
        assert not meeting_answers.awaiting([Message(role='assistant',content=draft),Message(role='user',content=reply)]),reply


def test_an_availability_question_lists_times_then_her_reply_drafts_the_request() -> None:
    listed=ask('When is my RM free next week?',intent='meeting_times')
    assert listed.proposal is None and meeting_answers.LISTED in listed.result.text
    reply=ask('When is my RM free next week?',listed.result.text,'Tuesday 10am about investments',intent='meeting_request')
    assert [local(t) for t in reply.proposal.target['preferred_times']]==[datetime(2026,11,10,10,0,tzinfo=SGT)]
    assert reply.proposal.target['topics']==['investments']


def test_existing_meetings_no_rm_and_the_open_request_limit(monkeypatch) -> None:
    assert ask('When is my next meeting with my RM?',intent='appointments') is None
    assert ask('Who is my relationship manager?',intent='open') is None
    no_rm=ask('Can I book a meeting with my RM?',rec=records(current=False))
    assert no_rm.result.text==meeting_answers.NO_RM and no_rm.proposal is None
    async def three(ctx):return 3
    monkeypatch.setattr(meeting_answers,'open_requests',three)
    full=ask('Can I book a meeting with my RM?')
    assert full.proposal is None and 'You already have 3 open meeting requests' in full.result.text and full.result.citations==[]


def test_the_purpose_is_redacted_and_at_most_200_characters() -> None:
    assert '4111' not in meeting_answers.purpose_text('meet about card 4111 1111 1111 1111 please')
    long=meeting_answers.purpose_text('I want to meet about my home loan '+'and the refinancing options '*20)
    assert len(long)<=200 and long.endswith('…')


def test_rule_planning_and_the_relationship_spec() -> None:
    assert orchestrator.intent_for('relationship','Can I meet Sophie next week about a home loan?')=='meeting_request'
    assert orchestrator.intent_for('relationship','When is my RM free?')=='meeting_times'
    assert orchestrator.intent_for('relationship','Can I move my meeting with my RM?')=='appointments'
    assert orchestrator.entity_agent('Can I meet Sophie next week about a home loan?')=='relationship'
    spec=registry.SPECS['relationship']
    assert 'meeting.request' in spec.offers and not {'appointment.hold','appointment.confirm'}&spec.offers
    assert [read.tool for read in spec.intents['meeting_request']]==['my_rm','free_slots']
    assert prompts.VERSIONS['planner']=='v11'  # v8's meeting line is kept in v9 (2b) and v10 (batch 3)


def outcome(kind: str,target: dict,result: dict) -> dict:
    now=datetime.now(timezone.utc)
    command_id=uuid4()
    return {'command':{'command_id':str(command_id),'customer_id':1002,'case_id':str(uuid4()),'type':kind,'target':target,'idempotency_key':'k',
        'state':'succeeded','proposed_at':now.isoformat(),'expires_at':(now+timedelta(minutes=30)).isoformat(),'confirmed_at':now.isoformat()},
        'receipt':{'receipt_id':'rcpt_'+uuid4().hex,'command_id':str(command_id),'customer_id':1002,'outcome':'succeeded','result':result,
            'audit_event_id':9,'created_at':now.isoformat()}}


REQUEST={'advisor_id':str(RM),'purpose':'Home loan','topics':['term_sheet','home_loan'],'format':'video',
    'preferred_times':['2026-11-10T10:00:00+08:00','2026-11-09T14:30:00+08:00']}


def test_meeting_actions_are_typed_on_the_ledger_and_need_confirmation() -> None:
    request_id=str(uuid4())
    sent=ActionOutcome.model_validate(outcome('meeting.request',REQUEST,{'request_id':request_id,'status':'requested',
        'respond_by':'2026-11-06T09:42:00Z','respond_by_text':'Fri 6 Nov 2026, 5:42 pm'}))
    assert isinstance(sent.receipt.result,MeetingRequestResult) and sent.command.target.topics==['home_loan','term_sheet']
    assert [t.isoformat() for t in sent.command.target.preferred_times]==['2026-11-09T06:30:00+00:00','2026-11-10T02:00:00+00:00']
    accepted=ActionOutcome.model_validate(outcome('meeting.counter_reply',{'request_id':request_id,'reply':'accept','advisor_id':str(RM)},
        {'request_id':request_id,'status':'scheduled','reply':'accept','appointment_id':str(uuid4()),'starts_at':'2026-11-10T02:00:00Z','when':'Tue 10 Nov'}))
    assert isinstance(accepted.receipt.result,CounterReplyResult)
    with pytest.raises(ValidationError):  # a receipt for another request or another reply never passes
        ActionOutcome.model_validate(outcome('meeting.withdraw',{'request_id':request_id,'advisor_id':str(RM)},{'request_id':str(uuid4()),'status':'withdrawn'}))
    with pytest.raises(ValidationError):
        ActionOutcome.model_validate(outcome('meeting.counter_reply',{'request_id':request_id,'reply':'decline','advisor_id':str(RM)},
            {'request_id':request_id,'status':'scheduled','reply':'accept','appointment_id':str(uuid4()),'starts_at':'2026-11-10T02:00:00Z'}))
    with pytest.raises(ValidationError):MeetingRequestTarget.model_validate({**REQUEST,'preferred_times':REQUEST['preferred_times']*2})
    with pytest.raises(ValidationError):MeetingRequestTarget.model_validate({**REQUEST,'purpose':'x'*201})
    assert {'meeting.request','meeting.withdraw','meeting.counter_reply'}<=set(EXECUTORS)<=set(authorization.KNOWN_TOOLS)
    assert {'meeting.request','meeting.withdraw','meeting.counter_reply'}<=authorization.CONFIRM_TYPES
    form=CreateAction.model_validate({'case_id':str(uuid4()),'task_id':'meetings','command_id':str(uuid4()),'type':'meeting.withdraw','target':{'request_id':request_id}})
    assert str(form.target.request_id)==request_id


def test_a_sent_request_says_when_her_rm_answers_and_where_to_follow_it() -> None:
    sent=ActionOutcome.model_validate(outcome('meeting.request',REQUEST,{'request_id':str(uuid4()),'status':'requested',
        'respond_by':'2026-11-06T09:42:00Z','respond_by_text':'Fri 6 Nov 2026, 5:42 pm'}))
    text=asyncio.run(orchestrator.outcome_text(sent,context()))
    assert text==orchestrator.MEETING_SENT.format(respond_by='Fri 6 Nov 2026, 5:42 pm')
    assert 'Fri 6 Nov 2026, 5:42 pm' in text and 'the Meetings page' in text and 'TCK-' not in text


def test_a_redraft_cancels_only_the_open_meeting_draft(monkeypatch) -> None:
    case,old,mixed=uuid4(),uuid4(),uuid4()
    ctx=context().model_copy(update={'case_id':case})
    groups={orchestrator.offers.group_for(case):[SimpleNamespace(type='meeting.request')],old:[SimpleNamespace(type='meeting.request')],
        mixed:[SimpleNamespace(type='meeting.request'),SimpleNamespace(type='handoff.create')]}
    cancelled=[]
    async def open_groups(current,conversation_id):return groups
    async def cancel(group,current):cancelled.append(group)
    monkeypatch.setattr(orchestrator.offers,'open_groups',open_groups)
    monkeypatch.setattr(orchestrator.offers,'cancel',cancel)
    proposal=OfferProposal(task_id='t1',agent='relationship',type='meeting.request',target=REQUEST,summary='x')
    asyncio.run(orchestrator.superseded([proposal],ctx,uuid4()))
    assert cancelled==[old]
    cancelled.clear()
    asyncio.run(orchestrator.superseded([proposal.model_copy(update={'type':'handoff.create'})],ctx,uuid4()))
    asyncio.run(orchestrator.superseded([proposal],ctx,None))
    assert cancelled==[]
