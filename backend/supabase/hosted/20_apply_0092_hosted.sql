-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0092$
declare previous supabase_migrations.schema_migrations; script text:=$source_0092$-- Released source SHA256: b8aca2ed547954c490c15e1ad28f2d4fc5027b80d65e828e4048bf6c98101068
-- RM12 / CR17: a client can book at most two meetings with their relationship manager in one week (Monday to Sunday,
-- in the advisor's time zone), so one client cannot fill the RM's calendar. Confirmed meetings and live holds count;
-- cancelled and expired ones do not. The limit is enforced where bookings are made (actions.require_available_slot, used
-- by hold and confirm), free times in a full week are no longer offered to that client (appointments_find_slots), and
-- the RM sees the rule with her availability (rm_availability.rules). Meetings booked before this migration stay.
-- Also: status notes name a team in full ("The fraud operations team is reviewing it", not "The fraud operations is…").

create function rm.weekly_meeting_limit() returns int language sql immutable set search_path='' as $$ select 2 $$;

-- Meetings the client has with this advisor in the week of p_at, not counting p_exclude (the hold being confirmed).
create function rm.client_week_meetings(p_customer bigint,p_advisor uuid,p_at timestamptz,p_exclude uuid default null)
returns int language sql stable security definer set search_path='' as $$
  select count(*)::int from rm.appointments ap join rm.advisors a on a.advisor_id=ap.advisor_id
  where ap.customer_id=p_customer and ap.advisor_id=p_advisor and ap.appointment_id is distinct from p_exclude
    and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())
    and date_trunc('week',ap.starts_at at time zone a.timezone)=date_trunc('week',p_at at time zone a.timezone) $$;

-- Same as 0034 plus the weekly limit (last check).
create or replace function actions.require_available_slot(p_target jsonb,p_customer_id bigint)
returns void language plpgsql volatile security definer set search_path=''
as $$
declare advisor rm.advisors; hours rm.working_hours; start_time timestamptz:=(p_target->>'starts_at')::timestamptz;
  end_time timestamptz:=(p_target->>'ends_at')::timestamptz; local_start timestamp; local_end timestamp;
begin
  -- Every hold/confirm/cancel for this advisor takes the same lock, before locking an appointment.
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(p_target->>'advisor_id'));
  select a.* into advisor from rm.advisors a join rm.assignments r on r.advisor_id=a.advisor_id
    where a.advisor_id=(p_target->>'advisor_id')::uuid and r.customer_id=p_customer_id and r.ended_on is null
      and r.started_on<=(clock_timestamp() at time zone 'Asia/Singapore')::date and a.is_active for share of a,r;
  if not found then raise exception 'the active relationship manager assignment changed' using errcode='P1001'; end if;
  if start_time<=clock_timestamp() or start_time>clock_timestamp()+interval '366 days' then
    raise exception 'appointment must start in the future within the next 366 days' using errcode='P1001';
  end if;
  local_start:=start_time at time zone advisor.timezone; local_end:=end_time at time zone advisor.timezone;
  select * into hours from rm.working_hours where advisor_id=advisor.advisor_id and weekday=extract(isodow from local_start) for share;
  if not found or local_start::date<>local_end::date or local_start::time<hours.starts_at or local_end::time>hours.ends_at
      or mod(extract(epoch from(local_start::time-hours.starts_at))::numeric,1800)<>0 then
    raise exception 'appointment does not match the advisor working-hours grid' using errcode='P1001';
  end if;
  if exists(select 1 from rm.advisor_blocks b where b.advisor_id=advisor.advisor_id
      and tstzrange(b.starts_at,b.ends_at,'[)') && tstzrange(start_time,end_time,'[)')) then
    raise exception 'advisor is unavailable at this time' using errcode='P1001';
  end if;
  if rm.client_week_meetings(p_customer_id,advisor.advisor_id,start_time,nullif(p_target->>'appointment_id','')::uuid)>=rm.weekly_meeting_limit() then
    raise exception 'you already have % meetings with your relationship manager that week; please choose another week',rm.weekly_meeting_limit()
      using errcode='P1001';
  end if;
end $$;

-- Same as 0012 plus: no free times in a week where the client already has the maximum number of meetings.
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
    and rm.client_week_meetings(own_customer,a.advisor_id,s.start_time)<rm.weekly_meeting_limit()
  order by s.start_time,a.advisor_id limit p_limit;
end $$;

-- Same as 0089 plus the booking rule, so the RM workspace shows it next to her availability.
create or replace function public.rm_availability() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); today date:=rm.today();
begin
  return jsonb_build_object('today',today,'as_of',clock_timestamp(),
    'rules',jsonb_build_object('weekly_meetings_per_client',rm.weekly_meeting_limit(),'week','Monday to Sunday'),
    'weekly_hours',(select coalesce(jsonb_agg(jsonb_build_object('weekday',w,'starts',to_char(h.starts_at,'HH24:MI'),'ends',to_char(h.ends_at,'HH24:MI')) order by w),'[]')
      from generate_series(1,7) w left join rm.working_hours h on h.advisor_id=advisor and h.weekday=w),
    'days',(select jsonb_agg(jsonb_build_object('date',d::date,'weekday',extract(isodow from d)::int,
      'hours',(select jsonb_build_object('starts',to_char(h.starts_at,'HH24:MI'),'ends',to_char(h.ends_at,'HH24:MI')) from rm.working_hours h
         where h.advisor_id=advisor and h.weekday=extract(isodow from d)),
      'blocks',(select coalesce(jsonb_agg(jsonb_build_object('block_id',b.block_id,'starts_at',b.starts_at,'ends_at',b.ends_at,'reason',b.reason,
         'starts',to_char(b.starts_at at time zone 'Asia/Singapore','HH24:MI'),'ends',to_char(b.ends_at at time zone 'Asia/Singapore','HH24:MI')) order by b.starts_at),'[]')
         from rm.advisor_blocks b where b.advisor_id=advisor and (b.starts_at at time zone 'Asia/Singapore')::date=d::date),
      'bookings',(select coalesce(jsonb_agg(jsonb_build_object('appointment_id',ap.appointment_id,'client',rm.display_name(p.full_name),'status',ap.status,
         'starts',to_char(ap.starts_at at time zone 'Asia/Singapore','HH24:MI'),'ends',to_char(ap.ends_at at time zone 'Asia/Singapore','HH24:MI')) order by ap.starts_at),'[]')
         from rm.appointments ap join banking.customer_profiles p using(customer_id) where ap.advisor_id=advisor
           and (ap.starts_at at time zone 'Asia/Singapore')::date=d::date and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())),
      'free',(select coalesce(jsonb_agg(to_char(s.starts_at at time zone 'Asia/Singapore','HH24:MI') order by s.starts_at),'[]')
         from rm.free_slots(advisor,d::date,d::date,40) s)) order by d)
      from generate_series(today::timestamp,(today+13)::timestamp,interval '1 day') d));
end $$;

-- Status notes: add "team" where the owner name does not already end in team (same as 0089 otherwise).
create or replace function support.status_note(p_status text,p_team text) returns text language sql immutable set search_path='' as $$
  select case p_status
    when 'submitted' then 'Received. '||case when p_team ilike 'your %' then p_team||' will review it.'
      else 'The '||lower(p_team)||case when p_team ~* 'team$' then '' else ' team' end||' will review it.' end
    when 'in_review' then case when p_team ilike 'your %' then p_team||' is reviewing it.'
      else 'The '||lower(p_team)||case when p_team ~* 'team$' then '' else ' team' end||' is reviewing it.' end
    when 'waiting_on_client' then 'The bank needs something from you before it can continue.'
    when 'resolved' then 'Marked resolved by the bank.'
    else 'Closed.' end $$;
update support.ticket_events set public_note=regexp_replace(public_note,'^(Received\. )?The ((?:[a-z]+ )*(?:operations|support)) (will review|is reviewing) it\.$','\1The \2 team \3 it.')
  where public_note ~ '^(Received\. )?The ((?:[a-z]+ )*(?:operations|support)) (will review|is reviewing) it\.$';

revoke all on function rm.weekly_meeting_limit(),rm.client_week_meetings(bigint,uuid,timestamptz,uuid) from public,anon,authenticated;
revoke all on function actions.require_available_slot(jsonb,bigint) from public,anon,authenticated;
revoke all on function public.appointments_find_slots(bigint,date,date,int) from public,anon;
grant execute on function public.appointments_find_slots(bigint,date,date,int) to authenticated;
revoke all on function public.rm_availability() from public,anon;
grant execute on function public.rm_availability() to authenticated;
$source_0092$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0092';
  if found then
    if previous.name is distinct from 'weekly_meeting_limit' or previous.statements is distinct from array[script] then
      raise exception 'migration 0092 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0092 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0092','weekly_meeting_limit',array[script]);
  end if;
end $apply_0092$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
