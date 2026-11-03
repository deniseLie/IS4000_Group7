-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0095$
declare previous supabase_migrations.schema_migrations; script text:=$source_0095$-- Released source SHA256: 4fda685c456a141bcdd6d9170f90729ff7853137d0075116057f20dc1faaa5d0
-- Persona 2: RM insights (Today page, conversation starters, meeting → notes → recap, questions across the book). Every function resolves the advisor from the RM claim and staff login (rm.current_advisor, 0089), reads only
-- clients in her current book (rm.in_book / current assignments) and writes one data.read audit event (rm.result).
-- Synthetic data only.

-- 1. A documented interaction can belong to an appointment ("Document this meeting"). The link is taken from the approved
--    documentation (structured.appointment_id) and kept only when the appointment is the same client's, with the same RM.
alter table rm.meeting_notes add column appointment_id uuid references rm.appointments(appointment_id) on delete set null;
create index meeting_notes_appointment_idx on rm.meeting_notes(appointment_id) where appointment_id is not null;

create function rm.link_note_appointment() returns trigger language plpgsql security definer set search_path='' as $$
declare wanted text:=new.structured->>'appointment_id';
begin
  if wanted is not null and wanted ~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    select ap.appointment_id into new.appointment_id from rm.appointments ap
      where ap.appointment_id=wanted::uuid and ap.customer_id=new.customer_id and ap.advisor_id=new.advisor_id and ap.status='confirmed';
  end if;
  return new;
end $$;
create trigger link_note_appointment before insert or update of structured on rm.meeting_notes for each row execute function rm.link_note_appointment();

-- Same as 0089 plus the linked appointment.
create or replace function rm.note_json(n rm.meeting_notes,p_advisor uuid) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('note_id',n.note_id,'met_on',n.met_on,'met_on_text',rm.day(n.met_on),'format',n.format,
    'author',rm.display_name(a.full_name),'written_by_current_rm',n.advisor_id=p_advisor,'title',n.title,'summary',n.summary,
    'commitments',to_jsonb(n.commitments),'decisions',to_jsonb(n.decisions),'client_requests',to_jsonb(n.client_requests),
    'source',n.source,'approved_at',n.approved_at,'has_source_notes',n.source_notes is not null,'appointment_id',n.appointment_id)
  from rm.advisors a where a.advisor_id=n.advisor_id
$$;

-- Same as 0089, plus whether the meeting has been documented, and past meetings up to 30 days back (to document them).
create or replace function public.rm_appointments(p_from date default null,p_to date default null) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); f date:=coalesce(p_from,rm.today()); t date:=coalesce(p_to,coalesce(p_from,rm.today())+13);
begin
  if t<f or t-f>62 then raise exception 'choose a range of up to 62 days' using errcode='22023'; end if;
  if f<rm.today()-30 then raise exception 'past meetings can be listed up to 30 days back' using errcode='22023'; end if;
  return rm.result(null,'rm.appointments','rm.appointments',(select coalesce(jsonb_agg(jsonb_build_object('appointment_id',ap.appointment_id,
      'customer_id',ap.customer_id,'client',rm.display_name(p.full_name),'starts_at',ap.starts_at,'ends_at',ap.ends_at,'when',rm.sgt(ap.starts_at),
      'date',(ap.starts_at at time zone 'Asia/Singapore')::date,'format',ap.format,'status',ap.status,'purpose',ap.purpose,
      'hold_expires',case when ap.status='held' then rm.sgt(ap.hold_expires_at) end,'past',ap.ends_at<=clock_timestamp(),
      'note_id',(select n.note_id from rm.meeting_notes n where n.appointment_id=ap.appointment_id order by n.created_at desc limit 1)) order by ap.starts_at),'[]')
    from rm.appointments ap join banking.customer_profiles p using(customer_id)
    where ap.advisor_id=advisor and rm.in_book(advisor,ap.customer_id)
      and (ap.starts_at at time zone 'Asia/Singapore')::date between f and t
      and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())));
end $$;

-- 2. Large money movements for one client (conversation starters, RM4). Amounts of 10,000 or more in either direction in
--    the last 180 days, as recorded; recurring = the same counterparty 3 or more times in that period (for example a
--    monthly income). No interpretation: the assistant may only ask about them.
create function public.rm_cash_events(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.cash_events'); end if;
  return rm.result(p_customer_id,'rm.cash_events','banking.transactions',(select coalesce(jsonb_agg(jsonb_build_object(
      'date',(t.txn_date at time zone 'Asia/Singapore')::date,'date_text',rm.day((t.txn_date at time zone 'Asia/Singapore')::date),
      'direction',case when t.amount>0 then 'incoming' else 'outgoing' end,'amount',abs(t.amount),'currency',t.currency,
      'description',t.description,'counterparty',t.merchant,'overseas',t.is_overseas,'status',t.status,
      'recurring',(select count(*)>=3 from banking.transactions r where r.customer_id=t.customer_id and r.merchant=t.merchant
         and r.txn_date>=clock_timestamp()-interval '180 days')) order by t.txn_date desc),'[]')
    from banking.transactions t where t.customer_id=p_customer_id and abs(t.amount)>=10000
      and t.txn_date>=clock_timestamp()-interval '180 days' and t.status in ('posted','pending')));
end $$;

-- 3. One compact line of facts per client in the book, for questions across the book ("who has a maturity this month",
--    "who haven't I met in 90 days"). Counts and dates only; full records stay behind the per-client functions.
create function public.rm_book_facts() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); today date:=rm.today();
begin
  return rm.result(null,'rm.book_facts','rm.assignments',(select coalesce(jsonb_agg(row order by row->>'client'),'[]') from (
    select jsonb_build_object('customer_id',p.customer_id,'client',rm.display_name(p.full_name),'income_range',p.income_range,
      'client_since',rm.day(a.started_on),
      'inherited_from',(select rm.display_name(v.full_name) from rm.assignments x join rm.advisors v using(advisor_id)
         where x.customer_id=p.customer_id and x.ended_on is not null and x.advisor_id<>advisor order by x.ended_on desc limit 1),
      'last_interaction',(select rm.day(max(n.met_on)) from rm.visible_notes(advisor,p.customer_id) n),
      'days_since_last_interaction',(select today-max(n.met_on) from rm.visible_notes(advisor,p.customer_id) n),
      'next_confirmed_meeting',(select rm.sgt(min(ap.starts_at)) from rm.appointments ap where ap.customer_id=p.customer_id and ap.advisor_id=advisor
         and ap.status='confirmed' and ap.starts_at>clock_timestamp()),
      'open_requests_for_you',(select count(*) from support.tickets t where t.customer_id=p.customer_id and rm.rm_owned(t.owner_team)
         and t.status in ('submitted','in_review','waiting_on_client')),
      'requests_past_target',(select count(*) from support.tickets t where t.customer_id=p.customer_id and t.status in ('submitted','in_review') and t.target_at<clock_timestamp()),
      'open_specialist_cases',(select count(*) from support.tickets t where t.customer_id=p.customer_id and not rm.rm_owned(t.owner_team)
         and t.status in ('submitted','in_review','waiting_on_client')),
      'review_due',(select rm.day(min(c.due_date)) from compliance.cases c where c.customer_id=p.customer_id and c.status not in ('completed','closed')),
      'maturities_and_reviews_next_90_days',(select coalesce(jsonb_agg(jsonb_build_object('title',m.title,'due',rm.day(m.due_on),'amount',m.amount,'currency',m.currency)
         order by m.due_on),'[]') from rm.client_milestones m where m.customer_id=p.customer_id and m.due_on between today and today+90),
      'open_tasks',(select count(*) from rm.tasks k where k.customer_id=p.customer_id and k.advisor_id=advisor and k.status='open'),
      'overdue_tasks',(select count(*) from rm.tasks k where k.customer_id=p.customer_id and k.advisor_id=advisor and k.status='open' and k.due_on<today),
      'large_movements_180_days',(select count(*) from banking.transactions t where t.customer_id=p.customer_id and abs(t.amount)>=10000
         and t.txn_date>=clock_timestamp()-interval '180 days'),
      'recorded_preferences',(select count(*) from rm.client_preferences f where f.customer_id=p.customer_id)) row
    from rm.assignments a join banking.customer_profiles p using(customer_id)
    where a.advisor_id=advisor and a.ended_on is null and a.started_on<=today
    limit 50) x));
end $$;

-- 4. Demo: one meeting with Michelle on the previous week's Friday that has not been documented yet (Today → "Meetings to
--    document"). In the previous week so it never counts towards the current week's booking limit (0092).
do $$
declare sophie constant uuid:='00000000-0000-4000-8000-100200000002'; today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date; at timestamptz;
begin
  if not exists(select 1 from rm.assignments where customer_id=1002 and advisor_id=sophie and ended_on is null) then return; end if;
  at:=((today-(extract(isodow from today)::int+2))+time '15:00') at time zone 'Asia/Singapore';
  insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose)
  select 1002,sophie,at,at+interval '30 minutes','phone','confirmed',(select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='phone'),
    'Check-in on the sale agreement and fixed deposit maturity'
  where not exists(select 1 from rm.appointments ap where ap.customer_id=1002 and ap.advisor_id=sophie and ap.starts_at=at);
end $$;

revoke all on function rm.link_note_appointment() from public,anon,authenticated;
do $$
declare f text;
begin
  foreach f in array array['rm_appointments(date,date)','rm_cash_events(bigint)','rm_book_facts()'] loop
    execute format('revoke all on function public.%s from public,anon',f);
    execute format('grant execute on function public.%s to authenticated',f);
  end loop;
end $$;
revoke all on function rm.note_json(rm.meeting_notes,uuid) from public,anon,authenticated;
$source_0095$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0095';
  if found then
    if previous.name is distinct from 'rm_insights' or previous.statements is distinct from array[script] then
      raise exception 'migration 0095 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0095 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0095','rm_insights',array[script]);
  end if;
end $apply_0095$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
