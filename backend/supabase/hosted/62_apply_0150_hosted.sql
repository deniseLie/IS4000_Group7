-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0150$
declare previous supabase_migrations.schema_migrations; script text:=$source_0150$-- Released source SHA256: 5e290591d9680ead91a73713606918ee6a84e6b14d459d4077fe20e4289e61f2
-- No weekly meeting limit (11 Nov 2026): a client may have any number of meetings with her relationship manager in a
-- week. 0092 capped it at two; every meeting now starts as a meeting request the RM accepts (0106, and at most 3 open
-- requests at a time), so the RM already decides how full her calendar gets. Already run by hand on the hosted project.
--   1. rm.weekly_meeting_limit() returns null, meaning no limit. Return a number again to bring a limit back.
--      rm.slot_problem (0106) and actions.require_available_slot (0092) compare the week's count with it, and a
--      comparison with null is never true, so neither refuses; rm_availability.rules carries null, and the RM's Schedule
--      then shows no booking rule and no "above the limit" warning (SchedulePage.jsx skips both without a number).
--   2. appointments_find_slots: "count < null" is never true either, so without this every free time would disappear.
--      Same as 0092 with the weekly condition skipped when there is no limit.

create or replace function rm.weekly_meeting_limit() returns int language sql immutable set search_path='' as $$ select null::int $$;

-- Same as 0092 except the last condition: free times in any week when there is no limit.
create or replace function public.appointments_find_slots(p_customer_id bigint,p_from date,p_to date,p_limit int)
returns table(customer_id bigint,advisor_id uuid,starts_at timestamptz,ends_at timestamptz)
language plpgsql volatile security definer set search_path=''
as $$
declare own_customer bigint:=banking.my_customer_id(); checked_at timestamptz:=clock_timestamp();
begin
  if auth.uid() is null or own_customer is null or p_customer_id is distinct from own_customer then
    raise exception 'appointment scope does not match the signed-in customer' using errcode='42501';
  end if;
  if p_from is null or p_to is null or p_from<(checked_at at time zone 'Asia/Singapore')::date
      or p_to<p_from or p_to-p_from>13 or p_limit is null or p_limit not between 1 and 20 then
    raise exception 'appointment search requires today or later, a maximum 14-day range and a limit of 1-20' using errcode='22023';
  end if;
  return query
  select own_customer,a.advisor_id,s.start_time,s.start_time+interval '30 minutes'
  from rm.assignments r join rm.advisors a on a.advisor_id=r.advisor_id
  cross join generate_series(p_from::timestamp,p_to::timestamp,interval '1 day') d(day)
  join rm.working_hours h on h.advisor_id=a.advisor_id and h.weekday=extract(isodow from d.day)
  cross join lateral generate_series((d.day::date+h.starts_at) at time zone a.timezone,
    (d.day::date+h.ends_at) at time zone a.timezone-interval '30 minutes',interval '30 minutes') s(start_time)
  where r.customer_id=own_customer and r.ended_on is null and r.started_on<=(checked_at at time zone 'Asia/Singapore')::date
    and a.is_active and s.start_time>checked_at
    and not exists(select 1 from rm.advisor_blocks b where b.advisor_id=a.advisor_id
      and tstzrange(b.starts_at,b.ends_at,'[)') && tstzrange(s.start_time,s.start_time+interval '30 minutes','[)'))
    and not exists(select 1 from rm.appointments ap where ap.advisor_id=a.advisor_id
      and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>checked_at)
      and tstzrange(ap.starts_at,ap.ends_at,'[)') && tstzrange(s.start_time,s.start_time+interval '30 minutes','[)'))
    and (rm.weekly_meeting_limit() is null or rm.client_week_meetings(own_customer,a.advisor_id,s.start_time)<rm.weekly_meeting_limit())
  order by s.start_time,a.advisor_id limit p_limit;
end $$;

revoke all on function rm.weekly_meeting_limit() from public,anon,authenticated;
revoke all on function public.appointments_find_slots(bigint,date,date,int) from public,anon;
grant execute on function public.appointments_find_slots(bigint,date,date,int) to authenticated;
$source_0150$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0150';
  if found then
    if previous.name is distinct from 'no_weekly_meeting_limit' or previous.statements is distinct from array[script] then
      raise exception 'migration 0150 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0150 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0150','no_weekly_meeting_limit',array[script]);
  end if;
end $apply_0150$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
