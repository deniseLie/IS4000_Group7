-- Audit Log Service (system-architecture.md §6, F3): append-only, hash-chained audit.events.
-- Only audit.record() inserts rows; app roles hold no insert/update/delete/truncate, and triggers reject
-- update/delete/truncate even for the owner. Each row stores prev_hash and hash = sha256(canonical row incl. prev_hash),
-- so editing or removing a row breaks the chain; audit.verify_chain() finds the first broken row.
-- Limits: the table owner can disable the triggers and rewrite the whole chain, and dropping the newest rows
-- is only caught by comparing head_hash with an earlier recorded value.
-- details hold ids, enums and counts only: a structural check rejects free text, but it cannot prove a short token is not personal data.
-- The API writes through public.audit_record_batch() (anon or customer JWT); actor and customer come from the JWT.
-- The audit schema is not exposed to the Data API.

create schema if not exists audit;
revoke all on schema audit from public;
grant usage on schema audit to authenticated;

create table audit.events (
  event_id      bigint generated always as identity primary key,  -- chain order
  occurred_at   timestamptz not null default clock_timestamp(),
  actor_type    text not null check (actor_type in ('customer','guest','agent','system','staff')),
  actor_id      text not null check (actor_id ~ '^[a-z0-9_.:-]{1,64}$'),  -- auth uuid or agent/role name
  customer_id   bigint,  -- no foreign key: removing a customer must not touch their trail
  session_id    uuid,
  request_id    uuid,
  case_id       uuid,
  category      text not null check (category in ('auth','chat','agent','data','policy','action','row','consent','notification','handoff','document','admin')),
  action        text not null check (action ~ '^[a-z_]+\.[a-z_]+$' and split_part(action, '.', 1) = category),
  resource_type text check (resource_type ~ '^[a-z_.]{1,64}$'),
  resource_id   text check (resource_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
  outcome       text not null check (outcome in ('success','denied','failed')),
  details       jsonb not null default '{}' check (jsonb_typeof(details) = 'object'),
  prev_hash     text not null check (prev_hash ~ '^[0-9a-f]{64}$'),
  hash          text not null unique check (hash ~ '^[0-9a-f]{64}$')
);
create index audit_events_customer_idx on audit.events (customer_id, event_id desc);
create index audit_events_request_idx on audit.events (request_id);
create index audit_events_case_idx on audit.events (case_id) where case_id is not null;
create index audit_events_guest_day_idx on audit.events (occurred_at) where customer_id is null;

-- Append-only, also for the owner.
create or replace function audit.reject_change()
returns trigger
language plpgsql
set search_path = ''
as $$ begin raise exception 'audit.events is append-only'; end $$;
create trigger events_no_update_delete before update or delete on audit.events for each row execute function audit.reject_change();
create trigger events_no_truncate before truncate on audit.events for each statement execute function audit.reject_change();

alter table audit.events enable row level security;
revoke all on audit.events from public, anon, authenticated, service_role;
grant select on audit.events to authenticated;
create policy own_rows on audit.events for select to authenticated using (customer_id = (select banking.my_customer_id()));

-- details: object of at most 20 snake_case keys; values are numbers, booleans, null, short tokens
-- (enums, ids, cite ids like article@v2#anchor) or one level of arrays/objects of those. No spaces, no long digit runs (card numbers).
create or replace function audit.is_safe(v jsonb, depth int default 0)
returns boolean
language plpgsql
immutable
set search_path = ''
as $$
declare s text;
begin
  case jsonb_typeof(v)
    when 'null', 'number', 'boolean' then return true;
    when 'string' then
      s := v #>> '{}';
      return s !~ '[0-9]{12,}' and (s ~ '^[A-Za-z0-9_.:#/+-]{1,80}$' or s ~ '^[a-z0-9_-]{1,60}@v[0-9]+(#[a-z0-9_-]{1,40})?$');
    when 'array' then
      return depth < 2 and jsonb_array_length(v) <= 20
        and not exists (select 1 from jsonb_array_elements(v) e where not audit.is_safe(e, depth + 1));
    when 'object' then
      return depth < 2 and (select count(*) from jsonb_object_keys(v)) <= 20
        and not exists (select 1 from jsonb_each(v) kv where kv.key !~ '^[a-z][a-z0-9_]{0,39}$' or not audit.is_safe(kv.value, depth + 1));
  end case;
  return false;
end $$;

-- Canonical form: a jsonb array (deterministic text, nulls kept) with occurred_at fixed to UTC microseconds.
create or replace function audit.event_hash(e audit.events)
returns text
language sql
stable
set search_path = ''
as $$
  select encode(sha256(convert_to(jsonb_build_array(
    e.prev_hash, e.event_id, to_char(e.occurred_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
    e.actor_type, e.actor_id, e.customer_id, e.session_id, e.request_id, e.case_id,
    e.category, e.action, e.resource_type, e.resource_id, e.outcome, e.details)::text, 'UTF8')), 'hex')
$$;

-- The only writer. Trusted callers only (other security definer functions and triggers); not granted to app roles.
-- Actor and customer default to the caller's JWT; request_id defaults to the x-request-id header PostgREST passes through.
-- The advisory lock is held until commit, so event_id order is commit order and each row chains to the one before it.
create or replace function audit.record(
  p_category text, p_action text, p_outcome text default 'success',
  p_resource_type text default null, p_resource_id text default null, p_details jsonb default '{}',
  p_case_id uuid default null, p_session_id uuid default null, p_request_id uuid default null,
  p_actor_type text default null, p_actor_id text default null, p_customer_id bigint default null)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  e audit.events;
  hdr text := nullif(current_setting('request.headers', true), '')::jsonb ->> 'x-request-id';
  jwt_role text := nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role';
begin
  if not audit.is_safe(coalesce(p_details, '{}')) or jsonb_typeof(coalesce(p_details, '{}')) <> 'object' then
    raise exception 'audit details must hold ids, enums and counts only';
  end if;
  perform pg_advisory_xact_lock(hashtext('audit.events'));

  e.prev_hash := coalesce((select x.hash from audit.events x order by x.event_id desc limit 1), repeat('0', 64));
  e.event_id := nextval(pg_get_serial_sequence('audit.events', 'event_id'));
  e.occurred_at := clock_timestamp();
  if p_actor_type is not null then
    e.actor_type := p_actor_type; e.actor_id := p_actor_id;
  elsif auth.uid() is not null then
    e.actor_type := 'customer'; e.actor_id := auth.uid()::text;
  elsif jwt_role = 'anon' then
    e.actor_type := 'guest'; e.actor_id := 'guest';
  else
    e.actor_type := 'system'; e.actor_id := lower(session_user);
  end if;
  e.customer_id := coalesce(p_customer_id, banking.my_customer_id());
  e.session_id := p_session_id;
  e.request_id := coalesce(p_request_id, case when hdr ~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then hdr::uuid end);
  e.case_id := p_case_id;
  e.category := p_category; e.action := p_action; e.outcome := p_outcome;
  e.resource_type := p_resource_type; e.resource_id := p_resource_id;
  e.details := coalesce(p_details, '{}');
  e.hash := audit.event_hash(e);

  insert into audit.events overriding system value select e.*;
  return e.event_id;
end $$;

-- API entry: one call per request, all events or none (one transaction). Customer and actor come from the JWT;
-- the only override is actor_type 'agent' with an agent name, so the customer_id is trustworthy but agent
-- attribution is only as good as the API that reports it. Guest rows share a daily cap because the anon key is public.
create or replace function public.audit_record_batch(p_events jsonb)
returns bigint[]
language plpgsql
security definer
set search_path = ''
as $$
declare
  ev jsonb;
  ids bigint[] := '{}';
  allowed text[] := array['category','action','outcome','resource_type','resource_id','details','case_id','session_id','request_id','actor_type','actor_id'];
begin
  if jsonb_typeof(p_events) <> 'array' or jsonb_array_length(p_events) not between 1 and 50 then
    raise exception 'events must be an array of 1-50 objects';
  end if;
  if auth.uid() is null and (select count(*) from audit.events where customer_id is null and occurred_at > now() - interval '1 day') >= 20000 then
    raise exception 'daily guest audit cap reached';
  end if;
  for ev in select * from jsonb_array_elements(p_events) loop
    if jsonb_typeof(ev) <> 'object' or exists (select 1 from jsonb_object_keys(ev) k where k <> all (allowed)) then
      raise exception 'unknown audit event key';
    end if;
    if ev ? 'actor_type' and (ev ->> 'actor_type' <> 'agent' or ev ->> 'actor_id' is null) then
      raise exception 'only agent actors may be named';
    end if;
    ids := ids || audit.record(
      ev ->> 'category', ev ->> 'action', coalesce(ev ->> 'outcome', 'success'),
      ev ->> 'resource_type', ev ->> 'resource_id', coalesce(ev -> 'details', '{}'),
      (ev ->> 'case_id')::uuid, (ev ->> 'session_id')::uuid, (ev ->> 'request_id')::uuid,
      ev ->> 'actor_type', ev ->> 'actor_id');
  end loop;
  return ids;
end $$;

-- Walks the chain in event_id order. first_bad_event_id is the first row whose prev_hash or hash does not match;
-- head_hash is the last good hash. hash_at is the hash after the first p_at rows (null if there are fewer good rows),
-- so a recorded anchor {checked, head_hash} can be checked later: rewriting old rows changes hash_at, and
-- removing the newest rows makes checked smaller than the anchor.
create or replace function audit.verify_chain(p_at bigint default null)
returns table (checked bigint, first_bad_event_id bigint, head_hash text, hash_at text)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare e audit.events; prev text := repeat('0', 64);
begin
  checked := 0;
  if p_at = 0 then hash_at := prev; end if;
  for e in select * from audit.events order by event_id loop
    checked := checked + 1;
    if e.prev_hash <> prev or e.hash <> audit.event_hash(e) then
      first_bad_event_id := e.event_id; head_hash := prev; return next; return;
    end if;
    prev := e.hash;
    if checked = p_at then hash_at := prev; end if;
  end loop;
  head_hash := prev; return next;
end $$;

-- Read-only wrapper for scripts/verify_audit.py; returns counts and hashes only.
create or replace function public.audit_verify_chain(p_at bigint default null)
returns table (checked bigint, first_bad_event_id bigint, head_hash text, hash_at text)
language sql
stable
security definer
set search_path = ''
as $$ select * from audit.verify_chain(p_at) $$;

revoke all on function audit.reject_change(), audit.is_safe(jsonb, int), audit.event_hash(audit.events), audit.verify_chain(bigint),
  audit.record(text, text, text, text, text, jsonb, uuid, uuid, uuid, text, text, bigint) from public, anon, authenticated;
revoke all on function public.audit_record_batch(jsonb), public.audit_verify_chain(bigint) from public, anon, authenticated;
grant execute on function public.audit_record_batch(jsonb) to anon, authenticated;
grant execute on function public.audit_verify_chain(bigint) to authenticated;
