-- Only free times for the caller's current advisor. Never disclose another customer's booking.
-- A definer is needed to exclude appointments hidden by customer RLS; scope is checked before any read.
-- These are suggestions, not holds. The Action Service must recheck availability while booking atomically.
create function public.appointments_find_slots(p_customer_id bigint,p_from date,p_to date,p_limit int)
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
  order by s.start_time,a.advisor_id limit p_limit;
end $$;
revoke all on function public.appointments_find_slots(bigint,date,date,int) from public,anon;
grant execute on function public.appointments_find_slots(bigint,date,date,int) to authenticated;
