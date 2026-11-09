"""rm_zoom_check (migration 0150) over real temporary PostgreSQL: the codes-only record read behind the RM's Zoom check.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_zoom_check_db.py -q
"""
import json
import os
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from tests.database_support import SqlRow
from tests.test_meeting_seam import SOPHIE,as_rm,meeting

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
JOIN='https://us06web.zoom.us/j/85012345678?pwd=synthetic-join-secret'


def check(db: 'Connection[SqlRow]',appointment: str) -> dict:
    as_rm(db)
    result=db.execute('select public.rm_zoom_check(%s)',(appointment,)).fetchone()[0]
    db.execute('reset role')
    return result


def link(db: 'Connection[SqlRow]',appointment: str,shift: str='0 minutes',cancelled: bool=False) -> None:
    db.execute('reset role')
    db.execute("""insert into meetings.zoom_links(meeting_id,provider_id,join_url,starts_at,cancelled_at)
        select appointment_id,85012345678,%s,starts_at+%s::interval,case when %s then now() end from rm.appointments where appointment_id=%s""",
        (JOIN,shift,cancelled,appointment))


def test_her_meeting_says_whether_its_zoom_link_is_current_in_codes_only(database: 'Connection[SqlRow]') -> None:
    appointment=meeting(database)
    none=check(database,appointment)
    assert none['denied'] is False and none['customer_id']==1002 and none['data']['link']=='none' and none['data']['provider_id'] is None
    assert set(none['data'])=={'meeting','starts_at','ends_at','format','status','link','provider_id'}
    link(database,appointment)
    current=check(database,appointment)
    assert (current['data']['link'],current['data']['provider_id'])==('set_up',85012345678) and 'zoom.us' not in json.dumps(current)
    database.execute("update meetings.zoom_links set starts_at=starts_at+interval '1 hour' where meeting_id=%s",(appointment,))
    assert check(database,appointment)['data']['link']=='stale'  # the booking moved after the link was made
    database.execute('update meetings.zoom_links set cancelled_at=now() where meeting_id=%s',(appointment,))
    assert check(database,appointment)['data']['link']=='none'
    audited=database.execute("select count(*) from audit.events where action='data.read' and details->>'tool'='rm.zoom_check'").fetchone()[0]
    assert audited==4


def test_a_meeting_outside_her_book_or_not_hers_is_refused_and_audited_the_same_way(database: 'Connection[SqlRow]') -> None:
    other=database.execute('select appointment_id from rm.appointments where advisor_id<>%s limit 1',(SOPHIE,)).fetchone()
    for appointment in [*([str(other[0])] if other else []),str(uuid4())]:
        refused=check(database,appointment)
        assert refused['denied'] is True and refused['data'] is None
    denials=database.execute("select count(*) from audit.events where action='policy.rm_client_denied' and details->>'tool'='rm.zoom_check'").fetchone()[0]
    assert denials==(2 if other else 1)


def test_only_a_signed_in_rm_may_call_it(database: 'Connection[SqlRow]') -> None:
    appointment=meeting(database)
    database.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(uuid4()),'role':'authenticated','app_metadata':{}}),))
    database.execute('set local role authenticated')
    with pytest.raises(Exception,match='relationship manager tools require'):database.execute('select public.rm_zoom_check(%s)',(appointment,))
