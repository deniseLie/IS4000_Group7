"""Talking points that follow the conversation (lane 1 v2, migration 0143) over real temporary PostgreSQL: the state the
classifier reads (list values only, active topics per chat), applying add / update / on hold with the checks repeated in
SQL (kind for the topic, her own words, a point to update, the carry-over window of 4 turns in the same chat, confirmed
points untouched), what the RM and she see of a point on hold, and v1 and sharing with the new columns. Synthetic data only.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_talking_points_state_db.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import UUID,uuid4
import pytest
from app.schemas.meetings import KINDS,TalkingPoint
from tests.database_support import SqlRow,set_customer
from tests.test_meeting_requests import act
from tests.test_meeting_seam import as_rm
from tests.test_offer_groups import sqlstate
from tests.test_talking_points_db import propose

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
MICHELLE_POINT='00000000-0000-4000-8000-100200000091'  # her seeded home loan point: inferred, planning, within 12 months, past its hold


class Chat:
    """One saved chat of hers; each say() is a finished turn (conversation_start_turn and conversation_finish_turn, 0013)."""
    def __init__(self,db: 'Connection[SqlRow]',customer: int=1002):
        set_customer(db,customer)
        self.db,self.customer,self.session=db,customer,uuid4()
        self.revision=int(db.execute("select (public.conversation_create(%s,'personal','English',null))->>'revision'",(self.session,)).fetchone()[0])

    def say(self,text: str) -> UUID:
        set_customer(self.db,self.customer)
        turn,run=uuid4(),uuid4()
        self.db.execute("select public.conversation_start_turn(%s,%s,%s,%s,'P3',%s,null)",(self.session,turn,self.revision,text,run))
        self.db.execute('select public.conversation_finish_turn(%s,%s,%s,%s::jsonb,null)',(self.session,turn,run,json.dumps({'answer':'Here is what I found.'})))
        self.revision+=1
        return turn


def apply(db: 'Connection[SqlRow]',turn: UUID,action: str,topic: str,stage: str,quote: str,timeframe: str='unknown',kind: str|None=None,
        concerns: list[str]|None=None,carried: bool=False,customer: int=1002) -> dict:
    set_customer(db,customer)
    return db.execute('select public.customer_apply_talking_point(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (turn,action,topic,stage,timeframe,kind,concerns or [],quote,carried)).fetchone()[0]


def refused(db: 'Connection[SqlRow]',*args) -> str | None:
    set_customer(db,1002)
    return sqlstate(db,'select public.customer_apply_talking_point(%s,%s,%s,%s,%s,%s,%s,%s,%s)',args)


def state(db: 'Connection[SqlRow]',turn: UUID,customer: int=1002) -> dict:
    set_customer(db,customer)
    return db.execute('select public.customer_topic_state(%s,%s)',(customer,turn)).fetchone()[0]['data']


def owner(db: 'Connection[SqlRow]') -> 'Connection[SqlRow]':
    db.execute('reset role')
    return db


def row(db: 'Connection[SqlRow]',topic: str) -> tuple:
    return owner(db).execute('select stage,timeframe,kind,concerns,on_hold_at is not null from insights.talking_points '
        'where customer_id=1002 and topic=%s and deleted_at is null',(topic,)).fetchone()


def shown(db: 'Connection[SqlRow]') -> dict:
    as_rm(db)
    return db.execute("select public.rm_talking_points(1002,'meeting_prep')").fetchone()[0]


def test_the_lists_match_the_shared_contract(database: 'Connection[SqlRow]') -> None:
    for topic,kinds in KINDS.items():
        assert database.execute('select insights.kinds(%s)',(topic,)).fetchone()==(list(kinds),)
    assert database.execute("select insights.valid_concerns(array['lock_in','down_payment','tenure','risk']),"
        "insights.valid_concerns(array['rates','fees','eligibility','timing','risk']),insights.valid_concerns(array['taxes'])").fetchone()==(True,False,False)
    # Her newest concerns first, each once, at most 4, stored in order.
    assert database.execute("select insights.merge_concerns(array['eligibility','fees','rates','timing'],array['lock_in','rates'])").fetchone()==(
        ['eligibility','fees','lock_in','rates'],)


def test_the_state_is_hers_list_values_only_and_topics_are_active_per_chat(database: 'Connection[SqlRow]') -> None:
    chat=Chat(database)
    first=chat.say("I'd like to start investing, maybe ETFs")
    assert state(database,first)['active_topics']==[]  # nothing recorded in this chat yet
    added=apply(database,first,'add','investments','curious','start investing',kind='etf')
    assert added['recorded'] is True and added['created'] is True and added['kind']=='etf' and added['on_hold'] is False
    follow=chat.say('Which one has lower fees?')
    current=state(database,follow)
    assert current['active_topics']==['investments'] and current['chat_topics'] is True and current['rm_first_name']=='Sophie'
    assert {p['topic']:p for p in current['points']}=={
        'home_loan':{'topic':'home_loan','stage':'planning','timeframe':'within_12m','kind':None,'concerns':[],'origin':'inferred','on_hold':False},
        'investments':{'topic':'investments','stage':'curious','timeframe':'unknown','kind':'etf','concerns':[],'origin':'inferred','on_hold':False}}
    assert 'start investing' not in json.dumps(current)  # never her words, no ids or dates
    # A carry-over: no topic word in her quote, but the topic is active in this chat.
    assert apply(database,follow,'update','investments','exploring','lower fees',concerns=['fees'],carried=True)['recorded'] is True
    assert row(database,'investments')==('exploring','unknown','etf',['fees'],False)  # a null kind never overwrites
    # The same carry-over in a new chat, or after 4 of her turns with nothing recorded, is refused.
    other=Chat(database).say('And the lock-in?')
    assert state(database,other)['active_topics']==[]
    assert apply(database,other,'update','investments','exploring','the lock-in',concerns=['lock_in'],carried=True)=={
        'recorded':False,'reason':'not_active','point_id':None}
    turns=[chat.say(text) for text in ('What is my card balance?','Thanks','When is my statement out?','Where is my nearest branch?',
        'How do I update my address?')]
    assert state(database,turns[-2])['active_topics']==['investments']  # the follow-up is the 4th turn back
    assert state(database,turns[-1])['active_topics']==[]
    assert apply(database,turns[-1],'update','investments','planning','update my address',carried=True)['reason']=='not_active'
    owner(database)
    assert database.execute("select details from audit.events where action='data.talking_point_skipped' and resource_id=%s",(str(turns[-1]),)).fetchone()==(
        {'topic':'investments','reason':'not_active','action':'update'},)
    assert database.execute("select details->>'action',details->>'carried' from audit.events where action='data.talking_point_recorded' "
        "and details->>'turn_id'=%s",(str(follow),)).fetchone()==('update','true')
    # Another client's turn, or her turn read as someone else.
    set_customer(database,1001)
    assert sqlstate(database,'select public.customer_topic_state(1001,%s)',(first,))=='42501'
    set_customer(database,1002)
    assert sqlstate(database,'select public.customer_topic_state(1001,%s)',(first,))=='42501'


def test_on_hold_hides_the_point_from_the_rm_keeps_it_on_her_page_and_her_next_mention_clears_it(database: 'Connection[SqlRow]') -> None:
    chat=Chat(database)
    off=chat.say("Actually we've decided not to go ahead with the condo")
    held=apply(database,off,'on_hold','home_loan','planning','decided not to go ahead with the condo')
    assert held['recorded'] is True and held['on_hold'] is True and held['point_id']==MICHELLE_POINT
    assert row(database,'home_loan')==('planning','within_12m',None,[],True)  # nothing else changes
    rm=shown(database)
    assert rm['data']==[] and rm['pending'] is False  # hidden, and not a pending gap either
    status=database.execute('select public.rm_point_status(1002,%s)',([MICHELLE_POINT],)).fetchone()[0]
    assert [p['status'] for p in status['data']]==['not_shared']
    set_customer(database,1002)
    [mine]=database.execute('select public.customer_read_talking_points(1002)').fetchone()[0]['data']['points']
    assert mine['on_hold'] is True and mine['on_hold_at'] and mine['pending'] is False and mine['kind'] is None
    owner(database)
    assert database.execute("select details from audit.events where action='data.talking_point_on_hold' and resource_id=%s",(MICHELLE_POINT,)).fetchone()==(
        {'topic':'home_loan','turn_id':str(off),'carried':False},)
    # Her next mention of the topic clears it; the stage still only moves up, the latest kind and timeframe win.
    back=chat.say("We're back to looking at condos, an HDB resale flat in a few months")
    assert apply(database,back,'update','home_loan','exploring','an HDB resale flat in a few months',timeframe='within_3m',kind='hdb_resale')['on_hold'] is False
    assert row(database,'home_loan')==('planning','within_3m','hdb_resale',[],False)
    [point]=shown(database)['data']
    assert TalkingPoint.model_validate(point).line()=='Property financing · planning · resale property · within 3 months · inferred from chat'
    # Update or on hold needs a point she has.
    for action in ('update','on_hold'):
        assert apply(database,chat.say('No more insurance plans for us'),action,'insurance','planning','insurance')=={'recorded':False,'reason':'no_point','point_id':None}


def test_sql_repeats_the_checks_and_never_changes_a_point_she_shared(database: 'Connection[SqlRow]') -> None:
    chat=Chat(database)
    turn=chat.say('We want a home loan for a landed house and I worry about the down payment')
    base=(turn,'add','home_loan','planning','unknown')
    assert refused(database,*base,'etf',[],'a home loan',False)=='22023'  # a kind of another topic
    assert refused(database,turn,'delete','home_loan','planning','unknown',None,[],'a home loan',False)=='22023'  # the model never deletes
    assert refused(database,*base,None,[],'a mortgage',False)=='22023'  # not her words
    assert refused(database,*base,None,['taxes'],'a home loan',False)=='22023'
    assert refused(database,turn,'add','health','planning','unknown',None,[],'a home loan',False)=='22023'
    assert apply(database,turn,'update','home_loan','planning','a home loan for a landed house',kind='landed',concerns=['down_payment'])['recorded'] is True
    assert apply(database,turn,'update','home_loan','ready','a home loan')=={'recorded':False,'reason':'duplicate','point_id':MICHELLE_POINT}
    assert row(database,'home_loan')==('planning','within_12m','landed',['down_payment'],False)  # unknown never overwrites the timeframe
    # Her own share replaces the inferred kind, concerns and on hold; inference never changes it afterwards.
    apply(database,chat.say("We've put the house off for now"),'on_hold','home_loan','planning','put the house off',carried=True)
    shared=act(database,'insight.share',{'topic':'home_loan','stage':'planning','timeframe':'within_12m'})
    assert shared['receipt']['outcome']=='succeeded'
    assert row(database,'home_loan')==('planning','within_12m',None,[],False)
    assert apply(database,chat.say('Is the lock-in two years?'),'update','home_loan','ready','the lock-in',concerns=['lock_in'],carried=True)['reason']=='confirmed'
    # A mute is rechecked as in v1 (0145 retired the care pause).
    propose(database,'insight.mute',{'topic':'investments'})
    assert apply(database,chat.say('Should I still invest in ETFs?'),'add','investments','curious','invest in ETFs',kind='etf')['reason']=='muted'


def test_v1_recording_caps_concerns_and_clears_on_hold(database: 'Connection[SqlRow]') -> None:
    chat=Chat(database)
    turn=chat.say('Rates, fees, eligibility and the lock-in all worry me about my home loan')
    apply(database,turn,'update','home_loan','planning','my home loan',concerns=['eligibility','fees','lock_in','rates'])
    apply(database,chat.say("We've decided not to go ahead with the home loan"),'on_hold','home_loan','planning','not to go ahead with the home loan')
    set_customer(database,1002)
    later=chat.say('What are home loan rates now? I worry about timing')
    assert database.execute('select public.customer_record_talking_point(%s,%s,%s,%s,%s,%s)',
        (later,'home_loan','curious','unknown',['rates','timing'],'home loan rates')).fetchone()[0]['recorded'] is True
    assert row(database,'home_loan')==('planning','within_12m',None,['eligibility','fees','rates','timing'],False)
