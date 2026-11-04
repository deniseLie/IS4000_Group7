-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0105$
declare previous supabase_migrations.schema_migrations; script text:=$source_0105$-- Released source SHA256: 8c9e715033d8a70d8da1007364d3a4312ed30f2926c862aaa5f5c12bfa004bd9
-- Meeting seam (RM Copilot Architecture). The only database contract between the client, consent and RM copilot side
-- and the meeting lifecycle side (services/meetings): a private event queue for meeting threads, and the consent check
-- by data category. Shared models: app/schemas/meetings.py. Later migrations: client, consent and copilot side
-- 0106-0119; meeting lifecycle side 0120-0139.
--
-- A meeting is an rm.appointments row (meeting_id = appointment_id). meetings is a private schema like events: it is
-- not exposed through the Data API, and only the functions below touch its table.

-- 1. Event queue -------------------------------------------------------------------------------------------------------
create schema meetings;
revoke all on schema meetings from public,anon,authenticated;
do $$ begin
  if not exists(select 1 from pg_roles where rolname='demobank_meetings') then create role demobank_meetings nologin; end if;
end $$;
grant usage on schema meetings to demobank_meetings;  -- the meeting runner's private LOGIN role is a member

create table meetings.events (
  event_id        bigint generated always as identity primary key,
  event_type      text not null check (event_type in ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','brief.requested','transcript.ready')),
  meeting_id      uuid not null references rm.appointments(appointment_id) on delete cascade,
  customer_id     bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id      uuid not null references rm.advisors(advisor_id),
  payload         jsonb not null default '{}' check (jsonb_typeof(payload)='object' and octet_length(payload::text)<=4096),
  dedupe_key      text unique check (dedupe_key is null or length(dedupe_key) between 1 and 200),
  status          text not null default 'pending' check (status in ('pending','processing','done','failed')),
  attempts        smallint not null default 0,
  available_at    timestamptz not null default clock_timestamp(),  -- while processing: the end of the runner's lease
  created_at      timestamptz not null default clock_timestamp(),
  processed_at    timestamptz,
  last_error_code text check (last_error_code is null or last_error_code ~ '^[a-z][a-z0-9_]{0,39}$')
);
create index meetings_events_ready_idx on meetings.events (available_at) where status in ('pending','processing');
create index meetings_events_meeting_idx on meetings.events (meeting_id);
create index meetings_events_customer_idx on meetings.events (customer_id);
create index meetings_events_advisor_idx on meetings.events (advisor_id);
alter table meetings.events enable row level security;  -- no policies: no direct table access for any app role
revoke all on meetings.events from public,anon,authenticated,demobank_meetings;

-- Queue an event for a meeting's thread. Payload: ids, codes and dates only (audit.is_safe), never client text.
-- A repeated dedupe_key (a provider event id, a command id) returns the first event and queues nothing.
create function meetings.enqueue(p_type text,p_meeting_id uuid,p_payload jsonb default '{}',p_dedupe_key text default null)
returns bigint language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments; id bigint;
begin
  if p_dedupe_key is not null then
    select event_id into id from meetings.events where dedupe_key=p_dedupe_key;
    if found then return id; end if;
  end if;
  if p_payload is null or jsonb_typeof(p_payload)<>'object' or not audit.is_safe(p_payload) then
    raise exception 'meeting event payload must be an object of ids, codes and dates' using errcode='22023';
  end if;
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  if not found then raise exception 'meeting not found' using errcode='P0002'; end if;
  insert into meetings.events(event_type,meeting_id,customer_id,advisor_id,payload,dedupe_key)
    values(p_type,p_meeting_id,a.customer_id,a.advisor_id,p_payload,p_dedupe_key)
    on conflict (dedupe_key) do nothing returning event_id into id;
  if id is null then return (select event_id from meetings.events where dedupe_key=p_dedupe_key); end if;
  perform audit.record('action','action.meeting_event_queued','success','meetings.events',id::text,
    jsonb_build_object('event_type',p_type,'meeting_id',p_meeting_id),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>a.customer_id);
  return id;
end $$;

-- The runner claims the oldest ready event for 10 minutes. A crashed runner's event becomes claimable again when its
-- lease ends, so meeting threads must treat every event idempotently. Returns null when nothing is ready.
create function meetings.claim_next() returns meetings.events language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
  select * into e from meetings.events where status in ('pending','processing') and available_at<=clock_timestamp()
    order by available_at,event_id for update skip locked limit 1;
  if not found then return null; end if;
  update meetings.events set status='processing',attempts=attempts+1,available_at=clock_timestamp()+interval '10 minutes'
    where event_id=e.event_id returning * into e;
  return e;
end $$;

-- Done, or retried with backoff (2, 4, 8 ... at most 300 s); after 5 attempts the event fails and is audited.
create function meetings.finish(p_event_id bigint,p_ok boolean,p_error_code text default null)
returns meetings.events language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
  if p_ok is null or (not p_ok and (p_error_code is null or p_error_code !~ '^[a-z][a-z0-9_]{0,39}$')) then
    raise exception 'a failed meeting event needs an error code' using errcode='22023';
  end if;
  update meetings.events set
      status=case when p_ok then 'done' when attempts>=5 then 'failed' else 'pending' end,
      processed_at=case when p_ok or attempts>=5 then clock_timestamp() end,
      last_error_code=case when p_ok then null else p_error_code end,
      available_at=case when p_ok or attempts>=5 then available_at else clock_timestamp()+make_interval(secs=>least(300,power(2,attempts)::int)) end
    where event_id=p_event_id and status='processing' returning * into e;
  if not found then raise exception 'meeting event is not being processed' using errcode='P0002'; end if;
  if e.status='failed' then
    perform audit.record('action','action.meeting_event_failed','failed','meetings.events',e.event_id::text,
      jsonb_build_object('event_type',e.event_type,'error_code',e.last_error_code,'attempts',e.attempts),
      p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>e.customer_id);
  end if;
  return e;
end $$;

revoke all on function meetings.enqueue(text,uuid,jsonb,text) from public,anon,authenticated;
revoke all on function meetings.claim_next() from public,anon,authenticated;
revoke all on function meetings.finish(bigint,boolean,text) from public,anon,authenticated;
grant execute on function meetings.enqueue(text,uuid,jsonb,text) to demobank_meetings;  -- e.g. transcript.ready from a webhook
grant execute on function meetings.claim_next() to demobank_meetings;
grant execute on function meetings.finish(bigint,boolean,text) to demobank_meetings;

-- 2. Consent by data category ------------------------------------------------------------------------------------------
-- True only while the client is in this RM's book and has an active rm_brief grant to this RM (not revoked, not
-- expired) that covers the category. Categories match app/schemas/meetings.ConsentCategory; an unknown one is an
-- error, never a silent false. Private: RM reads call it inside SQL, next to rm.in_book, before returning gated data.
-- The client's consent request (0037) accepts these categories only once consent v2 (0106-0119) adds them.
create function rm.consent_active(p_customer bigint,p_category text,p_advisor uuid)
returns boolean language plpgsql stable security definer set search_path='' as $$
begin
  if p_category is null or p_category not in ('transaction_insights','chat_topics','meeting_recording') then
    raise exception 'unknown consent category' using errcode='22023';
  end if;
  return rm.in_book(p_advisor,p_customer)
    and exists(select 1 from rm.active_consent(p_advisor,p_customer) g where p_category=any(g.categories));
end $$;
revoke all on function rm.consent_active(bigint,text,uuid) from public,anon,authenticated;

-- What the signed-in RM may see for one client in her book, per category (audited like every RM read).
create function public.rm_consent_status(p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.consent_status'); end if;
  return rm.result(p_customer_id,'rm.consent_status','consent.grants',(select jsonb_object_agg(c,rm.consent_active(p_customer_id,c,advisor))
    from unnest(array['transaction_insights','chat_topics','meeting_recording']) c));
end $$;
revoke all on function public.rm_consent_status(bigint) from public,anon;
grant execute on function public.rm_consent_status(bigint) to authenticated;
$source_0105$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0105';
  if found then
    if previous.name is distinct from 'meeting_seam' or previous.statements is distinct from array[script] then
      raise exception 'migration 0105 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0105 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0105','meeting_seam',array[script]);
  end if;
end $apply_0105$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
