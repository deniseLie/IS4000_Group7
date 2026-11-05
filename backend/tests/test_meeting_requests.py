"""Meeting requests, RM decisions, meeting events and consent-gated brief reads (migrations 0106 and 0107, RM copilot build
plan phase 1) and the demo reset over real temporary PostgreSQL. Synthetic data only.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_meeting_requests.py -q
"""
import json
import os
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from tests.database_support import SqlRow,set_customer
from tests.test_meeting_seam import PRIYA,SOPHIE,as_rm
from tests.test_offer_groups import sqlstate

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
MEI_LING_REQUEST='00000000-0000-4000-8000-100700000071'
MICHELLE_REQUEST='00000000-0000-4000-8000-100200000071'
MICHELLE_GRANT='00000000-0000-4000-8000-100200000041'  # chat_topics since consent v2 (0109)
MICHELLE_INSIGHTS_GRANT='00000000-0000-4000-8000-100200000042'


def free_times(db: 'Connection[SqlRow]',customer: int=1002,n: int=3,exclude: str|None=None,skip: int=0) -> list[datetime]:
    db.execute('reset role')
    rows=db.execute("""select s.starts_at from rm.free_slots(%s,rm.today()+1,rm.today()+40,400) s
        where rm.slot_problem(%s,%s,s.starts_at,%s) is null order by s.starts_at offset %s limit %s""",(SOPHIE,SOPHIE,customer,exclude,skip,n)).fetchall()
    assert len(rows)==n,'Sophie has free times in the next weeks'
    return [row[0] for row in rows]


def iso(times: list[datetime]) -> list[str]:return [t.isoformat() for t in times]


def act(db: 'Connection[SqlRow]',kind: str,target: dict,customer: int=1002) -> dict:
    """Propose and confirm one action as the client, the way a confirmed offer does."""
    set_customer(db,customer)
    session_id,case_id=uuid4(),uuid4()
    db.execute('select public.action_create_case(%s,%s)',(session_id,case_id))
    proposed=db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,kind,json.dumps(target),f'{case_id}:t1:{kind}')).fetchone()[0]
    return db.execute('select public.action_confirm(%s)',(proposed['command']['command_id'],)).fetchone()[0]


def ask(db: 'Connection[SqlRow]',times: list[datetime],topics: list[str]|None=None) -> dict:
    return act(db,'meeting.request',{'advisor_id':SOPHIE,'purpose':'Home loan for a condo next year','topics':topics or ['home_loan'],
        'format':'video','preferred_times':iso(times)})


def decide(db: 'Connection[SqlRow]',request: str,outcome: str,at: datetime|None=None,reason: str|None=None,decision: UUID|None=None) -> dict:
    as_rm(db)
    return db.execute('select public.rm_meeting_request_decide(%s,%s,%s,%s,%s)',(decision or uuid4(),request,outcome,at,reason)).fetchone()[0]


def events(db: 'Connection[SqlRow]',meeting: str) -> list[tuple]:
    db.execute('reset role')
    return db.execute('select event_type,dedupe_key,payload from meetings.events where meeting_id=%s order by event_id',(meeting,)).fetchall()


def test_the_seed_gives_michelle_a_scheduled_home_loan_meeting_and_queues_its_event(database: 'Connection[SqlRow]') -> None:
    # 0109: one v2 grant per category (chat topics keeps the 0106 id)
    grants=database.execute("select grant_id::text,categories,basis from consent.grants where customer_id=1002 and grant_id::text like '00000000-0000-4000-8000-1002000000%%' order by grant_id").fetchall()
    assert grants==[(MICHELLE_GRANT,['chat_topics'],'consent'),(MICHELLE_INSIGHTS_GRANT,['transaction_insights'],'consent'),
        ('00000000-0000-4000-8000-100200000043',['meeting_recording'],'notice')]
    status,meeting=database.execute('select status,appointment_id::text from rm.meeting_requests where request_id=%s',(MICHELLE_REQUEST,)).fetchone()
    assert status=='scheduled'
    row=database.execute('select status,format,purpose,topics,extract(isodow from starts_at at time zone %s) from rm.appointments where appointment_id=%s',
        ('Asia/Singapore',meeting)).fetchone()
    assert row==('confirmed','video','Home loan options',['home_loan','term_sheet','compliance_review'],4)
    [(kind,key,payload)]=events(database,meeting)
    assert kind=='meeting.scheduled' and key=='decision:00000000-0000-4000-8000-100200000072'
    assert payload['request_id']==MICHELLE_REQUEST and payload['topics']==['home_loan','term_sheet','compliance_review'] and 'purpose' not in payload
    assert database.execute('select status from rm.meeting_requests where request_id=%s',(MEI_LING_REQUEST,)).fetchone()==('requested',)


def test_a_client_requests_a_meeting_through_the_ledger_and_is_told_when_to_expect_an_answer(database: 'Connection[SqlRow]') -> None:
    times=free_times(database,n=2)
    outcome=ask(database,times,['term_sheet','home_loan'])
    assert outcome['command']['state']=='succeeded' and outcome['receipt']['result']['status']=='requested'
    request=outcome['receipt']['result']['request_id']
    database.execute('reset role')
    row=database.execute('select status,topics,preferred_times,respond_by>now(),command_id::text from rm.meeting_requests where request_id=%s',(request,)).fetchone()
    assert row==('requested',['home_loan','term_sheet'],times,True,outcome['command']['command_id'])
    event=database.execute("select * from events.outbox where command_id=%s and event_type='meeting.requested'",(outcome['command']['command_id'],)).fetchone()
    assert event is not None
    result=database.execute('select notify.deliver(e) from events.outbox e where e.event_id=%s',(event[0],)).fetchone()[0]
    assert result['disposition']=='delivered'
    body=database.execute('select body from notify.inbox where notification_id=%s',(result['notification_id'],)).fetchone()[0]
    assert outcome['receipt']['result']['respond_by_text'] in body


@pytest.mark.parametrize('change,code',[({'topics':['crypto']},'22023'),({'format':'chat'},'22023'),({'advisor_id':PRIYA},'42501'),
    ({'purpose':'My card is 4111 1111 1111 1111'},None),({'preferred_times':['2026-01-05T03:00:00+08:00']},'22023')])
def test_a_request_needs_her_rm_listed_topics_and_bookable_times(database: 'Connection[SqlRow]',change: dict,code: str|None) -> None:
    target={'advisor_id':SOPHIE,'purpose':'Home loan','topics':['home_loan'],'format':'video','preferred_times':iso(free_times(database,n=1)),**change}
    set_customer(database,1002)
    case_id=uuid4()
    database.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    refused=sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'meeting.request',json.dumps(target),f'{case_id}:t1:meeting.request'))
    assert refused is not None and (code is None or refused==code)


def test_sophie_accepts_a_preferred_time_once_and_the_meeting_side_is_told_once(database: 'Connection[SqlRow]') -> None:
    database.execute('reset role')
    [at]=database.execute('select preferred_times[1] from rm.meeting_requests where request_id=%s',(MEI_LING_REQUEST,)).fetchone()
    decision=uuid4()
    accepted=decide(database,MEI_LING_REQUEST,'accept',at,decision=decision)
    assert accepted['denied'] is False and accepted['request']['status']=='scheduled'
    meeting=accepted['request']['appointment_id']
    database.execute('reset role')
    assert database.execute('select status,topics,purpose is not null from rm.appointments where appointment_id=%s',(meeting,)).fetchone()==('confirmed',['fixed_deposit'],True)
    assert [(e[0],e[1]) for e in events(database,meeting)]==[('meeting.scheduled',f'decision:{decision}')]
    assert decide(database,MEI_LING_REQUEST,'accept',at,decision=decision)['replayed'] is True
    assert len(events(database,meeting))==1
    assert database.execute("select count(*) from notify.inbox where customer_id=1007 and template_key='meeting.accepted'").fetchone()==(1,)
    assert database.execute("select count(*) from audit.events where action='action.rm_meeting_request_decided' and resource_id=%s",(MEI_LING_REQUEST,)).fetchone()==(1,)
    as_rm(database)
    assert sqlstate(database,'select public.rm_meeting_request_decide(%s,%s,%s,%s,%s)',(uuid4(),MEI_LING_REQUEST,'decline',None,'not_needed'))=='P1001'


def test_accepting_needs_a_preferred_time_and_declining_needs_a_reason(database: 'Connection[SqlRow]') -> None:
    [other]=free_times(database,1007,n=1,skip=5)
    as_rm(database)
    for args in [('accept',other,None),('decline',None,None),('decline',None,'too_busy'),('maybe',None,None)]:
        assert sqlstate(database,'select public.rm_meeting_request_decide(%s,%s,%s,%s,%s)',(uuid4(),MEI_LING_REQUEST,*args))=='22023'
    declined=decide(database,MEI_LING_REQUEST,'decline',reason='answered_by_message')
    assert declined['request']['status']=='declined' and declined['request']['decline_reason']=='answered_by_message'
    database.execute('reset role')
    body=database.execute("select body from notify.inbox where customer_id=1007 and template_key='meeting.declined'").fetchone()[0]
    assert 'in a message' in body


@pytest.mark.parametrize('reply',['accept','decline'])
def test_a_proposed_time_is_held_until_the_client_answers(database: 'Connection[SqlRow]',reply: str) -> None:
    times=free_times(database,n=1)
    request=ask(database,times)['receipt']['result']['request_id']
    [other]=free_times(database,n=1,skip=3)
    countered=decide(database,request,'counter',other)
    held=countered['request']['appointment_id']
    assert countered['request']['status']=='countered' and countered['request']['meeting']['status']=='held'
    database.execute('reset role')
    assert database.execute('select rm.slot_problem(%s,1007,%s)',(SOPHIE,other)).fetchone()==('taken',)  # nobody else can book it meanwhile
    outcome=act(database,'meeting.counter_reply',{'request_id':request,'reply':reply})
    assert outcome['command']['state']=='succeeded'
    database.execute('reset role')
    status=database.execute('select status from rm.meeting_requests where request_id=%s',(request,)).fetchone()[0]
    meeting=database.execute('select status from rm.appointments where appointment_id=%s',(held,)).fetchone()[0]
    if reply=='accept':
        assert (status,meeting)==('scheduled','confirmed')
        assert [(e[0],e[1]) for e in events(database,held)]==[('meeting.scheduled',f"command:{outcome['command']['command_id']}")]
    else:
        assert (status,meeting)==('withdrawn','expired') and events(database,held)==[]


def test_withdrawing_an_open_request_frees_nothing_and_cannot_repeat(database: 'Connection[SqlRow]') -> None:
    request=ask(database,free_times(database,n=1))['receipt']['result']['request_id']
    assert act(database,'meeting.withdraw',{'request_id':request})['receipt']['result']['status']=='withdrawn'
    case_id=uuid4()
    database.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',
        (uuid4(),case_id,'meeting.withdraw',json.dumps({'request_id':request}),f'{case_id}:t2:meeting.withdraw'))=='22023'


def test_a_reschedule_and_a_cancellation_queue_their_events_once(database: 'Connection[SqlRow]') -> None:
    database.execute('reset role')
    meeting=str(database.execute('select appointment_id from rm.meeting_requests where request_id=%s',(MICHELLE_REQUEST,)).fetchone()[0])
    [later]=free_times(database,1002,n=1,exclude=meeting,skip=2)
    change=uuid4()
    as_rm(database)
    moved=database.execute('select public.rm_meeting_reschedule(%s,%s,%s,%s)',(change,meeting,later,'client_asked')).fetchone()[0]
    assert moved['replayed'] is False
    assert database.execute('select public.rm_meeting_reschedule(%s,%s,%s,%s)',(change,meeting,later,'client_asked')).fetchone()[0]['replayed'] is True
    outcome=act(database,'appointment.cancel',{'appointment_id':meeting})
    assert outcome['command']['state']=='succeeded'
    kinds=[(e[0],e[1]) for e in events(database,meeting)]
    assert kinds[1:]==[('meeting.rescheduled',f'change:{change}'),('meeting.cancelled',f"command:{outcome['command']['command_id']}")]
    assert events(database,meeting)[1][2]['reason']=='client_asked'


def test_clients_outside_her_book_are_refused_and_clients_cannot_decide(database: 'Connection[SqlRow]') -> None:
    times=free_times(database,1001,n=1)
    database.execute('reset role')
    alex=database.execute("""insert into rm.meeting_requests(customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by)
        values(1001,%s,'Investments','{investments}','video',%s,now()+interval '1 day') returning request_id::text""",(PRIYA,times)).fetchone()[0]
    assert decide(database,alex,'decline',reason='not_needed')['denied'] is True
    database.execute('reset role')
    assert database.execute("select count(*) from audit.events where action='policy.rm_client_denied' and details->>'tool'='rm.meeting_request_decide'").fetchone()==(1,)
    as_rm(database)
    assert all(r['customer_id']!=1001 for r in database.execute("select public.rm_meeting_requests('open')").fetchone()[0]['data'])
    set_customer(database,1002)
    assert sqlstate(database,'select public.rm_meeting_request_decide(%s,%s,%s,%s,%s)',(uuid4(),MEI_LING_REQUEST,'decline',None,'not_needed'))=='42501'


def test_a_client_reads_only_her_own_requests(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1002)
    assert database.execute('select count(*),min(customer_id) from rm.meeting_requests').fetchone()==(1,1002)
    assert [r['request_id'] for r in database.execute('select public.customer_read_meeting_requests(1002)').fetchone()[0]['data']]==[MICHELLE_REQUEST]
    assert sqlstate(database,'select public.customer_read_meeting_requests(1007)',())=='42501'


def test_gated_readers_return_data_only_under_consent_and_audit_the_grant_and_purpose(database: 'Connection[SqlRow]') -> None:
    as_rm(database)
    shared=database.execute("select public.rm_transaction_insights(1002,'meeting_prep')").fetchone()[0]
    assert shared['shared'] is True and shared['grant_id']==MICHELLE_INSIGHTS_GRANT
    assert set(shared['data'])>={'monthly','card_spending','overseas','large_movements','recurring'} and 'description' not in json.dumps(shared['data'])
    gap=database.execute("select public.rm_transaction_insights(1007,'client_view')").fetchone()[0]
    assert gap['shared'] is False and gap['data'] is None and gap['denied'] is False
    # 0107: talking points are always a list, so topics a client shared herself come back without chat_topics (0108 seeds
    # Michelle's inferred home loan point past its hold and Mei Ling's confirmed investments point)
    points=database.execute("select public.rm_talking_points(1002,'meeting_prep')").fetchone()[0]
    assert points['shared'] is True and [(p['topic'],p['origin']) for p in points['data']]==[('home_loan','inferred')]
    topics=database.execute("select public.rm_talking_points(1007,'client_view')").fetchone()[0]
    assert topics['shared'] is False and [(p['topic'],p['origin']) for p in topics['data']]==[('investments','confirmed')]
    cash=database.execute('select public.rm_cash_events(1007)').fetchone()[0]
    assert cash['shared'] is False and cash['data'] is None
    assert isinstance(database.execute('select public.rm_cash_events(1002)').fetchone()[0]['data'],list)
    assert database.execute("select public.rm_transaction_insights(1001,'client_view')").fetchone()[0]['denied'] is True
    for query in ["select public.rm_talking_points(1002,'marketing')","select public.rm_transaction_insights(1002,'marketing')",
            "select public.rm_transaction_insights(1002,null)","select public.rm_transaction_insights(1002,'client_view',7)"]:
        assert sqlstate(database,query,())=='22023'
    assert sqlstate(database,'select public.rm_transaction_insights(1002)',())=='42883'  # the 0106 signature without purpose is gone
    database.execute('reset role')
    def details(action: str,call: str) -> tuple:
        return database.execute('select outcome,details from audit.events where action=%s and resource_id=%s',(action,call)).fetchone()
    assert details('data.read',shared['call_id'])==('success',{'tool':'rm.transaction_insights','result_count':1,'category':'transaction_insights',
        'grant_id':shared['grant_id'],'purpose':'meeting_prep'})
    assert details('policy.rm_consent_gap',gap['call_id'])==('denied',{'tool':'rm.transaction_insights','category':'transaction_insights',
        'purpose':'client_view','reason':'no_active_consent'})
    assert details('policy.rm_consent_gap',topics['call_id'])[1]['purpose']=='client_view'
    assert details('data.read',points['call_id'])[1]=={'tool':'rm.talking_points','result_count':1,'category':'chat_topics','grant_id':MICHELLE_GRANT,
        'purpose':'meeting_prep'}
    assert details('data.read',topics['call_id'])[1]=={'tool':'rm.talking_points','result_count':1,'basis':'client_shared','command_ids':[],
        'purpose':'client_view'}  # the seeded share has no ledger command


def test_the_demo_reset_seeds_the_meetings_again_from_today(database: 'Connection[SqlRow]') -> None:
    # Sophie accepts Mei Ling's request and Michelle withdraws her grant; the reset undoes both and keeps other meetings.
    database.execute('reset role')
    [at]=database.execute('select preferred_times[1] from rm.meeting_requests where request_id=%s',(MEI_LING_REQUEST,)).fetchone()
    accepted=decide(database,MEI_LING_REQUEST,'accept',at)['request']['appointment_id']
    database.execute('reset role')
    database.execute('update consent.grants set revoked_at=now() where grant_id=%s',(MICHELLE_GRANT,))
    assert database.execute('select count(*) from insights.talking_points where customer_id=1002').fetchone()==(0,)  # 0108: withdrawal
    seeded=database.execute('select appointment_id from rm.meeting_requests where request_id=%s',(MICHELLE_REQUEST,)).fetchone()[0]
    others=database.execute('select count(*) from rm.appointments where customer_id=1002 and appointment_id<>%s',(seeded,)).fetchone()[0]
    first=database.execute('select rm.reset_meeting_demo()').fetchone()[0]
    again=database.execute('select rm.reset_meeting_demo()').fetchone()[0]
    assert (first['removed_requests'],first['removed_meetings'],again['removed_requests'],again['removed_meetings'])==(2,2,2,1)
    rows=database.execute('select customer_id,status,appointment_id::text from rm.meeting_requests order by customer_id').fetchall()
    assert [row[:2] for row in rows]==[(1002,'scheduled'),(1007,'requested')]
    meeting=rows[0][2]
    assert database.execute("select status,starts_at>now(),extract(isodow from starts_at at time zone 'Asia/Singapore') from rm.appointments where appointment_id=%s",
        (meeting,)).fetchone()==('confirmed',True,4)
    assert [(e[0],e[1]) for e in events(database,meeting)]==[('meeting.scheduled','decision:00000000-0000-4000-8000-100200000072')]
    assert events(database,accepted)==[] and database.execute('select count(*) from rm.appointments where appointment_id=%s',(accepted,)).fetchone()==(0,)
    assert database.execute('select count(*) from rm.appointments where customer_id=1002 and appointment_id<>%s',(meeting,)).fetchone()==(others,)
    assert database.execute("select count(*) from notify.inbox where customer_id=1007 and template_key like 'meeting.%%'").fetchone()==(0,)
    assert database.execute('select bool_and(t>now()) from rm.meeting_requests r,unnest(r.preferred_times) t where customer_id=1007').fetchone()==(True,)
    assert database.execute('select revoked_at is null and expires_at>now() from consent.grants where grant_id=%s',(MICHELLE_GRANT,)).fetchone()==(True,)
    # 0108: the two demo talking points again (Michelle's inferred home loan past its hold, Mei Ling's shared investments)
    assert (first['talking_points'],again['talking_points'])==(2,2)
    assert database.execute('select customer_id,topic,origin,visible_from<now() from insights.talking_points order by customer_id').fetchall()==[
        (1002,'home_loan','inferred',True),(1007,'investments','confirmed',True)]
    assert database.execute('select count(*) from insights.talking_point_evidence').fetchone()==(1,)
    assert database.execute('select count(*) from rm.topic_shares').fetchone()==(1,)
    assert sqlstate(database,'select rm.reset_meeting_demo(array[1001]::bigint[])',())=='22023'
    set_customer(database,1002)
    assert sqlstate(database,'select rm.reset_meeting_demo()',())=='42501'  # owner only


def test_slot_problems_name_the_reason(database: 'Connection[SqlRow]') -> None:
    [free]=free_times(database,1007,n=1)
    def problem(at: object) -> str|None:return database.execute('select rm.slot_problem(%s,1007,%s::timestamptz)',(SOPHIE,at)).fetchone()[0]
    assert problem(free) is None
    assert problem(database.execute("select (%s::timestamptz at time zone 'Asia/Singapore')::date+time '03:00'",(free,)).fetchone()[0].isoformat()+'+08:00')=='outside_hours'
    assert problem(datetime.now().astimezone().isoformat())=='too_soon'
    database.execute("insert into rm.advisor_blocks(advisor_id,starts_at,ends_at) values(%s,%s,%s::timestamptz+interval '30 minutes')",(SOPHIE,free,free))
    assert problem(free)=='blocked'
    database.execute('delete from rm.advisor_blocks where advisor_id=%s and starts_at=%s',(SOPHIE,free))
    database.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status)
        values(1002,%s,%s,%s::timestamptz+interval '30 minutes','video','confirmed')""",(SOPHIE,free,free))
    assert problem(free)=='taken'
    first,second,third=free_times(database,1007,n=3)  # same week: two meetings already booked there reach the limit
    for at in (first,second):
        database.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status)
            values(1007,%s,%s,%s::timestamptz+interval '30 minutes','video','confirmed')""",(SOPHIE,at,at))
    assert problem(third)=='weekly_limit'


def test_the_chat_draft_is_what_sql_stores_and_the_python_ledger_reads_it_back(database: 'Connection[SqlRow]',monkeypatch: pytest.MonkeyPatch) -> None:
    """2a: the meeting.request offer the chat drafts (agents/meeting_answers) passes the SQL normaliser and validator as is,
    and the recorded command and receipt parse with the Python ledger models (what /commands and the offer outcome read)."""
    import asyncio
    from types import SimpleNamespace
    from app.schemas.actions import ActionOutcome
    from app.schemas.chat import Message
    from app.schemas.contracts import Principal,RequestContext
    from app.schemas.meeting_requests import MeetingRequestResult,MeetingWithdrawResult
    from app.services.agents import meeting_answers
    async def none(ctx):return 0
    monkeypatch.setattr(meeting_answers,'open_requests',none)
    slots=[SimpleNamespace(advisor_id=UUID(SOPHIE),starts_at=t) for t in free_times(database,n=8)]
    records=SimpleNamespace(assignment=SimpleNamespace(data=SimpleNamespace(current=SimpleNamespace(rm_id=UUID(SOPHIE),name='Sophie Lim (DEMO)'))),
        slots=SimpleNamespace(data=slots))
    question='Can I meet Sophie about refinancing my home loan? My card is 4111 1111 1111 1111'
    reply=asyncio.run(meeting_answers.respond('t1',question,[Message(role='user',content=question)],records,
        RequestContext(session_id=uuid4(),principal=Principal(kind='guest')),'meeting_request'))
    sent=ActionOutcome.model_validate(act(database,'meeting.request',reply.proposal.target))
    assert sent.command.state=='succeeded' and isinstance(sent.receipt.result,MeetingRequestResult)
    assert sent.command.target.topics==['card_review','home_loan','term_sheet'] and '4111' not in sent.command.target.purpose
    assert [t for t in sent.command.target.preferred_times]==sorted(datetime.fromisoformat(t) for t in reply.proposal.target['preferred_times'])
    withdrawn=ActionOutcome.model_validate(act(database,'meeting.withdraw',{'request_id':str(sent.receipt.result.request_id)}))
    assert isinstance(withdrawn.receipt.result,MeetingWithdrawResult) and withdrawn.command.target.advisor_id==UUID(SOPHIE)
