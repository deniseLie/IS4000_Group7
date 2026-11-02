-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0089$
declare previous supabase_migrations.schema_migrations; script text:=$source_0089$-- Released source SHA256: f5563cdebb66bfb7e13b916492630a21a03e45f0b67081a29b361d00ecc420cf
-- F12 + F14 (Persona 2, Sophie): the relationship manager's book-based access, RM read functions and RM-approved writes.
-- Every RM function is a security definer that first resolves the caller to an advisor from administrator-owned
-- app_metadata (demobank_role=rm, rm_id) AND the rm.staff_logins link to auth.uid(); it then checks the client is in that
-- advisor's current book (rm.assignments, ended_on is null). A lookup outside the book is refused and audited (the refusal
-- commits, the data never leaves). Customer RLS is unchanged: no client can read any RM table added here.
-- Nothing here lets the RM assistant (the model) write: writes are separate functions the API calls only after the RM
-- approves the exact content in the RM workspace. Synthetic data only.

-- 1. Schema additions ---------------------------------------------------------------------------------------------------
-- Meeting notes: who may see them, what an RM-documented interaction recorded, and the RM's approval.
alter table rm.meeting_notes
  add column visibility text not null default 'book' check (visibility in ('book','author_only')),
  add column source text not null default 'seed' check (source in ('seed','rm_documented')),
  add column title text check (title is null or length(btrim(title)) between 1 and 160),
  add column decisions text[] not null default '{}' check (cardinality(decisions)<=10),
  add column client_requests text[] not null default '{}' check (cardinality(client_requests)<=10),
  add column source_notes text check (source_notes is null or length(source_notes) between 1 and 8000),
  add column structured jsonb check (structured is null or jsonb_typeof(structured)='object'),
  add column revisions jsonb not null default '[]' check (jsonb_typeof(revisions)='array'),
  add column approved_by uuid references auth.users(id) on delete set null,
  add column approved_at timestamptz,
  add column request_key uuid unique;
comment on column rm.meeting_notes.visibility is 'book: readable by the client''s current RM; author_only: only the RM who wrote it';

-- Client-stated preferences, each tied to the note or shared summary it was recorded from.
create table rm.client_preferences (
  preference_id  uuid primary key default gen_random_uuid(),
  customer_id    bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  preference     text not null check (length(btrim(preference)) between 1 and 300),
  recorded_on    date not null,
  recorded_by    uuid references rm.advisors(advisor_id),
  source_note_id uuid references rm.meeting_notes(note_id) on delete set null
);
create index client_preferences_customer_idx on rm.client_preferences(customer_id);

-- Upcoming dates an RM follows up on: maturities and scheduled reviews (synthetic).
create table rm.client_milestones (
  milestone_id uuid primary key default gen_random_uuid(),
  customer_id  bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  kind         text not null check (kind in ('fixed_deposit_maturity','investment_maturity','portfolio_review','insurance_renewal','account_review')),
  title        text not null check (length(btrim(title)) between 1 and 160),
  due_on       date not null,
  amount       numeric(14,2),
  currency     char(3) check (currency is null or currency ~ '^[A-Z]{3}$'),
  details      text check (details is null or length(details)<=300)
);
create index client_milestones_customer_idx on rm.client_milestones(customer_id,due_on);

-- Internal RM follow-up tasks (never shown to clients), linked to the interaction they came from.
create table rm.tasks (
  task_id      uuid primary key default gen_random_uuid(),
  advisor_id   uuid not null references rm.advisors(advisor_id),
  customer_id  bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  note_id      uuid references rm.meeting_notes(note_id) on delete set null,
  title        text not null check (length(btrim(title)) between 1 and 200),
  details      text check (details is null or length(details)<=500),
  owner_label  text check (owner_label is null or length(btrim(owner_label)) between 1 and 80),
  due_on       date,
  status       text not null default 'open' check (status in ('open','done','cancelled')),
  request_key  uuid unique,
  created_at   timestamptz not null default now(),
  completed_at timestamptz
);
create index tasks_advisor_idx on rm.tasks(advisor_id,status,due_on);
create index tasks_customer_idx on rm.tasks(customer_id);

-- Messages an RM approved and sent to a client (on a ticket or as an Inbox message), and failed sends.
create table rm.client_messages (
  message_id     uuid primary key,
  customer_id    bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id     uuid not null references rm.advisors(advisor_id),
  ticket_id      uuid references support.tickets(ticket_id) on delete set null,
  body           text check (body is null or length(body) between 1 and 500),
  status         text not null check (status in ('sent','failed')),
  failure_code   text check (failure_code is null or failure_code ~ '^[a-z_]{1,40}$'),
  draft_revision int not null default 1 check (draft_revision between 1 and 50),
  edited         boolean not null default false,
  status_change  text check (status_change is null or status_change in ('in_review','waiting_on_client','resolved')),
  approved_by    uuid references auth.users(id) on delete set null,
  approved_at    timestamptz not null default now(),
  check (status<>'sent' or body is not null)
);
create index client_messages_customer_idx on rm.client_messages(customer_id,approved_at desc);

-- Follow-up suggestions the RM dismissed (keys are derived from the underlying record and its date).
create table rm.dismissed_followups (
  advisor_id   uuid not null references rm.advisors(advisor_id),
  item_key     text not null check (item_key ~ '^[a-z_]{1,30}:[A-Za-z0-9_.:-]{1,100}$'),
  dismissed_at timestamptz not null default now(),
  primary key (advisor_id,item_key)
);

alter table rm.appointments add column purpose text check (purpose is null or length(btrim(purpose)) between 1 and 200);
alter table rm.advisor_blocks add column reason text check (reason is null or length(btrim(reason)) between 1 and 80);

-- RM-raised service requests are client-trackable tickets with their own source and an idempotency key.
alter table support.tickets drop constraint tickets_source_check;
alter table support.tickets add constraint tickets_source_check check (source in ('form','chat','handoff','rm_request','seed','rm'));
alter table support.tickets add column rm_request_key uuid unique, add column rm_note_id uuid references rm.meeting_notes(note_id) on delete set null;

do $$
declare t text;
begin
  foreach t in array array['rm.client_preferences','rm.client_milestones','rm.tasks','rm.client_messages','rm.dismissed_followups'] loop
    execute format('alter table %s enable row level security',t);
    execute format('revoke all on %s from public,anon,authenticated',t);
  end loop;
end $$;
create trigger audit_row after insert or update or delete on rm.tasks for each row execute function audit.row_change('task_id','status');
create trigger audit_row after insert or update or delete on rm.client_messages for each row execute function audit.row_change('message_id','status','edited','status_change');

insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('rm.client_update',1,'en','in_app','Update from your relationship manager','Your relationship manager updated request {reference}. See My requests for the details.',true),
  ('rm.client_message',1,'en','in_app','Message from your relationship manager','{message}',true),
  ('rm.request_raised',1,'en','in_app','Request {reference} raised for you','Your relationship manager raised request {reference} with the {owner_team} on your behalf. Check My requests for updates.',true)
on conflict do nothing;

-- 2. Audit actor: an RM (demobank_role=rm) is staff, not a customer. Same function as 0031 plus that one branch. ----------
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
  elsif auth.uid() is not null and auth.jwt()#>>'{app_metadata,demobank_role}'='support' and auth.jwt()->>'aal'='aal2' then
    e.actor_type := 'staff'; e.actor_id := auth.uid()::text;
  elsif auth.uid() is not null and auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then
    e.actor_type := 'staff'; e.actor_id := auth.uid()::text;
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

-- Status notes read "The your relationship manager will review it." for RM-owned tickets: say "Your relationship manager"
-- instead, and correct the notes already on the public timeline (the client sees them in My requests).
create or replace function support.status_note(p_status text,p_team text) returns text language sql immutable set search_path='' as $$
  select case p_status
    when 'submitted' then 'Received. '||case when p_team ilike 'your %' then p_team||' will review it.' else 'The '||lower(p_team)||' will review it.' end
    when 'in_review' then case when p_team ilike 'your %' then p_team||' is reviewing it.' else 'The '||lower(p_team)||' is reviewing it.' end
    when 'waiting_on_client' then 'The bank needs something from you before it can continue.'
    when 'resolved' then 'Marked resolved by the bank.'
    else 'Closed.' end $$;
update support.ticket_events set public_note=replace(public_note,'The your relationship manager','Your relationship manager')
  where public_note like '%The your relationship manager%';

-- 3. Identity and book helpers (private) ------------------------------------------------------------------------------
create function rm.today() returns date language sql stable set search_path='' as
$$ select (clock_timestamp() at time zone 'Asia/Singapore')::date $$;
create function rm.sgt(p timestamptz) returns text language sql stable set search_path='' as
$$ select to_char(p at time zone 'Asia/Singapore','FMDy FMDD Mon YYYY, FMHH12:MI am') $$;
create function rm.day(p date) returns text language sql immutable set search_path='' as
$$ select to_char(p,'FMDD Mon YYYY') $$;
create function rm.display_name(p text) returns text language sql immutable set search_path='' as
$$ select regexp_replace(p,'\s*\(DEMO\)\s*$','') $$;

-- The advisor behind this login, or 42501. Both the administrator-owned claim and the staff login link must agree.
create function rm.current_advisor() returns uuid language plpgsql stable security definer set search_path='' as $$
declare advisor uuid;
begin
  if auth.uid() is null or auth.jwt()->>'role' is distinct from 'authenticated'
      or auth.jwt()#>>'{app_metadata,demobank_role}' is distinct from 'rm' then
    raise exception 'relationship manager tools require an administrator-assigned rm role' using errcode='42501';
  end if;
  select s.advisor_id into advisor from rm.staff_logins s join rm.advisors a using(advisor_id)
    where s.auth_user_id=auth.uid() and a.is_active;
  if advisor is null or auth.jwt()#>>'{app_metadata,rm_id}' is distinct from advisor::text then
    raise exception 'this login is not linked to an active relationship manager' using errcode='42501';
  end if;
  return advisor;
end $$;

create function rm.in_book(p_advisor uuid,p_customer bigint) returns boolean language sql stable security definer set search_path='' as $$
  select exists(select 1 from rm.assignments where advisor_id=p_advisor and customer_id=p_customer and ended_on is null and started_on<=rm.today())
$$;

-- A refused lookup is recorded (and committed) before the caller returns {"denied":true}; no client data is returned.
create function rm.deny(p_customer bigint,p_tool text) returns jsonb language plpgsql volatile security definer set search_path='' as $$
begin
  perform audit.record('policy','policy.rm_client_denied','denied','banking.customer_profiles',coalesce(p_customer::text,'unknown'),
    jsonb_build_object('tool',p_tool,'reason','not_in_book'),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
    p_customer_id=>(select customer_id from banking.customer_profiles where customer_id=p_customer));
  return jsonb_build_object('denied',true,'as_of',clock_timestamp(),'data',null);
end $$;

-- Every successful RM read: one data.read audit event (ids and counts only) and a call id the API can cite.
create function rm.result(p_customer bigint,p_tool text,p_resource text,p_data jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare call_id uuid:=gen_random_uuid(); n int;
begin
  n:=case when p_data is null or p_data='null' then 0 when jsonb_typeof(p_data)='array' then jsonb_array_length(p_data) else 1 end;
  perform audit.record('data','data.read','success',p_resource,call_id::text,jsonb_build_object('tool',p_tool,'result_count',n),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer);
  return jsonb_build_object('denied',false,'customer_id',p_customer,'call_id',call_id,'as_of',clock_timestamp(),'data',p_data);
end $$;

-- Free 30-minute times in the advisor's own calendar (same rules as appointments_find_slots in 0012).
create function rm.free_slots(p_advisor uuid,p_from date,p_to date,p_limit int)
returns table(starts_at timestamptz,ends_at timestamptz) language sql stable security definer set search_path='' as $$
  select s.start_time,s.start_time+interval '30 minutes'
  from rm.advisors a
  cross join generate_series(p_from::timestamp,p_to::timestamp,interval '1 day') d(day)
  join rm.working_hours h on h.advisor_id=a.advisor_id and h.weekday=extract(isodow from d.day)
  cross join lateral generate_series((d.day::date+h.starts_at) at time zone a.timezone,
    (d.day::date+h.ends_at) at time zone a.timezone-interval '30 minutes',interval '30 minutes') s(start_time)
  where a.advisor_id=p_advisor and s.start_time>clock_timestamp()
    and not exists(select 1 from rm.advisor_blocks b where b.advisor_id=a.advisor_id
      and tstzrange(b.starts_at,b.ends_at,'[)') && tstzrange(s.start_time,s.start_time+interval '30 minutes','[)'))
    and not exists(select 1 from rm.appointments ap where ap.advisor_id=a.advisor_id
      and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())
      and tstzrange(ap.starts_at,ap.ends_at,'[)') && tstzrange(s.start_time,s.start_time+interval '30 minutes','[)'))
  order by s.start_time limit p_limit
$$;

-- Which ticket categories belong to specialist teams whose case detail the RM must not see.
create function rm.restricted_category(p_category text) returns boolean language sql immutable set search_path='' as
$$ select p_category in ('suspected_fraud','login_access') $$;
create function rm.rm_owned(p_owner_team text) returns boolean language sql immutable set search_path='' as
$$ select p_owner_team='Your relationship manager' $$;

-- 4. Record builders (private; callers have already checked the book) ------------------------------------------------
create function rm.ticket_json(t support.tickets,p_full boolean) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('reference',t.reference,'customer_id',t.customer_id,'client',rm.display_name(p.full_name),
    'category',t.category,'category_label',c.label,'owner_team',t.owner_team,'rm_owned',rm.rm_owned(t.owner_team),
    'restricted',rm.restricted_category(t.category),'urgency',t.urgency,'status',t.status,
    'client_safe_status',support.status_note(t.status,t.owner_team),'source',t.source,
    'created_at',t.created_at,'created_sgt',rm.sgt(t.created_at),'age_hours',floor(extract(epoch from clock_timestamp()-t.created_at)/3600)::int,
    'target_at',t.target_at,'target_sgt',rm.sgt(t.target_at),
    'overdue',t.status in ('submitted','in_review') and t.target_at<clock_timestamp(),
    -- Restricted specialist cases (fraud, login) never show the client's wording or details to the RM.
    'summary',case when rm.restricted_category(t.category) and not rm.rm_owned(t.owner_team) then null else t.summary end,
    'details',case when p_full and rm.rm_owned(t.owner_team) then t.details else null end,
    'last_update',(select jsonb_build_object('at',e.created_at,'at_sgt',rm.sgt(e.created_at),'status',e.status,'note',e.public_note)
      from support.ticket_events e where e.ticket_id=t.ticket_id and not e.internal order by e.created_at desc,e.event_id desc limit 1))
  from banking.customer_profiles p, support.request_categories c where p.customer_id=t.customer_id and c.category=t.category
$$;

create function rm.note_json(n rm.meeting_notes,p_advisor uuid) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('note_id',n.note_id,'met_on',n.met_on,'met_on_text',rm.day(n.met_on),'format',n.format,
    'author',rm.display_name(a.full_name),'written_by_current_rm',n.advisor_id=p_advisor,'title',n.title,'summary',n.summary,
    'commitments',to_jsonb(n.commitments),'decisions',to_jsonb(n.decisions),'client_requests',to_jsonb(n.client_requests),
    'source',n.source,'approved_at',n.approved_at,'has_source_notes',n.source_notes is not null)
  from rm.advisors a where a.advisor_id=n.advisor_id
$$;

-- Notes the advisor may read: notes shared with the client's current RM (any author), plus her own private notes.
create function rm.visible_notes(p_advisor uuid,p_customer bigint) returns setof rm.meeting_notes language sql stable security definer set search_path='' as $$
  select * from rm.meeting_notes where customer_id=p_customer and (visibility='book' or advisor_id=p_advisor) order by met_on desc,created_at desc limit 50
$$;

create function rm.active_consent(p_advisor uuid,p_customer bigint) returns setof consent.grants language sql stable security definer set search_path='' as $$
  select * from consent.grants where customer_id=p_customer and purpose='rm_brief' and recipient=p_advisor::text
    and revoked_at is null and (expires_at is null or expires_at>clock_timestamp())
$$;

create function rm.client_data(p_advisor uuid,p_customer bigint) returns jsonb language plpgsql stable security definer set search_path='' as $$
declare today date:=rm.today(); data jsonb; prev jsonb; gaps text[]:='{}';
begin
  select jsonb_build_object('previous_advisor',rm.display_name(a.full_name),'previous_from',x.started_on,'previous_to',x.ended_on,
      'previous_to_text',rm.day(x.ended_on)) into prev
    from rm.assignments x join rm.advisors a using(advisor_id)
    where x.customer_id=p_customer and x.ended_on is not null and x.advisor_id<>p_advisor order by x.ended_on desc limit 1;
  select jsonb_build_object(
    'profile',(select jsonb_build_object('customer_id',p.customer_id,'name',rm.display_name(p.full_name),'preferred_language',p.preferred_language,
        'employment_status',p.employment_status,'income_range',p.income_range) from banking.customer_profiles p where p.customer_id=p_customer),
    'relationship',(select jsonb_build_object('assigned_since',x.started_on,'assigned_since_text',rm.day(x.started_on),'inherited',prev is not null)||coalesce(prev,'{}')
        from rm.assignments x where x.customer_id=p_customer and x.advisor_id=p_advisor and x.ended_on is null),
    'accounts',(select coalesce(jsonb_agg(jsonb_build_object('type',account_type,'number','****'||right(masked_account_number,4),'currency',currency,
        'balance',balance,'status',status) order by account_id),'[]') from banking.bank_accounts where customer_id=p_customer),
    'cards',(select coalesce(jsonb_agg(jsonb_build_object('name',card_name,'network',network,'last4',last4,'status',status,'currency',currency,
        'credit_limit',credit_limit,'outstanding_balance',outstanding_balance,'minimum_payment_due',minimum_payment_due,
        'payment_due_date',payment_due_date) order by card_id),'[]') from banking.credit_cards where customer_id=p_customer),
    'loans',(select coalesce(jsonb_agg(jsonb_build_object('type',loan_type,'currency',currency,'outstanding_amount',outstanding_amount,
        'monthly_repayment',monthly_repayment,'next_due_date',next_due_date,'status',status) order by loan_id),'[]') from banking.loans where customer_id=p_customer),
    'compliance',(select jsonb_build_object('title',c.title,'status',c.status,'due_date',c.due_date,'due_text',rm.day(c.due_date),
        'outstanding_items',(select coalesce(jsonb_agg(jsonb_build_object('label',i.label,'status',i.submission_status) order by i.sort_order),'[]')
          from compliance.requested_items i where i.case_id=c.case_id and i.submission_status in ('not_submitted','rejected','unknown')),
        'items_with_bank',(select count(*) from compliance.requested_items i where i.case_id=c.case_id and i.submission_status='submitted_awaiting_review'),
        'open_bank_questions',(select coalesce(jsonb_agg(jsonb_build_object('question',q.question,'due_date',q.due_date,'due_text',rm.day(q.due_date))),'[]')
          from compliance.info_requests q where q.case_id=c.case_id and q.status='open'))
        from compliance.cases c where c.customer_id=p_customer and c.status not in ('completed','closed') order by c.due_date nulls last limit 1),
    'tickets',(select coalesce(jsonb_agg(rm.ticket_json(t,false) order by t.created_at desc),'[]') from support.tickets t
        where t.customer_id=p_customer and t.status in ('submitted','in_review','waiting_on_client')),
    'appointments',(select coalesce(jsonb_agg(jsonb_build_object('appointment_id',ap.appointment_id,'starts_at',ap.starts_at,'when',rm.sgt(ap.starts_at),
        'date',(ap.starts_at at time zone 'Asia/Singapore')::date,'format',ap.format,'status',ap.status,'purpose',ap.purpose,
        'hold_expires',case when ap.status='held' then rm.sgt(ap.hold_expires_at) end) order by ap.starts_at),'[]')
        from rm.appointments ap where ap.customer_id=p_customer and ap.advisor_id=p_advisor and ap.starts_at>clock_timestamp()
          and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())),
    'interactions',(select coalesce(jsonb_agg(rm.note_json(n,p_advisor) order by n.met_on desc),'[]')
        from (select * from rm.visible_notes(p_advisor,p_customer) limit 5) n),
    'preferences',(select coalesce(jsonb_agg(jsonb_build_object('preference',f.preference,'recorded_on',f.recorded_on,'recorded_text',rm.day(f.recorded_on),
        'stated_by','client','source_note_id',f.source_note_id) order by f.recorded_on desc),'[]') from rm.client_preferences f
        where f.customer_id=p_customer and (f.source_note_id is null or exists(select 1 from rm.visible_notes(p_advisor,p_customer) v where v.note_id=f.source_note_id))),
    'milestones',(select coalesce(jsonb_agg(jsonb_build_object('kind',m.kind,'title',m.title,'due_on',m.due_on,'due_text',rm.day(m.due_on),
        'amount',m.amount,'currency',m.currency,'details',m.details) order by m.due_on),'[]') from rm.client_milestones m
        where m.customer_id=p_customer and m.due_on between today and today+120),
    'rm_requests',(select coalesce(jsonb_agg(jsonb_build_object('what',r.what,'status',r.status,'urgency',r.urgency,'deadline',r.deadline,
        'raised_with',rm.display_name(a.full_name),'created_at',rm.sgt(r.created_at)) order by r.created_at desc),'[]')
        from rm.requests r left join rm.advisors a on a.advisor_id=r.advisor_id where r.customer_id=p_customer and r.status<>'closed'),
    'tasks',(select coalesce(jsonb_agg(jsonb_build_object('task_id',k.task_id,'title',k.title,'owner',k.owner_label,'due_on',k.due_on,
        'due_text',rm.day(k.due_on),'overdue',k.due_on<today) order by k.due_on nulls last),'[]') from rm.tasks k
        where k.customer_id=p_customer and k.advisor_id=p_advisor and k.status='open'),
    'shared_note_available',exists(select 1 from rm.shared_summaries s join rm.active_consent(p_advisor,p_customer) g on g.grant_id=s.consent_grant_id
        where s.customer_id=p_customer and s.advisor_id=p_advisor)) into data;
  if data->'compliance' is null or data->'compliance'='null' then gaps:=array_append(gaps,'no_open_compliance_review'); end if;
  if jsonb_array_length(data->'appointments')=0 then gaps:=array_append(gaps,'no_upcoming_appointment'); end if;
  if jsonb_array_length(data->'preferences')=0 then gaps:=array_append(gaps,'no_recorded_preferences'); end if;
  if jsonb_array_length(data->'interactions')=0 then gaps:=array_append(gaps,'no_meeting_notes');
  elsif ((data->'interactions'->0->>'met_on')::date)<today-180 then gaps:=array_append(gaps,'no_interaction_in_180_days'); end if;
  return data||jsonb_build_object('gaps',to_jsonb(gaps),'today',today,'today_text',rm.day(today));
end $$;

-- 5. RM reads (public RPCs) -------------------------------------------------------------------------------------------
create function public.rm_profile() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  return jsonb_build_object('advisor',(select jsonb_build_object('advisor_id',a.advisor_id,'name',rm.display_name(a.full_name),'role',a.role,
      'team',a.team_name,'email',a.email,'phone',a.phone) from rm.advisors a where a.advisor_id=advisor),
    'book_size',(select count(*) from rm.assignments where advisor_id=advisor and ended_on is null and started_on<=rm.today()),
    'today',rm.today(),'as_of',clock_timestamp());
end $$;

-- The advisor's book. A search that matches no client in the book but does match someone else is audited as refused.
create function public.rm_book(p_query text default null) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); q text:=nullif(btrim(coalesce(p_query,'')),''); data jsonb; today date:=rm.today(); outside bigint;
begin
  if q is not null and (length(q)>80 or q ~ '[%_\\]') then raise exception 'search with up to 80 letters' using errcode='22023'; end if;
  select coalesce(jsonb_agg(row order by row->>'name'),'[]') into data from (
    select jsonb_build_object('customer_id',p.customer_id,'name',rm.display_name(p.full_name),'income_range',p.income_range,
      'employment_status',p.employment_status,'assigned_since',a.started_on,
      'inherited_from',(select rm.display_name(v.full_name) from rm.assignments x join rm.advisors v using(advisor_id)
         where x.customer_id=p.customer_id and x.ended_on is not null and x.advisor_id<>advisor order by x.ended_on desc limit 1),
      'open_rm_tickets',(select count(*) from support.tickets t where t.customer_id=p.customer_id and rm.rm_owned(t.owner_team)
         and t.status in ('submitted','in_review','waiting_on_client')),
      'open_specialist_tickets',(select count(*) from support.tickets t where t.customer_id=p.customer_id and not rm.rm_owned(t.owner_team)
         and t.status in ('submitted','in_review','waiting_on_client')),
      'urgent',exists(select 1 from support.tickets t where t.customer_id=p.customer_id and t.urgency='urgent' and t.status in ('submitted','in_review','waiting_on_client')),
      'overdue',(select count(*) from support.tickets t where t.customer_id=p.customer_id and t.status in ('submitted','in_review') and t.target_at<clock_timestamp()),
      'next_appointment',(select rm.sgt(min(ap.starts_at)) from rm.appointments ap where ap.customer_id=p.customer_id and ap.advisor_id=advisor
         and ap.status='confirmed' and ap.starts_at>clock_timestamp()),
      'review_due',(select min(c.due_date) from compliance.cases c where c.customer_id=p.customer_id and c.status not in ('completed','closed'))) row
    from rm.assignments a join banking.customer_profiles p using(customer_id)
    where a.advisor_id=advisor and a.ended_on is null and a.started_on<=today
      and (q is null or rm.display_name(p.full_name) ilike '%'||q||'%')) x;
  if q is not null and jsonb_array_length(data)=0 then
    for outside in select p.customer_id from banking.customer_profiles p where rm.display_name(p.full_name) ilike '%'||q||'%' limit 5 loop
      perform rm.deny(outside,'rm.book');
    end loop;
  end if;
  return rm.result(null,'rm.book','rm.assignments',data);
end $$;

-- Names mentioned in an RM's message: book clients are returned; anyone else named is refused and audited, and the
-- reply is the same whether or not that person exists. A name matches on two consecutive name parts or one part of
-- at least four letters, as whole words.
create function public.rm_match_clients(p_text text) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); body text:=lower(left(coalesce(p_text,''),4000)); r record; inside jsonb:='[]'; denied int:=0; parts text[]; hit boolean; i int;
begin
  for r in select customer_id,rm.display_name(full_name) name from banking.customer_profiles loop
    parts:=regexp_split_to_array(lower(regexp_replace(r.name,'[^A-Za-z ]','','g')),'\s+');
    hit:=false;
    for i in 1..coalesce(array_length(parts,1),0) loop
      if length(parts[i])>=4 and body ~ ('\m'||parts[i]||'\M') then hit:=true; end if;
      if i<array_length(parts,1) and body ~ ('\m'||parts[i]||'\s+'||parts[i+1]||'\M') then hit:=true; end if;
    end loop;
    continue when not hit;
    if rm.in_book(advisor,r.customer_id) then inside:=inside||jsonb_build_object('customer_id',r.customer_id,'name',r.name);
    else denied:=denied+1; perform rm.deny(r.customer_id,'rm.match_clients'); end if;
  end loop;
  return jsonb_build_object('in_book',inside,'outside_book',denied>0,'as_of',clock_timestamp());
end $$;

create function public.rm_client_profile(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.client_profile'); end if;
  return rm.result(p_customer_id,'rm.client_profile','banking.customer_profiles',rm.client_data(advisor,p_customer_id));
end $$;

create function public.rm_meeting_notes(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.meeting_notes'); end if;
  return rm.result(p_customer_id,'rm.meeting_notes','rm.meeting_notes',jsonb_build_object(
    'notes',(select coalesce(jsonb_agg(rm.note_json(n,advisor) order by n.met_on desc),'[]') from rm.visible_notes(advisor,p_customer_id) n),
    'withheld_private_notes',(select count(*) from rm.meeting_notes where customer_id=p_customer_id and visibility='author_only' and advisor_id<>advisor)));
end $$;

-- The client's handover note: read only under an active rm_brief grant to this advisor; the check itself is audited.
create function public.rm_shared_summary(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); active boolean; data jsonb;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.shared_summary'); end if;
  active:=exists(select 1 from rm.active_consent(advisor,p_customer_id));
  perform audit.record('consent','consent.rm_brief_checked',case when active then 'success' else 'denied' end,'consent.grants',p_customer_id::text,
    jsonb_build_object('purpose','rm_brief','active',active),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  data:=jsonb_build_object('consent_active',active,
    'shared',case when active then (select coalesce(jsonb_agg(jsonb_build_object('summary_id',s.summary_id,'content',s.content,'shared_at',s.shared_at,
        'shared_text',rm.sgt(s.shared_at),'consent_expires',rm.sgt(g.expires_at)) order by s.shared_at desc),'[]')
      from rm.shared_summaries s join rm.active_consent(advisor,p_customer_id) g on g.grant_id=s.consent_grant_id where s.customer_id=p_customer_id) else '[]' end,
    'excluded_revoked',(select count(*) from rm.shared_summaries s join consent.grants g on g.grant_id=s.consent_grant_id
      where s.customer_id=p_customer_id and g.recipient=advisor::text and g.revoked_at is not null),
    'excluded_expired',(select count(*) from rm.shared_summaries s join consent.grants g on g.grant_id=s.consent_grant_id
      where s.customer_id=p_customer_id and g.recipient=advisor::text and g.revoked_at is null and g.expires_at<=clock_timestamp()));
  return rm.result(p_customer_id,'rm.shared_summary','rm.shared_summaries',data);
end $$;

-- Every open ticket in the book: the RM's own work, and specialist-managed cases with client-safe status only.
create function public.rm_tickets() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  return rm.result(null,'rm.tickets','support.tickets',(select jsonb_build_object(
    'rm_owned',coalesce(jsonb_agg(rm.ticket_json(t,false) order by (t.urgency='urgent') desc,(t.status in ('submitted','in_review') and t.target_at<clock_timestamp()) desc,
        array_position(array['urgent','high','normal','low'],t.urgency),t.created_at) filter (where rm.rm_owned(t.owner_team)),'[]'),
    'specialist',coalesce(jsonb_agg(rm.ticket_json(t,false) order by (t.urgency='urgent') desc,array_position(array['urgent','high','normal','low'],t.urgency),t.created_at)
        filter (where not rm.rm_owned(t.owner_team)),'[]'))
    from support.tickets t where t.status in ('submitted','in_review','waiting_on_client')
      and exists(select 1 from rm.assignments a where a.customer_id=t.customer_id and a.advisor_id=advisor and a.ended_on is null and a.started_on<=rm.today())));
end $$;

-- One ticket. RM-owned: full history, what the bank and the chatbot told the client, and the handover context.
-- Specialist-managed: client-safe status and the public timeline only; restricted cases never show the client's wording.
create function public.rm_ticket(p_reference text) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); t support.tickets; owned boolean; data jsonb;
begin
  if p_reference is null or p_reference !~ '^TCK-[0-9]{6,}$' then raise exception 'use a TCK- reference' using errcode='22023'; end if;
  select * into t from support.tickets where reference=p_reference;
  if not found or not rm.in_book(advisor,t.customer_id) then return rm.deny(t.customer_id,'rm.ticket'); end if;
  owned:=rm.rm_owned(t.owner_team);
  data:=rm.ticket_json(t,true)||jsonb_build_object(
    'category_info',(select jsonb_build_object('label',c.label,'description',c.description,'decided_by',c.decided_by,'target_hours',c.target_hours,
        'fields',c.fields,'documents',to_jsonb(c.documents),'kb_article',c.kb_article) from support.request_categories c where c.category=t.category),
    'told_by_bank',(select coalesce(jsonb_agg(jsonb_build_object('at',e.created_at,'at_sgt',rm.sgt(e.created_at),'status',e.status,'note',e.public_note)
        order by e.created_at,e.event_id),'[]') from support.ticket_events e where e.ticket_id=t.ticket_id and not e.internal),
    'internal_notes',case when owned then (select coalesce(jsonb_agg(jsonb_build_object('at_sgt',rm.sgt(e.created_at),'note',e.public_note) order by e.created_at),'[]')
        from support.ticket_events e where e.ticket_id=t.ticket_id and e.internal and e.public_note is not null) else '[]' end,
    'handover_context',case when owned then (select h.summary from support.handoffs h where h.handoff_id=t.handoff_id) end,
    'told_by_assistant',case when owned and t.case_id is not null then (select coalesce(jsonb_agg(jsonb_build_object('at_sgt',rm.sgt(m.created_at),'reply',left(m.redacted_text,800))
        order by m.created_at),'[]') from conversation.turns u join conversation.messages m on m.message_id=u.answer_message_id
        where u.case_id=t.case_id and u.customer_id=t.customer_id and u.state='completed') else '[]' end,
    'client_messages',case when owned and t.case_id is not null then (select coalesce(jsonb_agg(jsonb_build_object('at_sgt',rm.sgt(m.created_at),'text',left(m.redacted_text,500))
        order by m.created_at),'[]') from conversation.turns u join conversation.messages m on m.message_id=u.user_message_id
        where u.case_id=t.case_id and u.customer_id=t.customer_id) else '[]' end,
    'rm_request',case when owned then (select jsonb_build_object('what',r.what,'status',r.status,'raised_with',rm.display_name(a.full_name),'deadline',r.deadline)
        from rm.requests r left join rm.advisors a on a.advisor_id=r.advisor_id where r.request_id=t.request_id) end,
    'attachments',(select coalesce(jsonb_agg(d.file_name order by x.attached_at),'[]') from support.ticket_attachments x
        join compliance.documents d on d.document_id=x.document_id where x.ticket_id=t.ticket_id),
    'sent_by_rm',(select coalesce(jsonb_agg(jsonb_build_object('at_sgt',rm.sgt(cm.approved_at),'text',cm.body) order by cm.approved_at),'[]')
        from rm.client_messages cm where cm.ticket_id=t.ticket_id and cm.status='sent'));
  return rm.result(t.customer_id,'rm.ticket','support.tickets',data);
end $$;

-- Open compliance reviews across the book (client-safe fields; no internal risk rating or reviewer notes).
create function public.rm_review_status() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  return rm.result(null,'rm.review_status','compliance.cases',(select coalesce(jsonb_agg(jsonb_build_object('customer_id',c.customer_id,
      'client',rm.display_name(p.full_name),'title',c.title,'status',c.status,'due_date',c.due_date,'due_text',rm.day(c.due_date),
      'outstanding_items',(select coalesce(jsonb_agg(i.label order by i.sort_order),'[]') from compliance.requested_items i
        where i.case_id=c.case_id and i.submission_status in ('not_submitted','rejected','unknown')),
      'open_bank_questions',(select count(*) from compliance.info_requests q where q.case_id=c.case_id and q.status='open')) order by c.due_date),'[]')
    from compliance.cases c join banking.customer_profiles p using(customer_id)
    where c.status not in ('completed','closed') and rm.in_book(advisor,c.customer_id)));
end $$;

create function public.rm_appointments(p_from date default null,p_to date default null) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); f date:=coalesce(p_from,rm.today()); t date:=coalesce(p_to,coalesce(p_from,rm.today())+13);
begin
  if t<f or t-f>62 then raise exception 'choose a range of up to 62 days' using errcode='22023'; end if;
  return rm.result(null,'rm.appointments','rm.appointments',(select coalesce(jsonb_agg(jsonb_build_object('appointment_id',ap.appointment_id,
      'customer_id',ap.customer_id,'client',rm.display_name(p.full_name),'starts_at',ap.starts_at,'ends_at',ap.ends_at,'when',rm.sgt(ap.starts_at),
      'date',(ap.starts_at at time zone 'Asia/Singapore')::date,'format',ap.format,'status',ap.status,'purpose',ap.purpose,
      'hold_expires',case when ap.status='held' then rm.sgt(ap.hold_expires_at) end) order by ap.starts_at),'[]')
    from rm.appointments ap join banking.customer_profiles p using(customer_id)
    where ap.advisor_id=advisor and rm.in_book(advisor,ap.customer_id)
      and (ap.starts_at at time zone 'Asia/Singapore')::date between f and t
      and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())));
end $$;

-- The next 14 days of the advisor's calendar: weekly hours, blocked time, bookings and free times clients can book (CR17).
create function public.rm_availability() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); today date:=rm.today();
begin
  return jsonb_build_object('today',today,'as_of',clock_timestamp(),
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

create function public.rm_tasks(p_customer_id bigint default null) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  if p_customer_id is not null and not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.tasks'); end if;
  return rm.result(p_customer_id,'rm.tasks','rm.tasks',(select coalesce(jsonb_agg(jsonb_build_object('task_id',k.task_id,'customer_id',k.customer_id,
      'client',rm.display_name(p.full_name),'title',k.title,'details',k.details,'owner',k.owner_label,'due_on',k.due_on,'due_text',rm.day(k.due_on),
      'status',k.status,'overdue',k.status='open' and k.due_on<rm.today(),'note_id',k.note_id,
      'note_date',(select rm.day(n.met_on) from rm.meeting_notes n where n.note_id=k.note_id),'created_at',rm.sgt(k.created_at))
      order by (k.status='open') desc,k.due_on nulls last,k.created_at),'[]')
    from rm.tasks k join banking.customer_profiles p using(customer_id)
    where k.advisor_id=advisor and rm.in_book(advisor,k.customer_id) and (p_customer_id is null or k.customer_id=p_customer_id)
      and (k.status='open' or k.completed_at>clock_timestamp()-interval '14 days')));
end $$;

-- Candidate follow-ups across the book, each with its reason date and source record; dismissed items are left out.
create function public.rm_followups() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); today date:=rm.today(); items jsonb;
begin
  with book as (select a.customer_id,rm.display_name(p.full_name) client from rm.assignments a join banking.customer_profiles p using(customer_id)
      where a.advisor_id=advisor and a.ended_on is null and a.started_on<=today),
  meet as (select b.customer_id,min(ap.starts_at) starts_at from book b join rm.appointments ap on ap.customer_id=b.customer_id and ap.advisor_id=advisor
      and ap.status='confirmed' and ap.starts_at>clock_timestamp() group by b.customer_id),
  raw as (
    select 'ticket:'||t.reference item_key,b.customer_id,b.client,'rm_ticket' kind,
      c.label||' ('||replace(t.status,'_',' ')||case when t.status in ('submitted','in_review') and t.target_at<clock_timestamp() then ', past target' else '' end||')' title,
      t.target_at::date due_on,'rm' owner,'ticket:'||t.reference source_id,t.summary detail
      from book b join support.tickets t on t.customer_id=b.customer_id join support.request_categories c using(category)
      where rm.rm_owned(t.owner_team) and t.status in ('submitted','in_review','waiting_on_client')
    union all
    select 'specialist:'||t.reference,b.customer_id,b.client,'specialist_ticket',c.label||' with the '||t.owner_team,t.target_at::date,'specialist','ticket:'||t.reference,
      support.status_note(t.status,t.owner_team)
      from book b join support.tickets t on t.customer_id=b.customer_id join support.request_categories c using(category)
      where not rm.rm_owned(t.owner_team) and t.status in ('submitted','in_review','waiting_on_client')
        and (t.urgency in ('urgent','high') or t.target_at<clock_timestamp())
    union all
    select 'review:'||c.case_id||':'||c.due_date,b.customer_id,b.client,'review_deadline',c.title||' due',c.due_date,'rm','compliance',
      (select count(*) from compliance.requested_items i where i.case_id=c.case_id and i.submission_status in ('not_submitted','rejected','unknown'))||' item(s) still to send'
      from book b join compliance.cases c on c.customer_id=b.customer_id where c.status not in ('completed','closed') and c.due_date<=today+45
    union all
    select 'milestone:'||m.milestone_id||':'||m.due_on,b.customer_id,b.client,'milestone',m.title,m.due_on,'rm','milestones',m.details
      from book b join rm.client_milestones m on m.customer_id=b.customer_id where m.due_on between today and today+60
    union all
    select 'task:'||k.task_id||':'||coalesce(k.due_on::text,'none'),b.customer_id,b.client,'task',k.title,k.due_on,'rm','task:'||k.task_id,k.details
      from book b join rm.tasks k on k.customer_id=b.customer_id and k.advisor_id=advisor where k.status='open' and (k.due_on is null or k.due_on<=today+14)
    union all
    select 'meeting:'||ap.appointment_id,b.customer_id,b.client,'meeting',coalesce(ap.purpose,'Meeting (purpose not recorded)'),
      (ap.starts_at at time zone 'Asia/Singapore')::date,'rm','appointments',rm.sgt(ap.starts_at)||' · '||ap.format
      from book b join rm.appointments ap on ap.customer_id=b.customer_id and ap.advisor_id=advisor
      where ap.status='confirmed' and ap.starts_at>clock_timestamp() and ap.starts_at<clock_timestamp()+interval '14 days'
    union all
    select 'commitment:'||n.note_id||':'||ord,b.customer_id,b.client,'commitment',x.commitment,null::date,'rm','note:'||n.note_id,
      'Recorded on '||rm.day(n.met_on)||' by '||rm.display_name(a.full_name)||'; completion is not tracked'
      from book b cross join lateral (select * from rm.visible_notes(advisor,b.customer_id) limit 2) n
      join rm.advisors a on a.advisor_id=n.advisor_id cross join lateral unnest(n.commitments) with ordinality x(commitment,ord)
      where n.met_on>=today-400)
  select coalesce(jsonb_agg(jsonb_build_object('item_key',r.item_key,'customer_id',r.customer_id,'client',r.client,'kind',r.kind,'title',r.title,
      'due_on',r.due_on,'due_text',rm.day(r.due_on),'overdue',r.due_on<today,'owner',r.owner,'source_id',r.source_id,'detail',r.detail,
      'confirmed_meeting',(select rm.sgt(m.starts_at) from meet m where m.customer_id=r.customer_id))
      order by r.due_on nulls last,r.client),'[]') into items
  from raw r where not exists(select 1 from rm.dismissed_followups d where d.advisor_id=advisor and d.item_key=r.item_key);
  return rm.result(null,'rm.followups','rm.assignments',items);
end $$;

-- RM16: sources for a handover brief, only for a client whose reassignment to this advisor is recorded.
create function public.rm_handover_sources(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); data jsonb; shared jsonb; notes jsonb; told jsonb; ids jsonb; call_id uuid:=gen_random_uuid();
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.handover_brief'); end if;
  if not exists(select 1 from rm.assignments where customer_id=p_customer_id and ended_on is not null and advisor_id<>advisor) then
    return rm.result(p_customer_id,'rm.handover_brief','rm.assignments',jsonb_build_object('eligible',false));
  end if;
  shared:=(public.rm_shared_summary(p_customer_id))->'data';
  notes:=(select coalesce(jsonb_agg(rm.note_json(n,advisor) order by n.met_on desc),'[]') from rm.visible_notes(advisor,p_customer_id) n);
  told:=(select coalesce(jsonb_agg(y.x order by y.at),'[]') from (select u.at,u.x from (
      select e.created_at at,jsonb_build_object('at_sgt',rm.sgt(e.created_at),'by','bank','reference',t.reference,'text',e.public_note) x
        from support.ticket_events e join support.tickets t using(ticket_id) where t.customer_id=p_customer_id and not e.internal and e.public_note is not null
          and e.public_note !~ '^(Received\.|The .* is reviewing it\.)'
      union all
      select m.created_at,jsonb_build_object('at_sgt',rm.sgt(m.created_at),'by','assistant','reference',t.reference,'text',left(m.redacted_text,600))
        from support.tickets t join conversation.turns c on c.case_id=t.case_id and c.customer_id=t.customer_id and c.state='completed'
        join conversation.messages m on m.message_id=c.answer_message_id where t.customer_id=p_customer_id
      union all
      select cm.approved_at,jsonb_build_object('at_sgt',rm.sgt(cm.approved_at),'by','relationship manager','reference',t.reference,'text',cm.body)
        from rm.client_messages cm left join support.tickets t on t.ticket_id=cm.ticket_id where cm.customer_id=p_customer_id and cm.status='sent'
      ) u order by u.at desc limit 30) y);
  data:=rm.client_data(advisor,p_customer_id)||jsonb_build_object('eligible',true,'notes',notes,
    'withheld_private_notes',(select count(*) from rm.meeting_notes where customer_id=p_customer_id and visibility='author_only' and advisor_id<>advisor),
    'shared',shared,'told',told,
    'free_slots',(select coalesce(jsonb_agg(rm.sgt(s.starts_at) order by s.starts_at),'[]') from rm.free_slots(advisor,rm.today(),rm.today()+13,8) s));
  ids:=(select coalesce(jsonb_agg(z.x),'[]') from ((select n->>'note_id' x from jsonb_array_elements(notes) n limit 10)
        union all (select t->>'reference' from jsonb_array_elements(data->'tickets') t limit 10)) z);
  -- RM16: each brief generation is audited with the RM (actor), the client and the sources used (ids and counts only).
  perform audit.record('data','data.read','success','rm.handover_brief',call_id::text,jsonb_build_object('tool','rm.handover_brief',
    'notes',jsonb_array_length(notes),'tickets',jsonb_array_length(data->'tickets'),'shared_notes',jsonb_array_length(coalesce(shared->'shared','[]')),
    'excluded_consent',coalesce((shared->>'excluded_revoked')::int,0)+coalesce((shared->>'excluded_expired')::int,0),'told',jsonb_array_length(told),
    'source_ids',ids),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  return jsonb_build_object('denied',false,'customer_id',p_customer_id,'call_id',call_id,'as_of',clock_timestamp(),'data',data);
end $$;

-- 6. RM-approved writes (the API calls these only after the RM approved the exact content) -------------------------------
create function rm.require_text(p text,p_max int,p_what text) returns text language plpgsql immutable set search_path='' as $$
begin
  if p is null or length(btrim(p))=0 or length(p)>p_max then
    raise exception '% must be 1-% characters',p_what,p_max using errcode='22023';
  end if;
  perform conversation.require_redacted(p);
  return btrim(p);
end $$;

create function rm.clean_list(p text[],p_what text) returns text[] language plpgsql immutable set search_path='' as $$
declare item text; result text[]:='{}';
begin
  if cardinality(coalesce(p,'{}'))>10 then raise exception 'at most 10 % per interaction',p_what using errcode='22023'; end if;
  foreach item in array coalesce(p,'{}') loop
    if nullif(btrim(item),'') is not null then result:=result||rm.require_text(item,300,p_what); end if;
  end loop;
  return result;
end $$;

-- RM5: an RM-approved interaction summary becomes a meeting note that keeps the RM's source notes and revision trail.
create function public.rm_save_interaction(p_request_key uuid,p_customer_id bigint,p_met_on date,p_format text,p_title text,p_summary text,
  p_commitments text[],p_decisions text[],p_client_requests text[],p_source_notes text,p_structured jsonb,p_revisions jsonb)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); note rm.meeting_notes;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.save_interaction'); end if;
  if p_request_key is null then raise exception 'a request key is required' using errcode='22023'; end if;
  select * into note from rm.meeting_notes where request_key=p_request_key;
  if found then
    if note.customer_id<>p_customer_id or note.advisor_id<>advisor then raise exception 'request key already used' using errcode='23505'; end if;
    return jsonb_build_object('denied',false,'created',false,'note_id',note.note_id);
  end if;
  if p_met_on is null or p_met_on>rm.today() or p_met_on<rm.today()-365 then
    raise exception 'the interaction date must be within the last year and not in the future' using errcode='22023';
  end if;
  if p_format not in ('video','phone','in_person') then raise exception 'format must be video, phone or in_person' using errcode='22023'; end if;
  if jsonb_typeof(coalesce(p_structured,'{}'))<>'object' or jsonb_typeof(coalesce(p_revisions,'[]'))<>'array' or jsonb_array_length(coalesce(p_revisions,'[]'))>50 then
    raise exception 'structured summary must be an object and revisions an array of up to 50' using errcode='22023';
  end if;
  perform conversation.require_redacted(coalesce(p_structured,'{}')::text);
  perform conversation.require_redacted(coalesce(p_revisions,'[]')::text);
  insert into rm.meeting_notes(customer_id,advisor_id,met_on,format,summary,commitments,visibility,source,title,decisions,client_requests,
      source_notes,structured,revisions,approved_by,approved_at,request_key)
    values(p_customer_id,advisor,p_met_on,p_format,rm.require_text(p_summary,2000,'summary'),rm.clean_list(p_commitments,'commitments'),'book','rm_documented',
      case when nullif(btrim(coalesce(p_title,'')),'') is null then null else rm.require_text(p_title,160,'title') end,
      rm.clean_list(p_decisions,'decisions'),rm.clean_list(p_client_requests,'client requests'),
      case when p_source_notes is null then null else rm.require_text(p_source_notes,8000,'source notes') end,
      p_structured,coalesce(p_revisions,'[]'),auth.uid(),clock_timestamp(),p_request_key) returning * into note;
  perform audit.record('action','action.rm_interaction_approved','success','rm.meeting_notes',note.note_id::text,
    jsonb_build_object('commitments',cardinality(note.commitments),'decisions',cardinality(note.decisions),'requests',cardinality(note.client_requests),
      'revisions',jsonb_array_length(note.revisions)),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  return jsonb_build_object('denied',false,'created',true,'note_id',note.note_id,'met_on',note.met_on);
end $$;

-- RM10: RM-confirmed follow-up tasks linked to the interaction; an open task with the same title is reported, not duplicated.
create function public.rm_create_tasks(p_customer_id bigint,p_note_id uuid,p_tasks jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); item jsonb; existing rm.tasks; created jsonb:='[]'; duplicates jsonb:='[]'; task rm.tasks; due date; key uuid;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.create_tasks'); end if;
  if jsonb_typeof(p_tasks) is distinct from 'array' or jsonb_array_length(p_tasks) not between 1 and 10 then
    raise exception 'confirm between one and ten tasks' using errcode='22023';
  end if;
  if p_note_id is not null and not exists(select 1 from rm.visible_notes(advisor,p_customer_id) n where n.note_id=p_note_id) then
    raise exception 'the interaction is not one of this client''s notes you can see' using errcode='42501';
  end if;
  for item in select * from jsonb_array_elements(p_tasks) loop
    key:=(item->>'request_key')::uuid;
    if key is null then raise exception 'each task needs a request key' using errcode='22023'; end if;
    select * into task from rm.tasks where request_key=key;
    if found then created:=created||jsonb_build_object('task_id',task.task_id,'title',task.title,'replayed',true); continue; end if;
    due:=nullif(item->>'due_on','')::date;
    if due is not null and (due<rm.today() or due>rm.today()+366) then raise exception 'task due dates must be within the next year' using errcode='22023'; end if;
    select * into existing from rm.tasks where customer_id=p_customer_id and advisor_id=advisor and status='open'
      and lower(btrim(title))=lower(btrim(item->>'title')) limit 1;
    if found then duplicates:=duplicates||jsonb_build_object('title',existing.title,'existing_task_id',existing.task_id); continue; end if;
    insert into rm.tasks(advisor_id,customer_id,note_id,title,details,owner_label,due_on,request_key)
      values(advisor,p_customer_id,p_note_id,rm.require_text(item->>'title',200,'task title'),
        case when nullif(btrim(coalesce(item->>'details','')),'') is null then null else rm.require_text(item->>'details',500,'task details') end,
        case when nullif(btrim(coalesce(item->>'owner','')),'') is null then null else rm.require_text(item->>'owner',80,'task owner') end,due,key)
      returning * into task;
    created:=created||jsonb_build_object('task_id',task.task_id,'title',task.title,'replayed',false);
  end loop;
  return jsonb_build_object('denied',false,'created',created,'duplicates',duplicates);
end $$;

create function public.rm_update_task(p_task_id uuid,p_status text) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); task rm.tasks;
begin
  if p_status not in ('done','cancelled','open') then raise exception 'status must be done, cancelled or open' using errcode='22023'; end if;
  select * into task from rm.tasks where task_id=p_task_id and advisor_id=advisor for update;
  if not found then raise exception 'task not found' using errcode='P0002'; end if;
  if not rm.in_book(advisor,task.customer_id) then return rm.deny(task.customer_id,'rm.update_task'); end if;
  update rm.tasks set status=p_status,completed_at=case when p_status='open' then null else clock_timestamp() end where task_id=p_task_id;
  return jsonb_build_object('denied',false,'task_id',p_task_id,'status',p_status);
end $$;

-- RM6: validate a prepared service request without creating it (missing fields, format errors, open duplicates).
create function public.rm_check_service_request(p_customer_id bigint,p_category text,p_summary text,p_details jsonb,p_urgency text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); cat support.request_categories; missing jsonb:='[]'; field jsonb; problem text; dup support.tickets;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.check_service_request'); end if;
  select * into cat from support.request_categories where category=p_category and customer_raisable;
  if not found then return jsonb_build_object('denied',false,'valid',false,'missing','[]','problem','This request type cannot be raised for a client.'); end if;
  for field in select * from jsonb_array_elements(cat.fields) loop
    if (field->>'required')::boolean and nullif(btrim(coalesce(p_details->>(field->>'key'),'')),'') is null then
      missing:=missing||jsonb_build_object('key',field->>'key','label',field->>'label');
    end if;
  end loop;
  if nullif(btrim(coalesce(p_summary,'')),'') is null then missing:=missing||jsonb_build_object('key','summary','label','Short summary'); end if;
  begin
    perform actions.ticket_target(jsonb_build_object('category',p_category,'summary',coalesce(nullif(btrim(p_summary),''),'-'),
      'urgency',coalesce(p_urgency,'normal'),'details',coalesce(p_details,'{}')),p_customer_id);
  exception when others then problem:=sqlerrm;
  end;
  select * into dup from support.tickets where customer_id=p_customer_id and category=p_category and status in ('submitted','in_review','waiting_on_client')
    order by created_at desc limit 1;
  return jsonb_build_object('denied',false,'valid',jsonb_array_length(missing)=0 and problem is null and dup.ticket_id is null,
    'missing',missing,'problem',problem,'label',cat.label,'owner_team',cat.owner_team,'decided_by',cat.decided_by,'target_hours',cat.target_hours,
    'fields',cat.fields,'documents',to_jsonb(cat.documents),
    'duplicate',case when dup.ticket_id is null then null else jsonb_build_object('reference',dup.reference,'status',dup.status,'summary',dup.summary) end);
end $$;

-- RM6: create one confirmed service request for the client, after the RM confirms verification and authorisation.
-- The reference is returned only once the ticket exists; a replay with the same key returns the same reference.
create function public.rm_create_service_request(p_request_key uuid,p_customer_id bigint,p_category text,p_summary text,p_details jsonb,
  p_urgency text,p_client_verified boolean,p_note_id uuid default null)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); target jsonb; t support.tickets; dup support.tickets;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.create_service_request'); end if;
  if p_request_key is null then raise exception 'a request key is required' using errcode='22023'; end if;
  select * into t from support.tickets where rm_request_key=p_request_key;
  if found then
    if t.customer_id<>p_customer_id then raise exception 'request key already used' using errcode='23505'; end if;
    return jsonb_build_object('denied',false,'created',true,'replayed',true,'reference',t.reference,'owner_team',t.owner_team,'target_sgt',rm.sgt(t.target_at));
  end if;
  if p_client_verified is not true then
    raise exception 'confirm the client''s identity check and authorisation before submitting' using errcode='22023';
  end if;
  if p_note_id is not null and not exists(select 1 from rm.visible_notes(advisor,p_customer_id) n where n.note_id=p_note_id) then
    raise exception 'the interaction is not one of this client''s notes you can see' using errcode='42501';
  end if;
  target:=actions.ticket_target(jsonb_build_object('category',p_category,'summary',p_summary,'urgency',p_urgency,'details',coalesce(p_details,'{}')),p_customer_id);
  select * into dup from support.tickets where customer_id=p_customer_id and category=p_category and status in ('submitted','in_review','waiting_on_client')
    order by created_at desc limit 1;
  if found then
    return jsonb_build_object('denied',false,'created',false,'duplicate',jsonb_build_object('reference',dup.reference,'status',dup.status));
  end if;
  t:=support.open_ticket(p_customer_id,null,target->>'category',target->>'summary',target->'details',target->>'urgency','rm');
  update support.tickets set rm_request_key=p_request_key,rm_note_id=p_note_id where ticket_id=t.ticket_id;
  insert into notify.inbox(customer_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
    select p_customer_id,tp.template_key,tp.version,'en','P3',notify.render(tp.title,jsonb_build_object('reference',t.reference)),
      notify.render(tp.body,jsonb_build_object('reference',t.reference,'owner_team',lower(t.owner_team))),true,'rm_request:'||t.ticket_id
    from notify.templates tp where tp.template_key='rm.request_raised' and tp.channel='in_app' and tp.locale='en' order by tp.version desc limit 1;
  perform audit.record('action','action.rm_request_raised','success','support.tickets',t.ticket_id::text,
    jsonb_build_object('category',t.category,'reference',t.reference,'verified',true,'from_note',p_note_id is not null),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  return jsonb_build_object('denied',false,'created',true,'replayed',false,'reference',t.reference,'owner_team',t.owner_team,'target_sgt',rm.sgt(t.target_at));
end $$;

-- RM9: send an RM-approved message. On an RM-owned ticket it joins the public timeline the client sees in My requests
-- (optionally with a status the RM chose); without a ticket it goes to the client's Inbox. Approval is recorded.
create function public.rm_send_client_update(p_message_id uuid,p_customer_id bigint,p_reference text,p_body text,p_status text,
  p_draft_revision int,p_edited boolean) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); t support.tickets; msg text; sent rm.client_messages; changed boolean:=false;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.send_client_update'); end if;
  if p_message_id is null then raise exception 'a message id is required' using errcode='22023'; end if;
  select * into sent from rm.client_messages where message_id=p_message_id;
  if found then
    if sent.customer_id<>p_customer_id or sent.status<>'sent' then raise exception 'message id already used' using errcode='23505'; end if;
    return jsonb_build_object('denied',false,'sent',true,'replayed',true,'message_id',sent.message_id,'at',rm.sgt(sent.approved_at));
  end if;
  msg:=rm.require_text(p_body,500,'message');
  if p_status is not null and p_status not in ('in_review','waiting_on_client','resolved') then
    raise exception 'status must be in_review, waiting_on_client or resolved' using errcode='22023';
  end if;
  if p_reference is not null then
    select * into t from support.tickets where reference=p_reference and customer_id=p_customer_id for update;
    -- Restricted specialist cases (fraud, login) stay with their team; other specialist cases take a message but no status change.
    if not found or rm.restricted_category(t.category) and not rm.rm_owned(t.owner_team) then
      raise exception 'updates can only be added to this client''s requests that are not restricted to a specialist team' using errcode='42501';
    end if;
    if p_status is not null and not rm.rm_owned(t.owner_team) then
      raise exception 'only the owner team can change the status of this request; send the message without a status' using errcode='42501';
    end if;
    if t.status='closed' then raise exception 'this request is closed' using errcode='P1001'; end if;
    if p_status is not null and p_status<>t.status then
      perform support.move_ticket(t.ticket_id,p_status,msg); changed:=true;
    else
      insert into support.ticket_events(ticket_id,customer_id,status,public_note) values(t.ticket_id,p_customer_id,t.status,msg);
      update support.tickets set updated_at=clock_timestamp() where ticket_id=t.ticket_id;
    end if;
    insert into notify.inbox(customer_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
      select p_customer_id,tp.template_key,tp.version,'en','P3',tp.title,notify.render(tp.body,jsonb_build_object('reference',t.reference)),true,'rm_message:'||p_message_id
      from notify.templates tp where tp.template_key='rm.client_update' and tp.channel='in_app' and tp.locale='en' order by tp.version desc limit 1;
  else
    if p_status is not null then raise exception 'a status needs a request reference' using errcode='22023'; end if;
    insert into notify.inbox(customer_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
      select p_customer_id,tp.template_key,tp.version,'en','P3',tp.title,msg,true,'rm_message:'||p_message_id
      from notify.templates tp where tp.template_key='rm.client_message' and tp.channel='in_app' and tp.locale='en' order by tp.version desc limit 1;
  end if;
  insert into rm.client_messages(message_id,customer_id,advisor_id,ticket_id,body,status,draft_revision,edited,status_change,approved_by)
    values(p_message_id,p_customer_id,advisor,t.ticket_id,msg,'sent',greatest(1,least(coalesce(p_draft_revision,1),50)),coalesce(p_edited,false),
      case when changed then p_status end,auth.uid()) returning * into sent;
  perform audit.record('action','action.rm_client_update_sent','success','rm.client_messages',p_message_id::text,
    jsonb_build_object('reference',t.reference,'edited',coalesce(p_edited,false),'revision',sent.draft_revision,'status_change',sent.status_change),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  return jsonb_build_object('denied',false,'sent',true,'replayed',false,'message_id',p_message_id,'reference',t.reference,
    'status',case when changed then p_status else t.status end,'at',rm.sgt(sent.approved_at));
end $$;

-- A send that failed in the API is recorded as failed (no text) so the RM's approval and the outcome are both on file.
create function public.rm_record_failed_message(p_message_id uuid,p_customer_id bigint,p_reference text,p_failure_code text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); ticket uuid;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.send_client_update'); end if;
  if exists(select 1 from rm.client_messages where message_id=p_message_id) then return jsonb_build_object('denied',false,'recorded',false); end if;
  select ticket_id into ticket from support.tickets where reference=p_reference and customer_id=p_customer_id;
  insert into rm.client_messages(message_id,customer_id,advisor_id,ticket_id,status,failure_code,approved_by)
    values(p_message_id,p_customer_id,advisor,ticket,'failed',coalesce(substring(lower(p_failure_code) from '^[a-z_]{1,40}'),'unknown'),auth.uid());
  perform audit.record('action','action.rm_client_update_sent','failed','rm.client_messages',p_message_id::text,
    jsonb_build_object('reference',p_reference,'failure',coalesce(substring(lower(p_failure_code) from '^[a-z_]{1,40}'),'unknown')),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  return jsonb_build_object('denied',false,'recorded',true);
end $$;

-- RM12: weekly hours and blocked time drive the free times clients book in My requests → Meetings (CR17).
create function public.rm_set_working_hours(p_weekday int,p_starts time,p_ends time) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); clash int;
begin
  if p_weekday not between 1 and 7 then raise exception 'weekday must be 1 (Monday) to 7 (Sunday)' using errcode='22023'; end if;
  if (p_starts is null)<>(p_ends is null) or p_starts is not null and (p_ends<=p_starts or p_starts<'07:00' or p_ends>'21:00'
      or extract(minute from p_starts)::int%30<>0 or extract(minute from p_ends)::int%30<>0) then
    raise exception 'hours must be on the half hour between 07:00 and 21:00, ending after they start' using errcode='22023';
  end if;
  select count(*) into clash from rm.appointments ap where ap.advisor_id=advisor and ap.status='confirmed' and ap.starts_at>clock_timestamp()
    and extract(isodow from ap.starts_at at time zone 'Asia/Singapore')=p_weekday
    and (p_starts is null or (ap.starts_at at time zone 'Asia/Singapore')::time<p_starts or (ap.ends_at at time zone 'Asia/Singapore')::time>p_ends);
  if clash>0 then raise exception '% confirmed appointment(s) fall outside these hours; reschedule them with the client first',clash using errcode='P1001'; end if;
  if p_starts is null then delete from rm.working_hours where advisor_id=advisor and weekday=p_weekday;
  else insert into rm.working_hours(advisor_id,weekday,starts_at,ends_at) values(advisor,p_weekday,p_starts,p_ends)
    on conflict(advisor_id,weekday) do update set starts_at=excluded.starts_at,ends_at=excluded.ends_at; end if;
  perform audit.record('action','action.rm_availability_changed','success','rm.working_hours',advisor::text,
    jsonb_build_object('change','weekly_hours','weekday',p_weekday,'day_off',p_starts is null),p_actor_type=>'staff',p_actor_id=>auth.uid()::text);
  return jsonb_build_object('denied',false,'weekday',p_weekday,'starts',p_starts,'ends',p_ends);
end $$;

create function public.rm_add_block(p_starts timestamptz,p_ends timestamptz,p_reason text default null) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); id uuid; today date:=rm.today(); clash int;
begin
  if p_starts is null or p_ends is null or p_ends<=p_starts or p_ends-p_starts>interval '14 hours' or p_starts<clock_timestamp()-interval '30 minutes'
      or (p_starts at time zone 'Asia/Singapore')::date>today+13
      or extract(minute from p_starts at time zone 'Asia/Singapore')::int%30<>0 or extract(minute from p_ends at time zone 'Asia/Singapore')::int%30<>0 then
    raise exception 'block whole half hours within the next 14 days, up to 14 hours at a time' using errcode='22023';
  end if;
  select count(*) into clash from rm.appointments ap where ap.advisor_id=advisor
    and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())
    and tstzrange(ap.starts_at,ap.ends_at,'[)') && tstzrange(p_starts,p_ends,'[)');
  if clash>0 then raise exception 'a client has booked or is holding this time; reschedule with the client first' using errcode='P1001'; end if;
  insert into rm.advisor_blocks(advisor_id,starts_at,ends_at,reason)
    values(advisor,p_starts,p_ends,case when nullif(btrim(coalesce(p_reason,'')),'') is null then null else rm.require_text(p_reason,80,'reason') end)
    returning block_id into id;
  perform audit.record('action','action.rm_availability_changed','success','rm.advisor_blocks',id::text,
    jsonb_build_object('change','block_added'),p_actor_type=>'staff',p_actor_id=>auth.uid()::text);
  return jsonb_build_object('denied',false,'block_id',id);
end $$;

create function public.rm_remove_block(p_block_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  delete from rm.advisor_blocks where block_id=p_block_id and advisor_id=advisor and ends_at>clock_timestamp();
  if not found then raise exception 'block not found' using errcode='P0002'; end if;
  perform audit.record('action','action.rm_availability_changed','success','rm.advisor_blocks',p_block_id::text,
    jsonb_build_object('change','block_removed'),p_actor_type=>'staff',p_actor_id=>auth.uid()::text);
  return jsonb_build_object('denied',false,'block_id',p_block_id);
end $$;

create function public.rm_dismiss_followup(p_item_key text) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();
begin
  insert into rm.dismissed_followups(advisor_id,item_key) values(advisor,p_item_key) on conflict do nothing;
  return jsonb_build_object('denied',false,'item_key',p_item_key);
end $$;

-- 7. Grants: only signed-in users may call these, and every one of them refuses anyone who is not a linked RM. --------
do $$
declare f text;
begin
  foreach f in array array['rm_profile()','rm_book(text)','rm_match_clients(text)','rm_client_profile(bigint)','rm_meeting_notes(bigint)',
    'rm_shared_summary(bigint)','rm_tickets()','rm_ticket(text)','rm_review_status()','rm_appointments(date,date)','rm_availability()',
    'rm_tasks(bigint)','rm_followups()','rm_handover_sources(bigint)',
    'rm_save_interaction(uuid,bigint,date,text,text,text,text[],text[],text[],text,jsonb,jsonb)','rm_create_tasks(bigint,uuid,jsonb)',
    'rm_update_task(uuid,text)','rm_check_service_request(bigint,text,text,jsonb,text)',
    'rm_create_service_request(uuid,bigint,text,text,jsonb,text,boolean,uuid)','rm_send_client_update(uuid,bigint,text,text,text,int,boolean)',
    'rm_record_failed_message(uuid,bigint,text,text)','rm_set_working_hours(int,time,time)','rm_add_block(timestamptz,timestamptz,text)',
    'rm_remove_block(uuid)','rm_dismiss_followup(text)'] loop
    execute format('revoke all on function public.%s from public,anon',f);
    execute format('grant execute on function public.%s to authenticated',f);
  end loop;
end $$;
revoke all on all functions in schema rm from public,anon,authenticated;
$source_0089$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0089';
  if found then
    if previous.name is distinct from 'rm_workspace' or previous.statements is distinct from array[script] then
      raise exception 'migration 0089 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0089 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0089','rm_workspace',array[script]);
  end if;
end $apply_0089$;

do $apply_0090$
declare previous supabase_migrations.schema_migrations; script text:=$source_0090$-- Released source SHA256: 2445ac7c953ecf72ce251a9fd604a6914663a021f74f8fa39021ae343464e324
-- Persona 2 demo data for the RM workspace (RM1-RM12, RM16). Synthetic data only; no real people or accounts.
-- * Sophie's own meeting notes for Michelle, Grace and Hafiz (one deliberately unclear), client-stated preferences,
--   upcoming maturities and reviews, two open RM tasks (one overdue) and confirmed appointments with a purpose.
-- * Tan Mei Ling (1007), inherited from Rachel on 26 Oct 2026 so the handover brief (RM16) also works on the hosted
--   project (David is local only): Rachel's notes (one private, so it is withheld from Sophie), a chat answer that was
--   handed to the RM, an open request raised with Rachel, a handover note under active consent, and two older notes
--   whose consent expired or was revoked (both must be excluded).
-- Appointment and follow-up dates are relative to the day this migration is applied (the next weekday at a set time),
-- so the demo always has upcoming meetings. David's rows are added only where he exists (local test database).

do $seed$
declare
  today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date;
  sophie uuid:='00000000-0000-4000-8000-100200000002';
  rachel uuid:='00000000-0000-4000-8000-100300000001';
  priya uuid:='00000000-0000-4000-8000-100100000002';
  has_david boolean:=exists(select 1 from banking.customer_profiles where customer_id=1003);
  t support.tickets; sess uuid; kase uuid; q uuid; a uuid; h uuid;
begin
  if exists(select 1 from rm.client_milestones) then return; end if;  -- safe to re-run

  -- Mei Ling: profile, banking, and the recorded change from Rachel to Sophie.
  insert into banking.customer_profiles(customer_id,demo_email,full_name,income_range,employment_status,preferred_language) values
    (1007,'demo.meiling@example.com','Tan Mei Ling (DEMO)','SGD 20,000 and above / month','employed','en') on conflict (customer_id) do nothing;
  insert into banking.bank_accounts(account_id,customer_id,account_type,masked_account_number,balance,currency,opened_on) values
    (10071,1007,'savings','****8820',212400.00,'SGD','2016-05-09') on conflict (account_id) do nothing;
  insert into banking.credit_cards(card_id,customer_id,card_name,network,last4,credit_limit,outstanding_balance,minimum_payment_due,payment_due_date,status) values
    (10071,1007,'Signature Card','mastercard','3318',25000.00,980.50,50.00,today+20,'active') on conflict (card_id) do nothing;
  insert into rm.assignments(assignment_id,customer_id,advisor_id,started_on,ended_on,transition_reason_code) values
    ('00000000-0000-4000-8000-100700000011',1007,rachel,'2023-04-03','2026-10-25',null),
    ('00000000-0000-4000-8000-100700000012',1007,sophie,'2026-10-26',null,'team_restructure') on conflict (assignment_id) do nothing;

  -- Meeting notes. Sophie's own (book-visible), Rachel's for Mei Ling, and private notes that only their author may read.
  insert into rm.meeting_notes(note_id,customer_id,advisor_id,met_on,format,title,summary,commitments,decisions,client_requests,visibility) values
    ('00000000-0000-4000-8000-100200000061',1002,sophie,'2026-04-15','in_person','Annual review',
     'Annual review. Michelle''s consulting income is steady and she keeps a large SGD balance in her multi-currency account for business cash flow. She asked about placing part of it in a 6-month fixed deposit when her current fixed deposit matures in November, and wants the options compared first. She prefers video meetings in the afternoon.',
     array['Send Michelle fixed deposit options before her November maturity'],array['Keep the business float in the multi-currency account'],
     array['Compare 6-month fixed deposit options'],'book'),
    ('00000000-0000-4000-8000-100200000062',1002,sophie,'2026-09-24','video','Periodic review preparation',
     'Michelle explained that the SGD 120,000 incoming transfer on 22 Sep 2026 came from the sale of a property abroad. She will upload the sale agreement for her periodic review. She mentioned she may ask for a review of her Signature Card limit later in the year; no request was made.',
     array['Remind Michelle to upload the sale agreement before her review is due on 30 Nov 2026'],'{}','{}','book'),
    ('00000000-0000-4000-8000-100200000063',1002,sophie,'2026-10-20','phone','Short call',
     'Short call. Michelle mentioned "the thing for her mother" but it was not clear whether she means a new account or a transfer. Need to follow up.',
     array['Clarify what Michelle needs for her mother'],'{}','{}','book'),
    ('00000000-0000-4000-8000-100400000061',1004,sophie,'2026-08-12','video','Quarterly check-in',
     'Quarterly check-in. Grace is renovating her flat and pays her contractor by FAST transfer in stages; the next payment is expected in November. She wants to keep at least SGD 30,000 in savings as an emergency fund. She asked for general information about renovation loans.',
     array['Share general information about renovation loans'],array['Keep at least SGD 30,000 in savings'],array['General information on renovation loans'],'book'),
    ('00000000-0000-4000-8000-100500000061',1005,sophie,'2026-06-03','in_person','Business banking review',
     'Hafiz runs a design studio and uses his current account for business payments. He felt the monthly account fees were high and asked whether they could be reviewed. He is planning a family trip to Japan in December. He prefers to meet in person at the Private Clients Centre.',
     array['Ask the product team whether Hafiz''s current account fees can be reviewed'],'{}',array['Review of current account fees'],'book'),
    ('00000000-0000-4000-8000-100700000061',1007,rachel,'2025-07-08','in_person','Onboarding review',
     'Onboarding review. Mei Ling is a doctor with irregular hours. She prefers an email summary after each meeting and cannot meet on Saturdays. She has a 12-month fixed deposit maturing in December 2026 and wants to discuss renewal options before then.',
     array['Discuss fixed deposit renewal options before the December 2026 maturity'],'{}','{}','book'),
    ('00000000-0000-4000-8000-100700000062',1007,rachel,'2026-06-19','phone','Joint account request',
     'Mei Ling asked to add her husband as a joint holder on her savings account. Rachel explained the forms and the in-branch identity check. Mei Ling has not confirmed a date for the branch visit.',
     array['Send the joint account forms','Confirm a branch appointment for the identity check'],'{}',array['Add her husband as a joint account holder'],'book'),
    ('00000000-0000-4000-8000-100700000063',1007,rachel,'2026-09-30','phone','Private reminder',
     'Private working note by the previous relationship manager.','{}','{}','{}','author_only')
  on conflict (note_id) do nothing;
  if has_david then
    insert into rm.meeting_notes(note_id,customer_id,advisor_id,met_on,format,title,summary,visibility) values
      ('00000000-0000-4000-8000-100300000054',1003,rachel,'2026-10-10','phone','Private reminder','Private working note by the previous relationship manager.','author_only')
    on conflict (note_id) do nothing;
    update rm.meeting_notes set title=case met_on when '2025-03-12' then 'Annual review' when '2025-11-20' then 'Overseas transfer set-up' else 'Mid-year check-in' end
      where customer_id=1003 and title is null and advisor_id=rachel;
  end if;

  insert into rm.client_preferences(customer_id,preference,recorded_on,recorded_by,source_note_id) values
    (1002,'Prefers video meetings in the afternoon','2026-04-15',sophie,'00000000-0000-4000-8000-100200000061'),
    (1005,'Prefers to meet in person at the Private Clients Centre','2026-06-03',sophie,'00000000-0000-4000-8000-100500000061'),
    (1007,'Prefers an email summary after each meeting','2025-07-08',rachel,'00000000-0000-4000-8000-100700000061'),
    (1007,'Cannot meet on Saturdays','2025-07-08',rachel,'00000000-0000-4000-8000-100700000061');
  if has_david then
    insert into rm.client_preferences(customer_id,preference,recorded_on,recorded_by,source_note_id) values
      (1003,'Prefers phone calls before 10am','2025-03-12',rachel,'00000000-0000-4000-8000-100300000051');
  end if;

  insert into rm.client_milestones(customer_id,kind,title,due_on,amount,currency,details) values
    (1002,'fixed_deposit_maturity','Fixed deposit ****4410 matures',today+10,150000.00,'SGD','6-month SGD fixed deposit'),
    (1004,'portfolio_review','Quarterly portfolio check-in due',today+6,null,null,'Agreed quarterly check-in'),
    (1005,'account_review','Annual business account review',today+20,null,null,'Yearly review of the business current account'),
    (1007,'fixed_deposit_maturity','Fixed deposit ****9051 matures',greatest(date '2026-12-15',today+14),80000.00,'SGD','12-month SGD fixed deposit'),
    (1006,'insurance_renewal','Home insurance renewal',today+5,null,null,'Priya''s client: never shown to Sophie');
  if has_david then
    insert into rm.client_milestones(customer_id,kind,title,due_on,details) values (1003,'portfolio_review','Annual review due',today+25,'Yearly review');
  end if;

  insert into rm.tasks(advisor_id,customer_id,note_id,title,owner_label,due_on,created_at) values
    (sophie,1002,'00000000-0000-4000-8000-100200000061','Send Michelle fixed deposit options','Sophie Lim',today+5,clock_timestamp()-interval '20 days'),
    (sophie,1005,'00000000-0000-4000-8000-100500000061','Ask the product team whether Hafiz''s current account fees can be reviewed','Sophie Lim',today-2,clock_timestamp()-interval '40 days');

  -- Confirmed appointments with a recorded purpose (Grace's has none recorded), plus one held time that is not confirmed.
  insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,hold_expires_at,joining_details,purpose)
  select v.customer_id,sophie,s.at,s.at+interval '30 minutes',v.format,v.status,case when v.status='held' then clock_timestamp()+interval '3 days' end,
    (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format=v.format),v.purpose
  from (values (1002,3,'14:30','video','confirmed','Periodic review documents and fixed deposit maturity',0),
               (1004,4,'11:00','phone','confirmed',null,0),
               (1005,1,'16:00','in_person','confirmed','Current account fee review',7),
               (1004,5,'15:00','video','held',null,0)) v(customer_id,dow,at_time,format,status,purpose,extra)
  cross join lateral (select ((today+((v.dow-extract(isodow from today)::int+6)%7)+1+v.extra)+v.at_time::time) at time zone 'Asia/Singapore' at) s;
  if has_david then
    insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose)
    select 1003,sophie,s.at,s.at+interval '30 minutes','in_person','confirmed',
      (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='in_person'),'Introductory meeting after the change of relationship manager'
    from (select ((today+((2-extract(isodow from today)::int+6)%7)+1)+time '10:00') at time zone 'Asia/Singapore' at) s;
  end if;

  -- Mei Ling's request raised with Rachel (its trigger opens an RM ticket) and what the bank told her about it.
  insert into rm.requests(request_id,customer_id,advisor_id,who,what,urgency,deadline,country,currency,status,created_at,updated_at) values
    ('00000000-0000-4000-8000-100700000031',1007,rachel,'Tan Mei Ling','Joint account forms to add my husband to my savings account','normal',null,'SG',null,'sent',
     '2026-10-02 11:20+08','2026-10-02 11:20+08') on conflict (request_id) do nothing;
  select * into t from support.tickets where request_id='00000000-0000-4000-8000-100700000031';
  perform support.move_ticket(t.ticket_id,'in_review','Your relationship manager has the joint account forms ready and will send them with the branch appointment options.');

  -- A chat question the assistant could not answer (a fixed deposit renewal rate), handed to the RM as a ticket.
  insert into conversation.sessions(customer_id,mode,language,started_at,last_active_at) values (1007,'personal','English','2026-10-27 20:05+08','2026-10-27 20:06+08')
    returning session_id into sess;
  insert into conversation.cases(session_id,customer_id,topic,priority,created_at) values (sess,1007,'relationship','P3','2026-10-27 20:05+08') returning case_id into kase;
  insert into conversation.messages(session_id,customer_id,role,redacted_text,created_at) values
    (sess,1007,'user','What interest rate will I get if I renew my fixed deposit in December?','2026-10-27 20:05+08') returning message_id into q;
  insert into conversation.messages(session_id,customer_id,role,redacted_text,created_at) values
    (sess,1007,'assistant','I can''t quote a renewal rate for your fixed deposit; rates are set by the bank at the time of renewal. I''ve passed your question to your relationship manager, who will reply to you. You can follow it in My requests.','2026-10-27 20:06+08')
    returning message_id into a;
  insert into conversation.turns(turn_id,session_id,customer_id,case_id,user_message_id,answer_message_id,state,run_id,request_hash,revision,created_at)
    values (gen_random_uuid(),sess,1007,kase,q,a,'completed',gen_random_uuid(),repeat('0',64),1,'2026-10-27 20:05+08');
  insert into support.handoffs(customer_id,case_id,reason,priority,summary,created_at)
    values (1007,kase,'general_support','P3','Question for your relationship manager (fees or rates): What interest rate will I get if I renew my fixed deposit in December?','2026-10-27 20:06+08')
    returning handoff_id into h;

  -- Mei Ling's handover notes for Sophie: one under active consent, one whose consent expired, one revoked.
  insert into consent.grants(grant_id,customer_id,purpose,recipient,categories,granted_at,expires_at,revoked_at) values
    ('00000000-0000-4000-8000-100700000041',1007,'rm_brief',sophie::text,array['agenda','profile'],'2026-10-27 09:00+08','2027-04-30 23:59+08',null),
    ('00000000-0000-4000-8000-100700000043',1007,'rm_brief',sophie::text,array['profile'],'2026-01-05 09:00+08','2026-10-01 00:00+08',null),
    ('00000000-0000-4000-8000-100700000045',1007,'rm_brief',sophie::text,array['profile'],'2026-10-26 09:00+08','2027-04-30 23:59+08','2026-10-28 18:00+08')
  on conflict (grant_id) do nothing;
  insert into rm.shared_summaries(summary_id,customer_id,advisor_id,content,consent_grant_id,shared_at) values
    ('00000000-0000-4000-8000-100700000042',1007,sophie,'Handover note for my new relationship manager: I am planning to move to a bigger flat next year and may need a home loan, so I would like general information first. Please call after 6pm on weekdays; I work shifts.','00000000-0000-4000-8000-100700000041','2026-10-27 09:05+08'),
    ('00000000-0000-4000-8000-100700000044',1007,sophie,'Older note (consent expired): please post statements to my old address.','00000000-0000-4000-8000-100700000043','2026-01-05 09:05+08'),
    ('00000000-0000-4000-8000-100700000046',1007,sophie,'Withdrawn note (consent revoked): details about a family matter.','00000000-0000-4000-8000-100700000045','2026-10-26 09:05+08')
  on conflict (summary_id) do nothing;
end $seed$;
$source_0090$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0090';
  if found then
    if previous.name is distinct from 'rm_demo_seed' or previous.statements is distinct from array[script] then
      raise exception 'migration 0090 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0090 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0090','rm_demo_seed',array[script]);
  end if;
end $apply_0090$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
