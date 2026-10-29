-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0079$
declare previous supabase_migrations.schema_migrations; script text:=$source_0079$-- Released source SHA256: 89204ee41b85d9fcf8bf918d99655252d9caf3704b134e43b9bd401b4c7b6255
-- Follow-up to 0078 (CR7). 0078 cleared reminders only when a document or request changed afterwards, so reminders already
-- in the Inbox stayed until the next change. This:
--   1. also clears a reminder whose deadline is out of date (the review or request now has a different due date, for
--      example after 0073 moved Michelle's dates to 30 Nov); a new one with the right date follows within 7 days of it;
--   2. clears again when a review's or request's due date changes;
--   3. runs the clean-up once now for every review, so reminders she has already acted on disappear straight away.

create or replace function notify.clear_answered_reminders(p_customer_id bigint,p_case_id uuid)
returns int language plpgsql volatile security definer set search_path='' as $$
declare cleared int;
begin
  update notify.inbox i set cleared_at=clock_timestamp(),read_at=coalesce(i.read_at,clock_timestamp())
  from events.outbox o
  where i.event_id=o.event_id and i.customer_id=p_customer_id and o.customer_id=p_customer_id and i.cleared_at is null
    and o.payload->>'case_id'=p_case_id::text
    and ((o.event_type='compliance.deadline_reminder'
          and (not exists(select 1 from compliance.requested_items r where r.customer_id=p_customer_id and r.case_id=p_case_id
                 and r.submission_status in ('not_submitted','rejected','unknown'))
            or not exists(select 1 from compliance.cases c where c.customer_id=p_customer_id and c.case_id=p_case_id
                 and c.status not in ('completed','closed') and c.due_date::text is not distinct from o.payload->>'due_date')))
      or (o.event_type='compliance.information_reminder'
          and not exists(select 1 from compliance.info_requests q where q.customer_id=p_customer_id and q.case_id=p_case_id
            and q.request_id::text=o.payload->>'request_id' and q.status='open'
            and q.due_date::text is not distinct from o.payload->>'due_date')));
  get diagnostics cleared=row_count;
  if cleared>0 then
    perform audit.record('notification','notification.reminders_cleared','success','notify.inbox',null,
      jsonb_build_object('case_id',p_case_id,'cleared_count',cleared),p_actor_type=>'system',p_actor_id=>'notification_worker',p_customer_id=>p_customer_id);
  end if;
  return cleared;
end $$;
revoke all on function notify.clear_answered_reminders(bigint,uuid) from public,anon,authenticated;

create function notify.clear_reminders_after_case() returns trigger language plpgsql security definer set search_path='' as $$
begin perform notify.clear_answered_reminders(new.customer_id,new.case_id); return null; end $$;
revoke all on function notify.clear_reminders_after_case() from public,anon,authenticated;
create trigger clear_reminders_after_case after update of due_date,status on compliance.cases
  for each row when (old.due_date is distinct from new.due_date or old.status is distinct from new.status)
  execute function notify.clear_reminders_after_case();

create trigger clear_reminders_after_request_date after update of due_date on compliance.info_requests
  for each row when (old.due_date is distinct from new.due_date)
  execute function notify.clear_reminders_after_request();

-- Once now, for every review that has a reminder in someone's Inbox.
select notify.clear_answered_reminders(c.customer_id,c.case_id) from compliance.cases c
where exists(select 1 from notify.inbox i join events.outbox o on o.event_id=i.event_id
  where i.customer_id=c.customer_id and i.cleared_at is null and o.payload->>'case_id'=c.case_id::text
    and o.event_type in ('compliance.deadline_reminder','compliance.information_reminder'));
$source_0079$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0079';
  if found then
    if previous.name is distinct from 'clear_existing_reminders' or previous.statements is distinct from array[script] then
      raise exception 'migration 0079 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0079 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0079','clear_existing_reminders',array[script]);
  end if;
end $apply_0079$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
