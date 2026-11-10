-- Reset Michelle (1002) and Sophie for a fresh run of the end-to-end demo (docs/workflows/demo-end-to-end.md).
-- One paste in the SQL Editor (owner). Replaces running 34 + 41 + 51 by hand, and also clears what those leave behind.
-- Synthetic demo data only. Safe to run more than once; everything runs in one transaction, so a failure changes nothing.
--
-- Removed (rows made by app use, rehearsals or evaluation runs; seeded rows are kept):
--   * Meeting side of Michelle's meetings (workspace, transcript, minutes, follow-ups, packs, briefings, Zoom records),
--     and of Mei Ling's request meetings. Without this, rm.reset_meeting_demo fails once a meeting page was opened.
--   * Michelle's saved chats, every ticket that is not seeded (with its handoff), her Inbox notices and queued notices,
--     her action ledger (Actions page), sharing grants that are not seeded, consent requests, chat-insight tombstones,
--     her uploaded documents and their checks.
--   * Sophie: notes she documented and tasks she created (any client), drafted client messages, assistant chats,
--     dismissed follow-ups and blocked times. Her working hours go back to Monday to Friday 09:00-17:00.
-- Then re-seeded as by 34 and 51: Michelle's and Mei Ling's meeting requests, the seeded Property financing meeting,
-- Michelle's three sharing grants to Sophie, chat insights, portfolio (no Lombard loan, no collateral alert) and her
-- periodic review (sale agreement to send, proof of address with the bank).
--
-- Kept: the audit log (append-only; every deletion here is itself audited), seeded tickets, notes, tasks, preferences
-- and appointments, Michelle's standing agreement to meeting transcripts (Data sharing), card statuses, other clients'
-- tickets and chats. Files already in Storage stay there (no row points to them any more); Zoom meetings created on
-- the provider are not deleted there.
--
-- After it: as Michelle, switch Transaction insights off in Data sharing if the demo shows the sharing switch on the
-- meeting request card (the re-seeded grant shares them for 180 days).
begin;

do $$
declare
  sophie constant uuid:='00000000-0000-4000-8000-100200000002';
  seeded_grants constant uuid[]:=array['00000000-0000-4000-8000-100200000041','00000000-0000-4000-8000-100200000042',
    '00000000-0000-4000-8000-100200000043']::uuid[];
  i11 constant uuid:='00000000-0000-4000-8000-100200000011'; i12 constant uuid:='00000000-0000-4000-8000-100200000012';
  m uuid[]; c uuid; n int; counts jsonb:='{}';
begin
  if not exists(select 1 from rm.assignments where customer_id=1002 and advisor_id=sophie and ended_on is null) then
    raise exception 'Michelle (1002) is not in Sophie''s book; nothing was changed' using errcode='P0001';
  end if;

  -- 1. Meeting side. Michelle's meetings, and Mei Ling's meetings that came from a request (the reset removes those).
  --    Children first: most meeting tables point at their parent without cascading.
  select coalesce(array_agg(appointment_id),'{}') into m from rm.appointments
    where customer_id=1002 or appointment_id in (select appointment_id from rm.meeting_requests where customer_id=1007 and appointment_id is not null);
  delete from meetings.review_jobs where review_id in (select review_id from meetings.minutes_reviews where meeting_id=any(m))
    or event_id in (select event_id from meetings.events where meeting_id=any(m));
  delete from meetings.zoom_evidence where source_id in (select source_id from meetings.sources where meeting_id=any(m))
    or job_id in (select job_id from meetings.zoom_fetch_jobs where binding_id in (select binding_id from meetings.zoom_bindings where meeting_id=any(m))
      or event_id in (select event_id from meetings.events where meeting_id=any(m)));
  delete from meetings.zoom_fetch_jobs where binding_id in (select binding_id from meetings.zoom_bindings where meeting_id=any(m))
    or event_id in (select event_id from meetings.events where meeting_id=any(m));
  delete from meetings.provider_call_times where binding_id in (select binding_id from meetings.zoom_bindings where meeting_id=any(m));
  delete from meetings.minutes_reviews where meeting_id=any(m);
  delete from meetings.effects where draft_id in (select draft_id from meetings.drafts where meeting_id=any(m));
  delete from meetings.followups where meeting_id=any(m);
  delete from meetings.drafts where meeting_id=any(m);
  delete from meetings.source_chunks where source_id in (select source_id from meetings.sources where meeting_id=any(m));
  delete from meetings.import_provenance where source_id in (select source_id from meetings.sources where meeting_id=any(m));
  delete from meetings.sources where meeting_id=any(m);
  delete from meetings.recording_grants where meeting_id=any(m);
  delete from meetings.commands where meeting_id=any(m);
  delete from meetings.notice_acknowledgements where meeting_id=any(m);
  delete from meetings.call_time_corrections where meeting_id=any(m);
  delete from meetings.zoom_bindings where meeting_id=any(m);
  delete from meetings.briefs where meeting_id=any(m);
  delete from meetings.packs where meeting_id=any(m);
  delete from meetings.workflows where meeting_id=any(m);
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('meeting_workspaces',n);
  delete from meetings.zoom_links where meeting_id=any(m);
  delete from meetings.zoom_creation_intents where meeting_id=any(m);
  delete from meetings.threads where meeting_id=any(m);
  delete from meetings.timers where meeting_id=any(m);
  delete from meetings.graph_pending where meeting_id=any(m);
  delete from meetings.graph_writes where meeting_id=any(m);
  delete from meetings.graph_checkpoints where meeting_id=any(m);
  delete from meetings.graph_runs where meeting_id=any(m);
  delete from meetings.copilot_briefs where meeting_id=any(m);
  delete from meetings.events where meeting_id=any(m);  -- the reset queues a fresh meeting.scheduled for its meeting
  perform meetings.refresh_timers(x) from unnest(m) x;  -- the seeded meetings kept get their preparation reminder back

  -- 2. Michelle's saved chats (turns, messages and cases go with them).
  delete from compliance.support_context where customer_id=1002;
  delete from conversation.sessions where customer_id=1002;
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('chats',n);
  delete from conversation.cases where customer_id=1002;

  -- 3. Tickets that are not seeded (attachments and status events go with them), and handoffs.
  delete from support.handoff_notes where customer_id=1002;
  delete from support.tickets where customer_id=1002 and source<>'seed';
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('tickets',n);
  delete from support.handoffs where customer_id=1002;

  -- 4. Notices.
  delete from notify.inbox where customer_id=1002;
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('inbox_notices',n);
  delete from events.outbox where customer_id=1002 or command_id in (select command_id from actions.commands where customer_id=1002);

  -- 5. Sophie's workspace. Notes and tasks still used by another client's meeting record are kept.
  delete from rm.client_messages where customer_id=1002 or advisor_id=sophie;
  delete from rm.tasks t where (t.customer_id=1002 or t.advisor_id=sophie) and t.request_key is not null
    and not exists(select 1 from meetings.followups f where f.task_id=t.task_id);
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('rm_tasks',n);
  delete from rm.meeting_notes x where (x.customer_id=1002 or x.advisor_id=sophie) and x.source<>'seed'
    and not exists(select 1 from meetings.drafts d where d.note_id=x.note_id);
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('rm_notes',n);
  delete from rm.shared_summaries where customer_id=1002;
  delete from rm.agendas where customer_id=1002;
  delete from rm.requests where customer_id=1002;
  delete from rm.dismissed_followups where advisor_id=sophie;
  delete from rm.assistant_conversations where advisor_id=sophie;
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('assistant_chats',n);
  delete from rm.advisor_blocks where advisor_id=sophie;
  delete from rm.working_hours where advisor_id=sophie and weekday not between 1 and 5;
  insert into rm.working_hours(advisor_id,weekday,starts_at,ends_at) select sophie,d,time '09:00',time '17:00' from generate_series(1,5) d
    on conflict (advisor_id,weekday) do update set starts_at=excluded.starts_at,ends_at=excluded.ends_at;

  -- 6. Sharing. The reset puts back the three seeded grants (and revokes nothing else, since nothing else is left).
  delete from consent.requests where customer_id=1002;
  delete from consent.grants where customer_id=1002 and not grant_id=any(seeded_grants);
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('sharing_grants',n);
  delete from insights.point_tombstones where customer_id=1002;

  -- 7. Documents and the periodic review (as 51_reset_periodic_review.sql).
  update compliance.requested_items set current_document_id=null where customer_id=1002 and current_document_id is not null;
  delete from compliance.prechecks where customer_id=1002;
  delete from compliance.documents where customer_id=1002;
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('documents',n);
  select case_id into c from compliance.cases where customer_id=1002 order by created_at desc limit 1;
  if c is null then raise exception 'No periodic review found for customer 1002; nothing was changed' using errcode='P0001'; end if;
  delete from compliance.info_requests where case_id=c or item_id in (i11,i12);
  delete from compliance.requested_items where case_id=c and item_id not in (i11,i12);
  insert into compliance.requested_items(item_id,case_id,customer_id,label,instructions,check_guidance,submission_status,client_mark,client_marked_at,sort_order,updated_at)
  values
   (i12,c,1002,'Sale agreement for your Bangkok property',
    'The signed sale agreement for your Bangkok flat. We ask because the sale money was paid into your account: it shows where the money came from. Include every page, with both names, the sale price, the date and the signatures. If it is in Thai, add an English translation in the same file.',
    'Check that the agreement is signed by both sides, shows the seller and buyer names, the property address, the sale price with its currency and the sale date, and that every page is included. If it is in Thai, an English translation must be included.',
    'not_submitted','not_prepared',null,1,now()),
   (i11,c,1002,'Proof of address',
    'A utility bill or bank statement from the last 3 months that shows your name and home address.',
    'Check the name matches the client, the address is a home address and the document is dated within the last 3 months.',
    'submitted_awaiting_review','prepared',timestamptz '2026-11-05 10:00+08',2,timestamptz '2026-11-05 10:00+08')
  on conflict (item_id) do update set case_id=excluded.case_id,customer_id=excluded.customer_id,label=excluded.label,instructions=excluded.instructions,
    check_guidance=excluded.check_guidance,submission_status=excluded.submission_status,client_mark=excluded.client_mark,
    client_marked_at=excluded.client_marked_at,sort_order=excluded.sort_order,current_document_id=null,updated_at=excluded.updated_at;
  update compliance.cases set title='Periodic review 2026',
    notice_text='As part of our regular review, please send the documents listed by 30 November 2026: the signed sale agreement for your Bangkok property (to show where the sale money came from) and a recent proof of address.',
    due_date='2026-11-30',status='documents_partially_received',status_updated_at=now()
    where case_id=c;
  insert into compliance.status_history(case_id,customer_id,status,note,changed_at)
    values(c,1002,'documents_partially_received','Demo reset: sale agreement requested; proof of address with the bank',now());

  -- 8. Meetings, grants, chat insights and portfolio re-seeded (as 34_reset_meeting_demo.sql).
  counts:=counts||jsonb_build_object('meeting_demo',rm.reset_meeting_demo());

  -- 9. Michelle's action ledger (receipts go with it). Last: the rows above pointed at it.
  delete from actions.staff_commands where command_id in (select command_id from actions.commands where customer_id=1002);
  delete from actions.commands where customer_id=1002;
  get diagnostics n=row_count; counts:=counts||jsonb_build_object('actions',n);

  raise notice 'Storyline demo reset: %',counts;
end $$;

-- Checks. Expect: one upcoming "Property financing options" meeting from the reset plus the seeded ones; only seeded
-- tickets; the sale agreement not_submitted and the proof of address submitted_awaiting_review; no Lombard loan.
select 'meeting' as what,purpose as detail,to_char(starts_at at time zone 'Asia/Singapore','Dy DD Mon HH24:MI') as value
  from rm.appointments where customer_id=1002 and status='confirmed' and starts_at>now()
union all select 'ticket',reference||' '||category,status from support.tickets where customer_id=1002
union all select 'review item',label,submission_status from compliance.requested_items
  where case_id=(select case_id from compliance.cases where customer_id=1002 order by created_at desc limit 1)
union all select 'lombard loans',count(*)::text,'' from banking.loans where customer_id=1002 and loan_type='securities_backed' and status='active'
union all select 'saved chats',count(*)::text,'' from conversation.sessions where customer_id=1002
union all select 'sharing grants',string_agg(array_to_string(categories,','),'; '),'' from consent.grants where customer_id=1002 and revoked_at is null;
commit;
