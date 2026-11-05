"""Consent v2 over real temporary PostgreSQL (migrations 0109-0113, RM copilot build plan phase 6): grants with a scope and
an end, meeting-bound grants, the renewal notice, the recording notice, the wrong advisor and reassignment, and the
Data sharing read. Synthetic data only.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_consent_v2.py -q
"""
import json
import os
import re
from datetime import date,timedelta
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from app.schemas.actions import ActionOutcome
from app.schemas.consent_actions import ConsentGrantV2Result
from tests.database_support import SqlRow,set_customer
from tests.test_meeting_requests import MICHELLE_GRANT,MICHELLE_INSIGHTS_GRANT,MICHELLE_REQUEST,act,decide,free_times,iso
from tests.test_meeting_seam import PRIYA,SOPHIE,as_rm
from tests.test_offer_groups import offer,sqlstate

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
ACCOUNT=10021  # Michelle's multi-currency account (0004)


def today(db: 'Connection[SqlRow]') -> date:
    db.execute('reset role')
    return db.execute('select rm.today()').fetchone()[0]


def share(category: str='transaction_insights',scope: dict|None=None,until: str='date',**extra: object) -> dict:
    notice={'transaction_insights':'ti-2026-11','chat_topics':'ct-2026-11'}[category]
    scope=scope or ({'accounts':'all','lookback_months':6} if category=='transaction_insights' else {'topic_areas':['home_property']})
    return {'category':category,'recipient':SOPHIE,'scope':scope,'notice_version':notice,'until':until,**extra}


def refused(db: 'Connection[SqlRow]',target: dict,customer: int=1002) -> str|None:
    set_customer(db,customer)
    case=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    return sqlstate(db,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case,'consent.grant',json.dumps(target),f'{case}:t1:consent.grant'))


def meeting_of(db: 'Connection[SqlRow]',request: str=MICHELLE_REQUEST) -> str:
    db.execute('reset role')
    return str(db.execute('select appointment_id from rm.meeting_requests where request_id=%s',(request,)).fetchone()[0])


def insights(db: 'Connection[SqlRow]',customer: int=1002,days: int=365) -> dict:
    as_rm(db)
    return db.execute("select public.rm_transaction_insights(%s,'meeting_prep',%s)",(customer,days)).fetchone()[0]


def grant_row(db: 'Connection[SqlRow]',grant: str) -> tuple:
    db.execute('reset role')
    return db.execute('select categories,scope,until_kind,until_meeting_id::text,until_request_id::text,basis,end_reason,revoked_at is null,expires_at '
        'from consent.grants where grant_id=%s',(grant,)).fetchone()


def test_the_catalogue_names_three_gated_categories_and_what_is_always_shared(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1002)
    rows=database.execute('select code,gated,default_on,withdrawable,max_months,notice_version,reason is not null from consent.categories order by sort').fetchall()
    assert rows[:3]==[('transaction_insights',True,False,True,12,'ti-2026-11',False),('chat_topics',True,False,True,12,'ct-2026-11',False),
        ('meeting_recording',True,True,False,None,'mr-2026-11',True)]
    assert [(r[0],r[1],r[6]) for r in rows[3:]]==[('profile_basics',False,True),('products',False,True),('requests_and_meetings',False,True),
        ('compliance_status',False,True)]  # always shared, each with the reason


def test_a_scoped_grant_shows_only_her_chosen_account_and_months_until_her_next_meeting(database: 'Connection[SqlRow]') -> None:
    """Done-when of phase 6: Michelle shares 3 months of one account until her next meeting; Sophie sees only that."""
    database.execute('reset role')
    window=database.execute("""select count(*) filter (where account_id=%s and txn_date>=now()-interval '3 months'),
        count(*) filter (where card_id is not null and amount<0 and txn_date>=now()-interval '6 months')
        from banking.transactions where customer_id=1002 and status in ('posted','pending')""",(ACCOUNT,)).fetchone()
    assert window[1]>0,'Michelle has card spending in the seeded 6 months, so leaving cards out is visible'
    before=insights(database)
    assert before['grant_id']==MICHELLE_INSIGHTS_GRANT and before['data']['scope']['accounts']=='all' and before['data']['card_spending']
    meeting=meeting_of(database)
    outcome=act(database,'consent.grant',share(scope={'accounts':[ACCOUNT],'lookback_months':3},until='meeting',meeting_id=meeting))
    assert outcome['command']['state']=='succeeded'
    parsed=ActionOutcome.model_validate(outcome)  # the Python ledger reads it back
    assert isinstance(parsed.receipt.result,ConsentGrantV2Result) and str(parsed.receipt.result.replaces)==MICHELLE_INSIGHTS_GRANT
    result=outcome['receipt']['result']
    assert result['replaces']==MICHELLE_INSIGHTS_GRANT and result['category_label']=='Transaction insights' and result['recipient_name']
    assert grant_row(database,MICHELLE_INSIGHTS_GRANT)[6:8]==('replaced',False)  # one grant per category and RM
    categories,scope,until,bound,*_=grant_row(database,result['grant_id'])
    assert (categories,scope,until,bound)==(['transaction_insights'],{'accounts':[ACCOUNT],'lookback_months':3},'meeting',meeting)
    after=insights(database)
    assert after['shared'] is True and after['grant_id']==result['grant_id']
    data=after['data']
    assert data['scope']['accounts']==[{'type':'multi_currency','number':'****7734','currency':'SGD'}] and data['scope']['cards'] is False
    assert data['scope']['lookback_months']==3 and data['transactions']==window[0] and data['card_spending']==[]
    assert date.fromisoformat(data['from'])>=today(database)-timedelta(days=93)  # asked for 365 days, got her 3 months
    assert '10021' not in json.dumps(after)  # account ids never reach the RM side
    as_rm(database)
    cash=database.execute('select public.rm_cash_events(1002)').fetchone()[0]
    assert cash['shared'] is True and all(row['date']>=data['from'] for row in cash['data'])
    database.execute('reset role')
    audit=database.execute("select details from audit.events where action='consent.granted' and resource_id=%s",(result['grant_id'],)).fetchone()[0]
    assert audit=={'category':'transaction_insights','until':'meeting','notice_version':'ti-2026-11','replaced':1,'accounts':'1','lookback_months':3,
        'topic_areas':None}


def test_until_meeting_ends_when_the_meeting_side_closes_it_or_the_meeting_is_cancelled(database: 'Connection[SqlRow]') -> None:
    meeting=meeting_of(database)
    granted=act(database,'consent.grant',share(until='meeting',meeting_id=meeting))['receipt']['result']
    database.execute('reset role')
    assert database.execute("select expires_at=a.ends_at+interval '30 days' from consent.grants g,rm.appointments a where g.grant_id=%s and a.appointment_id=%s",
        (granted['grant_id'],meeting)).fetchone()==(True,)  # the backstop if the minutes are never approved
    assert database.execute('select consent.close_meeting_grants(%s)',(meeting,)).fetchone()==(1,)  # minutes.approved on the meeting side
    assert database.execute('select consent.close_meeting_grants(%s)',(meeting,)).fetchone()==(0,)
    assert grant_row(database,granted['grant_id'])[6:8]==('meeting_closed',False)
    gap=insights(database)
    assert gap['shared'] is False and gap['data'] is None
    database.execute('reset role')
    assert database.execute("select details from audit.events where action='consent.closed' and resource_id=%s",(granted['grant_id'],)).fetchone()[0]=={
        'reason':'meeting_closed','meeting_id':meeting,'category':'transaction_insights'}
    assert sqlstate(database,"select consent.close_meeting_grants(%s,'whenever')",(meeting,))=='22023'
    # The meeting runner's role may call it; the app's roles may not.
    assert database.execute("select has_function_privilege('demobank_meetings','consent.close_meeting_grants(uuid,text)','execute'),"
        "has_function_privilege('authenticated','consent.close_meeting_grants(uuid,text)','execute')").fetchone()==(True,False)
    again=act(database,'consent.grant',share(until='meeting',meeting_id=meeting))['receipt']['result']
    database.execute('reset role')
    database.execute("update rm.appointments set status='cancelled' where appointment_id=%s",(meeting,))
    assert grant_row(database,again['grant_id'])[6:8]==('meeting_cancelled',False)


def test_sharing_until_a_requested_meeting_follows_the_request(database: 'Connection[SqlRow]') -> None:
    """The meeting request card's switch: a chained consent.grant after meeting.request, bound to the meeting it becomes."""
    times=free_times(database,n=2)
    set_customer(database,1002)
    case,group=uuid4(),uuid4()
    database.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    offer(database,case,'meeting.request',{'advisor_id':SOPHIE,'purpose':'Home loan for a condo next year','topics':['home_loan'],'format':'video',
        'preferred_times':iso(times)},group,1)
    offer(database,case,'consent.grant',{**share(until='request'),'after_receipt':None},group,2,task='t2')
    offer(database,case,'consent.grant',{**share('chat_topics',until='request'),'after_receipt':None},group,3,task='t3')
    outcomes=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    assert [o['command']['state'] for o in outcomes]==['succeeded']*3
    request=outcomes[0]['receipt']['result']['request_id']
    grants=[o['receipt']['result']['grant_id'] for o in outcomes[1:]]
    assert [o['receipt']['result']['until_request_id'] for o in outcomes[1:]]==[request,request]
    assert all(grant_row(database,g)[2:5]==('request',None,request) for g in grants)
    decide(database,request,'accept',times[0])
    meeting=meeting_of(database,request)
    assert all(grant_row(database,g)[3]==meeting for g in grants)  # scheduled: bound to its meeting
    database.execute('reset role')
    assert database.execute('select consent.close_meeting_grants(%s)',(meeting,)).fetchone()==(2,)
    # Not chained to a request: refused at proposal.
    assert refused(database,share(until='request'))=='22023'


def test_a_withdrawn_request_ends_the_grants_waiting_for_it(database: 'Connection[SqlRow]') -> None:
    times=free_times(database,n=1)
    set_customer(database,1002)
    case,group=uuid4(),uuid4()
    database.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    offer(database,case,'meeting.request',{'advisor_id':SOPHIE,'purpose':'Investments','topics':['investments'],'format':'phone',
        'preferred_times':iso(times)},group,1)
    offer(database,case,'consent.grant',{**share(until='request'),'after_receipt':None},group,2,task='t2')
    outcomes=database.execute('select public.offer_group_confirm(%s)',(group,)).fetchone()[0]
    request,grant=outcomes[0]['receipt']['result']['request_id'],outcomes[1]['receipt']['result']['grant_id']
    assert act(database,'meeting.withdraw',{'request_id':request})['command']['state']=='succeeded'
    assert grant_row(database,grant)[6:8]==('request_closed',False)


def test_end_dates_are_checked_and_expiry_closes_access(database: 'Connection[SqlRow]') -> None:
    day=today(database)
    for expires in [day,day+timedelta(days=400),'2026-02-30']:
        assert refused(database,share(until='date',expires_on=str(expires)))=='22023'
    assert refused(database,share(until='date'))=='22023'  # no date
    granted=act(database,'consent.grant',share(until='date',expires_on=str(day+timedelta(days=30))))['receipt']['result']
    assert date.fromisoformat(granted['expires_at'][:10])>=day+timedelta(days=30)  # through the end of that day in Singapore
    database.execute('reset role')
    database.execute("update consent.grants set expires_at=now()-interval '1 second' where grant_id=%s",(granted['grant_id'],))
    assert database.execute("select rm.consent_active(1002,'transaction_insights',%s)",(SOPHIE,)).fetchone()==(False,)
    assert insights(database)['shared'] is False


def test_a_renewal_notice_comes_seven_days_before_a_dated_grant_ends(database: 'Connection[SqlRow]') -> None:
    day=today(database)
    granted=act(database,'consent.grant',share(until='date',expires_on=str(day+timedelta(days=5))))['receipt']['result']
    database.execute('reset role')
    first=database.execute('select events.enqueue_due_reminders()').fetchone()[0]
    again=database.execute('select events.enqueue_due_reminders()').fetchone()[0]
    events=database.execute("select payload from events.outbox where event_type='consent.renewal_due' and customer_id=1002").fetchall()
    assert first>=1 and [e[0]['grant_id'] for e in events]==[granted['grant_id']] and again<first
    delivered=database.execute("select notify.deliver(o) from events.outbox o where event_type='consent.renewal_due' and customer_id=1002").fetchone()[0]
    assert delivered['disposition']=='delivered'
    title,body=database.execute("select title,body from notify.inbox where customer_id=1002 and template_key='consent.renewal_due'").fetchone()
    assert title=='Sharing ends soon' and 'transaction insights' in body and 'Sophie Lim' in body and 'Data sharing' in body
    # A replaced grant's notice is stale.
    act(database,'consent.grant',share(until='date',expires_on=str(day+timedelta(days=60))))
    database.execute('reset role')
    database.execute('delete from notify.inbox where customer_id=1002 and template_key=%s',('consent.renewal_due',))
    assert database.execute("select notify.deliver(o)->>'disposition' from events.outbox o where event_type='consent.renewal_due' and customer_id=1002").fetchone()==('stale',)


def test_only_her_current_rm_and_the_current_notice_can_be_granted(database: 'Connection[SqlRow]') -> None:
    assert refused(database,{**share(),'recipient':PRIYA,'until':'date','expires_on':str(today(database)+timedelta(days=30))})=='42501'
    assert refused(database,{**share(until='date',expires_on=str(today(database)+timedelta(days=30))),'notice_version':'ti-2025-01'})=='22023'
    assert refused(database,share(scope={'accounts':[10011],'lookback_months':3},until='date',expires_on=str(today(database)+timedelta(days=30))))=='42501'  # Alex's
    assert refused(database,share(scope={'accounts':'all','lookback_months':9},until='date',expires_on=str(today(database)+timedelta(days=30))))=='22023'
    assert refused(database,share('chat_topics',{'topic_areas':['health']},until='date',expires_on=str(today(database)+timedelta(days=30))))=='22023'
    assert refused(database,{**share(until='date',expires_on=str(today(database)+timedelta(days=30))),'category':'meeting_recording'})=='22023'


def test_reassignment_ends_access_and_the_new_rm_needs_her_own_grant(database: 'Connection[SqlRow]') -> None:
    database.execute('reset role')
    assert database.execute("select rm.consent_active(1002,'transaction_insights',%s)",(SOPHIE,)).fetchone()==(True,)
    database.execute("update rm.assignments set ended_on=rm.today() where customer_id=1002 and advisor_id=%s and ended_on is null",(SOPHIE,))
    database.execute("insert into rm.assignments(customer_id,advisor_id,started_on) values(1002,%s,rm.today())",(PRIYA,))
    assert database.execute("select rm.consent_active(1002,'transaction_insights',%s),rm.consent_active(1002,'transaction_insights',%s)",
        (SOPHIE,PRIYA)).fetchone()==(False,False)
    assert insights(database)['denied'] is True  # Sophie: no longer her client
    assert refused(database,share(until='date',expires_on=str(today(database)+timedelta(days=30))))=='42501'  # Sophie is not her RM now
    granted=act(database,'consent.grant',{**share(until='date',expires_on=str(today(database)+timedelta(days=30))),'recipient':PRIYA})
    assert granted['command']['state']=='succeeded' and granted['receipt']['result']['replaces'] is None
    database.execute('reset role')
    assert database.execute("select rm.consent_active(1002,'transaction_insights',%s)",(PRIYA,)).fetchone()==(True,)


def test_meeting_recording_is_on_under_the_meeting_notice_and_cannot_be_revoked(database: 'Connection[SqlRow]') -> None:
    database.execute('reset role')
    assert database.execute("select count(*) from consent.grants where customer_id=1007 and 'meeting_recording'=any(categories)").fetchone()==(0,)
    [at]=free_times(database,1007,n=1)
    database.execute('reset role')
    meeting=database.execute("""insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status)
        values(1007,%s,%s,%s::timestamptz+interval '30 minutes','video','confirmed') returning appointment_id,recording_notice_version""",(SOPHIE,at,at)).fetchone()
    assert meeting[1]=='mr-2026-11'
    grant,basis,version,until=database.execute("""select grant_id::text,basis,notice_version,until_kind from consent.grants
        where customer_id=1007 and categories=array['meeting_recording']""").fetchone()
    assert (basis,version,until)==('notice','mr-2026-11',None)
    assert database.execute("select rm.consent_active(1007,'meeting_recording',%s)",(SOPHIE,)).fetchone()==(True,)
    set_customer(database,1002)  # Michelle's seeded recording grant is notice-based too
    case=uuid4()
    database.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    assert sqlstate(database,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',
        (uuid4(),case,'consent.revoke',json.dumps({'grant_id':'00000000-0000-4000-8000-100200000043'}),f'{case}:t1:consent.revoke'))=='22023'


def test_revoking_a_v2_grant_records_why_it_ended(database: 'Connection[SqlRow]') -> None:
    outcome=act(database,'consent.revoke',{'grant_id':MICHELLE_INSIGHTS_GRANT})
    assert outcome['command']['state']=='succeeded' and outcome['receipt']['result']['status']=='revoked'
    ActionOutcome.model_validate(outcome)
    assert grant_row(database,MICHELLE_INSIGHTS_GRANT)[6:8]==('revoked',False)
    gap=insights(database)
    assert gap['shared'] is False and gap['data'] is None


def test_chat_topic_areas_limit_the_inferred_topics_she_shares(database: 'Connection[SqlRow]') -> None:
    as_rm(database)
    assert [p['topic'] for p in database.execute("select public.rm_talking_points(1002,'meeting_prep')").fetchone()[0]['data']]==['home_loan']
    granted=act(database,'consent.grant',share('chat_topics',{'topic_areas':['investments','deposits']},until='date',
        expires_on=str(today(database)+timedelta(days=30))))['receipt']['result']
    assert granted['replaces']==MICHELLE_GRANT and granted['scope']=={'topic_areas':['investments','deposits']}
    as_rm(database)
    shown=database.execute("select public.rm_talking_points(1002,'meeting_prep')").fetchone()[0]
    assert shown['shared'] is True and shown['data']==[] and shown['pending'] is False  # home_loan is outside her areas


def test_data_sharing_reads_her_cards_accounts_and_next_meeting_under_her_jwt(database: 'Connection[SqlRow]') -> None:
    set_customer(database,1002)
    data=database.execute('select public.customer_read_consent_sharing(1002)').fetchone()[0]['data']
    assert data['rm']['advisor_id']==SOPHIE and data['rm']['name']=='Sophie Lim'
    cards={c['code']:c for c in data['categories']}
    assert cards['transaction_insights']['grant']['grant_id']==MICHELLE_INSIGHTS_GRANT and cards['transaction_insights']['grant']['scope']=={
        'accounts':'all','lookback_months':6}
    assert cards['meeting_recording']['grant']['basis']=='notice' and cards['products']['grant'] is None and not cards['products']['gated']
    database.execute('reset role')
    first=database.execute("select appointment_id::text from rm.appointments where customer_id=1002 and advisor_id=%s and status='confirmed' "
        "and starts_at>now() order by starts_at limit 1",(SOPHIE,)).fetchone()[0]
    assert [a['account_id'] for a in data['accounts']]==[ACCOUNT] and data['next_meeting']['meeting_id']==first
    assert data['topic_areas']==['home_property','investments','deposits','insurance','cards','everyday_banking']
    set_customer(database,1002)
    assert sqlstate(database,'select public.customer_read_consent_sharing(1003)',())=='42501'


# 6b (0110): topic deletion with undo -----------------------------------------------------------------------------------
MICHELLE_POINT='00000000-0000-4000-8000-100200000091'
MEI_LING_POINT='00000000-0000-4000-8000-100700000091'


def topics(db: 'Connection[SqlRow]',customer: int=1002) -> dict:
    as_rm(db)
    return db.execute("select public.rm_talking_points(%s,'meeting_prep')",(customer,)).fetchone()[0]


def status(db: 'Connection[SqlRow]',points: list[str],customer: int=1002) -> list[tuple]:
    as_rm(db)
    data=db.execute('select public.rm_point_status(%s,%s::uuid[])',(customer,points)).fetchone()[0]['data']
    return [(row['status'],row['removed_on'] is not None) for row in data]


def automatic(db: 'Connection[SqlRow]',kind: str,target: dict,customer: int=1002) -> dict:
    set_customer(db,customer)
    case=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    return db.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case,kind,json.dumps(target),f'{case}:t1:{kind}')).fetchone()[0]


def purge(db: 'Connection[SqlRow]') -> int:
    db.execute('reset role')
    db.execute("update insights.talking_points set purge_after=now()-interval '1 second' where deleted_at is not null")
    db.execute('set local role demobank_worker')  # the worker's role (app/worker.py calls it every minute)
    try:return db.execute('select events.purge_removed_topics()').fetchone()[0]
    finally:db.execute('reset role')


def test_her_delete_hides_a_topic_at_once_undo_brings_it_back_and_the_purge_ends_it(database: 'Connection[SqlRow]') -> None:
    assert [p['point_id'] for p in topics(database)['data']]==[MICHELLE_POINT]
    deleted=automatic(database,'insight.delete',{'point_id':MICHELLE_POINT})
    assert deleted['command']['state']=='succeeded' and deleted['receipt']['result']['status']=='deleted' and deleted['receipt']['result']['restore_until']
    ActionOutcome.model_validate(deleted)
    shown=topics(database)
    assert shown['shared'] is True and shown['data']==[] and shown['pending'] is False  # gone from every RM read at once
    assert status(database,[MICHELLE_POINT])==[('removed',True)]
    set_customer(database,1002)
    mine=database.execute('select public.customer_read_talking_points(1002)').fetchone()[0]['data']
    assert mine['points']==[] and [(p['point_id'],p['reason'],p['can_restore']) for p in mine['removed']]==[(MICHELLE_POINT,'client',True)]
    assert refused_point(database,'insight.delete',MICHELLE_POINT)=='22023'  # already removed
    restored=automatic(database,'insight.restore',{'point_id':MICHELLE_POINT})
    assert restored['receipt']['result']=={'point_id':MICHELLE_POINT,'topic':'home_loan','status':'restored'}
    ActionOutcome.model_validate(restored)
    assert [p['point_id'] for p in topics(database)['data']]==[MICHELLE_POINT] and status(database,[MICHELLE_POINT])==[('shown',False)]
    automatic(database,'insight.delete',{'point_id':MICHELLE_POINT})
    assert purge(database)==1
    assert database.execute('select count(*) from insights.talking_points where point_id=%s',(MICHELLE_POINT,)).fetchone()==(0,)
    assert database.execute('select count(*) from insights.talking_point_evidence where point_id=%s',(MICHELLE_POINT,)).fetchone()==(0,)
    assert database.execute("select details,actor_type from audit.events where action='data.insight_purged' and resource_id=%s",(MICHELLE_POINT,)).fetchone()==(
        {'reason':'client'},'system')
    assert status(database,[MICHELLE_POINT,MEI_LING_POINT])==[('removed',True),('unknown',False)]  # still removed after the purge; one client per call
    database.execute('reset role')
    assert refused_point(database,'insight.restore',MICHELLE_POINT)=='42501'  # purged: nothing to restore


def refused_point(db: 'Connection[SqlRow]',kind: str,point: str) -> str|None:
    set_customer(db,1002)
    case=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    return sqlstate(db,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case,kind,json.dumps({'point_id':point}),f'{case}:t1:{kind}'))


def test_a_deleted_topic_is_not_inferred_again_before_it_is_purged(database: 'Connection[SqlRow]') -> None:
    from tests.test_talking_points_db import record,saved_turn
    automatic(database,'insight.delete',{'point_id':MICHELLE_POINT})
    assert record(database,saved_turn(database,'Still planning to buy a condo'),'home_loan','planning','buy a condo')['reason']=='removed'
    purge(database)
    assert record(database,saved_turn(database,'Still planning to buy a condo'),'home_loan','planning','buy a condo')['recorded'] is True


def test_revoking_chat_topics_removes_inferred_topics_and_keeps_the_ones_she_shared(database: 'Connection[SqlRow]') -> None:
    from tests.test_talking_points_db import record,saved_turn
    act(database,'insight.share',{'topic':'insurance','stage':'planning','timeframe':'unknown'})
    record(database,saved_turn(database,'Planning to invest'),'investments','planning','invest')
    revoked=act(database,'consent.revoke',{'grant_id':MICHELLE_GRANT})
    assert revoked['command']['state']=='succeeded'
    database.execute('reset role')
    rows=database.execute('select topic,origin,deleted_reason from insights.talking_points where customer_id=1002 order by topic').fetchall()
    assert rows==[('home_loan','inferred','chat_topics_revoked'),('insurance','confirmed',None),('investments','inferred','chat_topics_revoked')]
    assert database.execute('select count(*) from insights.talking_point_evidence where customer_id=1002').fetchone()==(0,)  # her words go at once
    gap=topics(database)
    assert gap['shared'] is False and [(p['topic'],p['origin']) for p in gap['data']]==[('insurance','confirmed')]
    database.execute('reset role')
    assert database.execute("select outcome,details->>'reason' from audit.events where action='policy.rm_consent_gap' and resource_id=%s",
        (gap['call_id'],)).fetchone()==('denied','no_active_consent')  # the copilot shows the gap; the refusal is audited
    assert record(database,saved_turn(database,'Ready to invest'),'investments','ready','invest')['reason']=='no_consent'  # extraction stops
    assert refused_point(database,'insight.restore',MICHELLE_POINT)=='22023'  # only her own delete has an undo
    # Sharing chat topics again: the removed points are not brought back, new ones are recorded afresh.
    act(database,'consent.grant',share('chat_topics',{'topic_areas':['investments']},until='date',expires_on=str(today(database)+timedelta(days=30))))
    assert record(database,saved_turn(database,'Ready to invest'),'investments','ready','invest')['recorded'] is True


def test_narrowing_her_topic_areas_removes_the_inferred_topics_outside_them(database: 'Connection[SqlRow]') -> None:
    from tests.test_talking_points_db import record,saved_turn
    act(database,'consent.grant',share('chat_topics',{'topic_areas':['investments']},until='date',expires_on=str(today(database)+timedelta(days=30))))
    database.execute('reset role')
    assert database.execute('select deleted_reason from insights.talking_points where point_id=%s',(MICHELLE_POINT,)).fetchone()==('scope_narrowed',)
    assert record(database,saved_turn(database,'Buying a condo next year'),'home_loan','planning','condo')['reason']=='out_of_scope'
    assert record(database,saved_turn(database,'Planning to invest'),'investments','planning','invest')['recorded'] is True
    database.execute('reset role')
    assert sqlstate(database,"insert into insights.talking_points(customer_id,topic,stage,origin,visible_from,expires_at) "
        "values(1002,'health','curious','inferred',now(),now())",())=='23514'  # never stored


# 6d (0112, 0113): the access log, the RM's ask and Delete all my data -------------------------------------------------
LOG_KEYS={'call_id','at','advisor_id','relation','tool','category','purpose','meeting_id','outcome','basis','grant_id'}
CODE=r'^[a-z][a-z0-9_.]{0,60}$'
UUID_RE=r'^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'


def access_log(db: 'Connection[SqlRow]',customer: int=1002) -> list[dict]:
    set_customer(db,customer)
    return db.execute('select public.my_consent_access_log(%s,50)',(customer,)).fetchone()[0]['data']


def rm_read(db: 'Connection[SqlRow]',sql: str,*args: object) -> dict:
    as_rm(db)
    try:return db.execute(f'select public.{sql}',args).fetchone()[0]
    finally:db.execute('reset role')


def ask(db: 'Connection[SqlRow]',customer: int,category: str,reason: str='review',meeting: str|None=None,key: str|None=None) -> dict:
    as_rm(db)
    try:return db.execute('select public.rm_ask_consent(%s,%s,%s,%s,%s)',(key or str(uuid4()),customer,category,reason,meeting)).fetchone()[0]
    finally:db.execute('reset role')


def test_the_access_log_shows_who_read_what_and_when_in_ids_codes_and_dates_only(database: 'Connection[SqlRow]') -> None:
    rm_read(database,'rm_client_profile(%s)',1002)
    shared=rm_read(database,"rm_transaction_insights(%s,'meeting_prep',180)",1002)
    act(database,'consent.revoke',{'grant_id':MICHELLE_INSIGHTS_GRANT})
    gap=rm_read(database,"rm_transaction_insights(%s,'client_view',180)",1002)
    assert rm_read(database,'rm_client_profile(%s)',1001)['denied'] is True  # Alex is Priya's client
    rows=access_log(database)
    for row in rows:  # ids, codes and dates only: never a record, an amount or a name
        assert set(row)==LOG_KEYS
        for key,value in row.items():
            assert value is None or (re.match(UUID_RE,value) if key in ('call_id','advisor_id','meeting_id','grant_id') else
                re.match(r'^\d{4}-\d\d-\d\dT',value) if key=='at' else re.match(CODE,value)),(key,value)
    latest,read,profile=rows[0],next(r for r in rows if r['call_id']==shared['call_id']),next(r for r in rows if r['tool']=='rm.client_profile')
    assert (latest['call_id'],latest['outcome'],latest['category'],latest['purpose'],latest['basis'],latest['relation'],latest['advisor_id'])==(
        gap['call_id'],'not_shared','transaction_insights','client_view',None,'current_rm',SOPHIE)
    database.execute('reset role')
    meeting=database.execute("""select appointment_id::text from rm.appointments where customer_id=1002 and advisor_id=%s and status='confirmed'
        and starts_at>=now() and starts_at<now()+interval '14 days' order by starts_at limit 1""",(SOPHIE,)).fetchone()
    assert (read['outcome'],read['basis'],read['grant_id'],read['purpose'],read['meeting_id'])==(
        'read','consent',MICHELLE_INSIGHTS_GRANT,'meeting_prep',meeting[0] if meeting else None)
    assert (profile['category'],profile['basis'],profile['purpose'])==('profile_basics','always_shared',None)
    assert {r['call_id'] for r in access_log(database,1003)}&{r['call_id'] for r in rows}==set()  # only her own
    [alex]=[r for r in access_log(database,1001) if r['outcome']=='refused']
    assert (alex['relation'],alex['advisor_id'],alex['category'],alex['call_id'])==('not_your_rm',SOPHIE,None,None)
    database.execute('reset role')
    assert database.execute("select count(*) from audit.events where action='data.read' and details->>'tool'='consent.access_log' and actor_type='customer'"
        ).fetchone()==(3,)  # her reads of the log are audited like her other reads
    as_rm(database)
    assert sqlstate(database,'select public.my_consent_access_log(1002,50)',())=='42501'  # an RM has no customer log


def test_sophie_asks_with_a_reason_and_michelle_answers_by_switching_it_on(database: 'Connection[SqlRow]') -> None:
    act(database,'consent.revoke',{'grant_id':MICHELLE_INSIGHTS_GRANT})
    meeting=meeting_of(database)
    key=str(uuid4())
    asked=ask(database,1002,'transaction_insights','meeting_prep',meeting,key)
    assert asked['created'] is True and asked['replayed'] is False and asked['ask']['status']=='open' and asked['ask']['meeting_id']==meeting
    assert ask(database,1002,'transaction_insights','meeting_prep',meeting,key)['replayed'] is True  # a double click records once
    assert ask(database,1002,'transaction_insights')['duplicate']['ask_id']==asked['ask']['ask_id']  # one open ask per category
    ticket=database.execute('select category,status,source,summary,target_at-created_at from support.tickets where reference=%s',(asked['ask']['reference'],)).fetchone()
    assert ticket[:3]==('rm_consent_request','waiting_on_client','rm')
    assert ticket[3].startswith('Sophie Lim asked you to share transaction insights to prepare for your meeting on') and ticket[4].days==14
    title,body=database.execute("select title,body from notify.inbox where customer_id=1002 and template_key='consent.rm_asked'").fetchone()
    assert title=='Sophie Lim asked you to share transaction insights' and 'Nothing is shared unless you switch it on in Data sharing' in body
    assert database.execute("select actor_type,details from audit.events where action='consent.rm_asked'").fetchone()==(
        'staff',{'category':'transaction_insights','reason':'meeting_prep','meeting_id':meeting,'reference':asked['ask']['reference']})
    assert insights(database)['shared'] is False  # an ask shares nothing
    set_customer(database,1002)
    [mine]=database.execute('select public.customer_read_consent_asks(1002)').fetchone()[0]['data']
    assert (mine['status'],mine['rm_name'],mine['category_label'],mine['reason'],mine['meeting_id'])==('open','Sophie Lim','Transaction insights','meeting_prep',meeting)
    granted=act(database,'consent.grant',share('transaction_insights',until='date',expires_on=str(today(database)+timedelta(days=30))))
    database.execute('reset role')
    status,grant=database.execute('select status,grant_id::text from consent.asks where ask_id=%s',(asked['ask']['ask_id'],)).fetchone()
    assert status=='shared' and grant==granted['receipt']['result']['grant_id']
    assert database.execute('select status from support.tickets where reference=%s',(asked['ask']['reference'],)).fetchone()==('resolved',)
    assert database.execute("select details from audit.events where action='consent.ask_answered'").fetchone()==(
        {'category':'transaction_insights','answer':'shared','grant_id':grant},)
    assert ask(database,1002,'transaction_insights')=={'denied':False,'created':False,'already_shared':True,'category':'transaction_insights'}


def refused_ask(db: 'Connection[SqlRow]',ask_id: str,customer: int) -> str|None:
    set_customer(db,customer)
    case=uuid4()
    db.execute('select public.action_create_case(%s,%s)',(uuid4(),case))
    return sqlstate(db,'select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case,'consent.decline_ask',json.dumps({'ask_id':ask_id}),
        f'{case}:t1:consent.decline_ask'))


def test_she_can_decline_an_ask_and_one_she_does_not_answer_lapses_after_14_days(database: 'Connection[SqlRow]') -> None:
    act(database,'consent.revoke',{'grant_id':MICHELLE_GRANT})  # chat topics
    first=ask(database,1002,'chat_topics','advice')
    declined=automatic(database,'consent.decline_ask',{'ask_id':first['ask']['ask_id']})
    assert declined['receipt']['result']=={'ask_id':first['ask']['ask_id'],'category':'chat_topics','recipient':SOPHIE,'status':'declined'}
    ActionOutcome.model_validate(declined)
    database.execute('reset role')
    assert database.execute('select status from support.tickets where reference=%s',(first['ask']['reference'],)).fetchone()==('closed',)
    assert database.execute("select count(*) from notify.inbox where customer_id=1002 and template_key='consent.rm_asked'").fetchone()==(1,)
    assert refused_ask(database,first['ask']['ask_id'],1002)=='22023'  # already answered
    database.execute('reset role')
    assert refused_ask(database,first['ask']['ask_id'],1003)=='42501'  # not hers
    database.execute('reset role')
    again=ask(database,1002,'chat_topics','review')  # she said no once; Sophie may ask again later
    assert again['created'] is True
    database.execute("update consent.asks set asked_at=now()-interval '15 days',lapses_at=now()-interval '1 day' where ask_id=%s",(again['ask']['ask_id'],))
    database.execute('set local role demobank_worker')
    database.execute('select events.enqueue_due_reminders()')  # the worker's minute
    database.execute('reset role')
    assert database.execute('select status from consent.asks where ask_id=%s',(again['ask']['ask_id'],)).fetchone()==('lapsed',)
    assert database.execute('select status from support.tickets where reference=%s',(again['ask']['reference'],)).fetchone()==('closed',)
    assert database.execute("select actor_type,details from audit.events where action='consent.ask_lapsed'").fetchone()==('system',{'category':'chat_topics'})
    assert ask(database,1003,'chat_topics')['denied'] is True  # David is not in her book: refused and audited
    for args in [('profile_basics','review',None),('chat_topics','meeting_prep',None),('chat_topics','because',None),('chat_topics','meeting_prep',str(uuid4()))]:
        as_rm(database)
        assert sqlstate(database,'select public.rm_ask_consent(%s,1002,%s,%s,%s)',(str(uuid4()),*args))==('42501' if args[2] else '22023')
        database.execute('reset role')


def test_an_ask_never_leaks_her_records_to_another_client_or_the_rm(database: 'Connection[SqlRow]') -> None:
    """Red team: the ask, its ticket and notice carry codes and fixed wording only; another client sees none of it; the RM
    still gets the gap until she switches it on; her access log is hers alone."""
    act(database,'consent.revoke',{'grant_id':MICHELLE_INSIGHTS_GRANT})
    asked=ask(database,1002,'transaction_insights','review')
    details=database.execute('select details from support.tickets where reference=%s',(asked['ask']['reference'],)).fetchone()[0]
    assert details=={'category':'transaction_insights','reason':'review'}
    set_customer(database,1003)
    assert database.execute('select count(*) from consent.asks').fetchone()==(0,)  # RLS: only her own asks
    assert sqlstate(database,'select public.customer_read_consent_asks(1002)',())=='42501'
    database.execute('reset role')
    gap=insights(database)
    assert gap['shared'] is False and gap['data'] is None
    database.execute('reset role')
    stored=database.execute("select details from audit.events where action in ('consent.rm_asked','policy.rm_consent_gap') order by event_id").fetchall()
    assert stored and all(set(d)<={'category','reason','meeting_id','reference','tool','purpose'} for (d,) in stored)


def test_delete_all_my_data_is_a_request_with_a_ten_business_day_target(database: 'Connection[SqlRow]') -> None:
    target={'category':'data_deletion','summary':'Delete all my data','urgency':'normal','details':{}}
    first=act(database,'ticket.create',target)
    assert first['command']['state']=='succeeded' and first['receipt']['result']['created'] is True
    ActionOutcome.model_validate(first)
    database.execute('reset role')
    owner,due,days=database.execute("""select owner_team,(target_at at time zone 'Asia/Singapore'),(select count(*) from generate_series(
        (created_at at time zone 'Asia/Singapore')::date+1,(target_at at time zone 'Asia/Singapore')::date,interval '1 day') d where extract(isodow from d)<6)
        from support.tickets where reference=%s""",(first['receipt']['result']['reference'],)).fetchone()
    assert owner=='Data protection team' and days==10 and due.isoweekday()<6 and (due.hour,due.minute)==(18,0)
    assert act(database,'ticket.create',target)['receipt']['result']['created'] is False  # asking twice shows the open request
