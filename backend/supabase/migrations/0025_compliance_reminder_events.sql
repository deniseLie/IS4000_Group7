-- A scheduler calls this idempotent producer. Daily keys include the recorded due date; no invented deadlines.
create function events.enqueue_due_reminders()
returns int language plpgsql volatile security definer set search_path=''
as $$
declare today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date; inserted int;
begin
  insert into events.outbox(event_type,customer_id,payload,dedup_key)
    select 'compliance.deadline_reminder',c.customer_id,jsonb_build_object('case_id',c.case_id,'due_date',c.due_date),
      'compliance:'||c.case_id::text||':'||c.due_date::text||':'||today::text
    from compliance.cases c where c.due_date between today and today+7
      and c.status in ('review_opened','documents_requested','documents_partially_received','awaiting_client_documents','information_requested')
      and exists(select 1 from compliance.requested_items i where i.customer_id=c.customer_id and i.case_id=c.case_id
        and i.submission_status in ('not_submitted','rejected','unknown'))
    on conflict(customer_id,event_type,dedup_key) where dedup_key is not null do nothing;
  get diagnostics inserted=row_count;
  if inserted>0 then
    perform audit.record('notification','notification.reminders_enqueued','success','events.outbox',null,jsonb_build_object('event_count',inserted),
      p_actor_type=>'system',p_actor_id=>'notification_worker');
  end if;
  return inserted;
end $$;
revoke all on function events.enqueue_due_reminders() from public,anon,authenticated;
grant execute on function events.enqueue_due_reminders() to demobank_worker;
