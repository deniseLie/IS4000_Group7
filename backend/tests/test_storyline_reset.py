"""58_reset_storyline_demo.sql over real temporary PostgreSQL: after a rehearsal, Michelle (1002) and Sophie are back
to the seed, row count for row count, Michelle has no meeting request or booked meeting (she asks in the demo), and the
script runs again without error.

Run from backend/: TEST_DATABASE_LOCAL=1 ../.venv/bin/python -m pytest tests/test_storyline_reset.py -q
The meeting-workspace case also needs TEST_DATA_API_LOCAL=1 (it drives a meeting to approved minutes through the API).
"""
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4
import pytest
from tests.database_support import SOPHIE_LOGIN, SqlRow, set_customer
from tests.test_meeting_lifecycle import flow  # noqa: F401  (fixture: one meeting driven through the real API)

if TYPE_CHECKING:
    from psycopg import Connection

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATABASE_LOCAL')!='1',reason='Opt in to native temporary PostgreSQL with TEST_DATABASE_LOCAL=1')
SCRIPT=Path(__file__).parents[1]/'supabase'/'hosted'/'58_reset_storyline_demo.sql'
SOPHIE='00000000-0000-4000-8000-100200000002'
SCHEMAS=('banking','compliance','consent','conversation','actions','support','notify','events','rm','insights','meetings')
GROWS={'compliance.status_history'}  # each review reset adds a history line, as 51 does


def body() -> str:
    """The script without its own begin/commit, so it runs inside the test's transaction (the fixture rolls back)."""
    return re.sub(r'^(begin|commit);$','',SCRIPT.read_text(),flags=re.M)


def snapshot(db: 'Connection[SqlRow]') -> dict[str,int]:
    """Rows per table that belong to Michelle, to Sophie, or to one of Michelle's meetings."""
    tables=db.execute("""select c.table_schema||'.'||c.table_name,c.column_name from information_schema.columns c
        join information_schema.tables t on t.table_schema=c.table_schema and t.table_name=c.table_name and t.table_type='BASE TABLE'
        where c.table_schema=any(%s) and c.column_name in ('customer_id','advisor_id','meeting_id') order by 1,2""",(list(SCHEMAS),)).fetchall()
    where={'customer_id':'customer_id=1002','advisor_id':f"advisor_id='{SOPHIE}'",
        'meeting_id':'meeting_id in (select appointment_id from rm.appointments where customer_id=1002)'}
    return {f'{table}.{column}':db.execute(f'select count(*) from {table} where {where[column]}').fetchone()[0]
        for table,column in tables if table not in GROWS}


def reset(db: 'Connection[SqlRow]') -> None:
    db.execute('reset role')
    db.execute(body())


def not_from_requests(db: 'Connection[SqlRow]') -> list:
    """Michelle's appointments that did not come from a meeting request (the seeded check-ins), before any reset."""
    return db.execute("""select appointment_id from rm.appointments where customer_id=1002 and appointment_id not in
        (select appointment_id from rm.meeting_requests where appointment_id is not null) order by 1""").fetchall()


def no_booked_meeting(db: 'Connection[SqlRow]',kept: list) -> None:
    """She asks for her meeting in the demo, so the reset leaves her no request and no meeting from one. Her seeded
    check-ins stay: 0090's is dated from the day the migration ran (past on hosted, next week in a fresh test database)."""
    assert db.execute('select count(*) from rm.meeting_requests where customer_id=1002').fetchone()[0]==0
    assert db.execute('select appointment_id from rm.appointments where customer_id=1002 order by 1').fetchall()==kept
    assert db.execute("select count(*) from notify.inbox where customer_id=1002 and template_key like 'meeting.%'").fetchone()[0]==0
    assert db.execute('select status from rm.meeting_requests where customer_id=1007').fetchall()==[('requested',)]


def as_sophie(db: 'Connection[SqlRow]') -> None:
    db.execute("select set_config('request.jwt.claims',%s,true)",(json.dumps({'sub':str(SOPHIE_LOGIN),'role':'authenticated','aal':'aal1',
        'session_id':str(uuid4()),'app_metadata':{'demobank_role':'rm','rm_id':SOPHIE}}),))
    db.execute('set local role authenticated')


def test_a_rehearsal_is_undone_and_seeded_rows_stay(database: 'Connection[SqlRow]') -> None:
    kept=not_from_requests(database)
    reset(database)  # the demo's start state (51 keeps two review items; no meeting request or booked meeting for Michelle)
    no_booked_meeting(database,kept)
    seed=snapshot(database)
    seeded_tickets=database.execute("select ticket_id from support.tickets where customer_id=1002 and source='seed' order by 1").fetchall()
    seeded_notes=database.execute("select note_id from rm.meeting_notes where customer_id=1002 order by 1").fetchall()
    seeded_tasks=database.execute("select task_id from rm.tasks where advisor_id=%s order by 1",(SOPHIE,)).fetchall()
    # Michelle: a saved chat case, a card-limit request she confirms, and a handoff (actions ledger, tickets, notices).
    set_customer(database,1002)
    case_id=uuid4()
    database.execute('select public.action_create_case(%s,%s)',(uuid4(),case_id))
    limit={'category':'card_limit_change','summary':'Higher limit for a trip to Japan.','urgency':'normal',
        'details':{'card':'7310','new_limit':'30000','duration':'temporary','until':(date.today()+timedelta(days=30)).isoformat(),'reason':'Travel','declaration':'yes'}}
    proposed=database.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'ticket.create',json.dumps(limit),f'{case_id}:t:ticket.create')).fetchone()[0]
    database.execute('select public.action_confirm(%s)',(proposed['command']['command_id'],))
    database.execute('select public.action_propose(%s,%s,%s,%s::jsonb,%s)',(uuid4(),case_id,'handoff.create',
        json.dumps({'reason':'card_verification','priority':'P0','summary':'Card ending 7310: verify the blocked card.'}),f'{case_id}:h:handoff.create'))
    database.execute('reset role')
    # Sophie: a blocked time, a dismissed follow-up and an assistant chat through her own functions.
    as_sophie(database)
    database.execute("select public.rm_add_block(date_trunc('hour',now())+interval '2 days',date_trunc('hour',now())+interval '2 days 1 hour','Demo')")
    database.execute("select public.rm_dismiss_followup('task:demo')")
    database.execute("select public.rm_save_conversation(%s,'Michelle prep','[]'::jsonb,'{}'::jsonb)",(uuid4(),))
    database.execute('reset role')
    # Records the app writes elsewhere in a rehearsal, entered directly.
    note=database.execute(f"""insert into rm.meeting_notes(customer_id,advisor_id,met_on,format,summary,source) values
        (1002,'{SOPHIE}',current_date,'video','Discussed a Lombard loan.','rm_documented') returning note_id""").fetchone()[0]
    database.execute(f"""insert into rm.meeting_notes(customer_id,advisor_id,met_on,format,summary,source) values
        (1007,'{SOPHIE}',current_date,'phone','Call about her deposit.','rm_documented')""")
    database.execute(f"insert into rm.tasks(advisor_id,customer_id,note_id,title,request_key) values('{SOPHIE}',1002,%s,'Send time deposit options',%s)",(note,uuid4()))
    database.execute(f"insert into rm.client_messages(message_id,customer_id,advisor_id,body,status) values(%s,1002,'{SOPHIE}','Your recap is ready.','sent')",(uuid4(),))
    database.execute(f"insert into consent.grants(customer_id,purpose,recipient,categories) values(1002,'rm_brief','{SOPHIE}',array['transaction_insights'])")
    database.execute("insert into insights.point_tombstones(point_id,customer_id,deleted_at,reason) values(%s,1002,now(),'client')",(uuid4(),))
    review=database.execute('select case_id from compliance.cases where customer_id=1002 order by created_at desc limit 1').fetchone()[0]
    document=database.execute("""insert into compliance.documents(case_id,item_id,customer_id,storage_path,file_name,mime_type,size_bytes)
        values(%s,'00000000-0000-4000-8000-100200000012',1002,'demo/sale.pdf','sale.pdf','application/pdf',100) returning document_id""",(review,)).fetchone()[0]
    database.execute("update compliance.requested_items set current_document_id=%s,submission_status='submitted_awaiting_review' where item_id='00000000-0000-4000-8000-100200000012'",(document,))
    database.execute(f"update rm.working_hours set ends_at='12:00' where advisor_id='{SOPHIE}' and weekday=3")
    database.execute(f"delete from rm.working_hours where advisor_id='{SOPHIE}' and weekday=5")
    database.execute(f"insert into rm.working_hours(advisor_id,weekday,starts_at,ends_at) values('{SOPHIE}',6,'10:00','12:00')")
    database.execute("select rm.demo_lombard_scenario(720000,35)")
    assert snapshot(database)!=seed

    reset(database)
    after=snapshot(database)
    assert {k:(seed[k],after[k]) for k in seed if seed[k]!=after[k]}=={}
    assert database.execute("select ticket_id from support.tickets where customer_id=1002 order by 1").fetchall()==seeded_tickets
    assert database.execute("select note_id from rm.meeting_notes where customer_id=1002 order by 1").fetchall()==seeded_notes
    assert database.execute("select task_id from rm.tasks where advisor_id=%s order by 1",(SOPHIE,)).fetchall()==seeded_tasks
    assert database.execute(f"select weekday,starts_at::text,ends_at::text from rm.working_hours where advisor_id='{SOPHIE}' order by 1").fetchall()==[
        (d,'09:00:00','17:00:00') for d in range(1,6)]
    assert database.execute("select label,submission_status from compliance.requested_items where case_id=%s order by sort_order",(review,)).fetchall()==[
        ('Sale agreement for your Bangkok property','not_submitted'),('Proof of address','submitted_awaiting_review')]
    assert database.execute("select count(*) from rm.client_milestones where customer_id=1002 and kind='collateral_alert'").fetchone()[0]==0
    assert database.execute("select array_agg(c order by c) from consent.grants,unnest(categories) c where customer_id=1002 and revoked_at is null").fetchone()[0]==[
        'chat_topics','meeting_recording','transaction_insights']
    no_booked_meeting(database,kept)
    reset(database)  # a second run finds nothing left and re-seeds the same
    assert {k:(seed[k],v) for k,v in snapshot(database).items() if seed[k]!=v}=={}
    no_booked_meeting(database,kept)


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='The meeting workspace needs TEST_DATA_API_LOCAL=1')
def test_an_opened_meeting_with_approved_minutes_no_longer_blocks_the_reset(flow) -> None:
    """Before this script, 34_reset_meeting_demo.sql failed once a meeting page was opened: meeting records point at the
    appointment without cascading. Drive one meeting to approved minutes, shared recap and done follow-ups, then reset."""
    import psycopg
    from tests.test_meeting_lifecycle import approve_body,claim_transcript,export_body,post,start,view
    from app.schemas.meeting_lifecycle import MinutesContent
    with psycopg.connect(flow.uri,autocommit=True) as db:
        db.execute(SCRIPT.read_text())  # the demo's start state
        seed=snapshot(db)
    start(flow,'rm');post(flow,'rm','/transcript-import',export_body())
    claim=claim_transcript(flow);context=flow.worker.context(claim)
    content=MinutesContent.model_validate({'title':'Review and next steps',
        'summary':[{'text':'The document is awaiting bank review.','quote':'The document is awaiting bank review.','cue_id':'cue1'}],'decisions':[],
        'actions':[{'action_id':str(uuid4()),'text':text,'cue_id':cue,'quote':quote,'owner':owner,'commitment':'agreed','condition':None,'due_on':None,'due_quote':None}
            for text,cue,quote,owner in [('Send updated details','cue2','I will send the updated details.','client'),
                ('Check review status','cue3','I will check the review status.','rm')]],
        'client_recap':'The document is awaiting bank review. Please send the updated details; your RM will check the status.'})
    flow.worker.save(claim,context,content);flow.worker.finish(claim)
    draft=view(flow,'rm')['draft']
    post(flow,'rm','/approve-minutes',approve_body(draft))
    post(flow,'rm','/followups',{**approve_body(draft),'action_ids':[str(a.action_id) for a in content.actions]})
    post(flow,'rm','/share-recap',approve_body(draft))
    for action in content.actions:post(flow,action.owner,f'/followups/{action.action_id}/complete',{'evidence':'Done','command_id':str(uuid4())})
    with psycopg.connect(flow.uri,autocommit=True) as db:
        assert db.execute('select count(*) from meetings.workflows where meeting_id=%s',(flow.meeting,)).fetchone()[0]==1
        db.execute(SCRIPT.read_text())
        db.execute(SCRIPT.read_text())  # and again
        after=snapshot(db)
        assert db.execute('select count(*) from rm.appointments where appointment_id=%s',(flow.meeting,)).fetchone()[0]==1  # not from a request: kept
    assert {k:(seed[k],v) for k,v in after.items() if seed[k]!=v}=={}
