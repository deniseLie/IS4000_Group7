-- Business schemas for the multi-agent design (system-architecture.md §5.7-5.9, §5.12): compliance, rm, consent,
-- conversation, actions, support, notify, events. kb and audit come with their own Step 1 tasks.
-- Every table has RLS. Customer tables carry customer_id and get one read-only policy:
--   customer_id = (select banking.my_customer_id())
-- No customer write policies on purpose: writes come later through the Action Service (run_action()),
-- so a signed-in user cannot change their own records through the Data API.
-- Internal tables (case_internal, advisor_blocks, templates, outbox) have RLS and no policy at all.
-- After applying: add these schemas to Settings -> API -> Exposed schemas (only the ones the backend reads).

create schema if not exists compliance;
create schema if not exists rm;
create schema if not exists consent;
create schema if not exists conversation;
create schema if not exists actions;
create schema if not exists support;
create schema if not exists notify;
create schema if not exists events;

-- compliance: Michelle's review case (CR1-CR6). Only client-visible fields live in customer tables.
create table compliance.cases (
  case_id           uuid primary key default gen_random_uuid(),
  customer_id       bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  case_type         text not null check (case_type in ('periodic_review','additional_review')),
  title             text not null,
  notice_text       text not null,
  notice_date       date not null,
  due_date          date,
  status            text not null check (status in ('review_opened','documents_requested','documents_partially_received','awaiting_client_documents','under_review','information_requested','completed','closed')),
  status_updated_at timestamptz not null default now(),
  created_at        timestamptz not null default now()
);
create index cases_customer_id_idx on compliance.cases (customer_id);

create table compliance.case_internal (
  case_id       uuid primary key references compliance.cases(case_id) on delete cascade,
  risk_rating   text check (risk_rating in ('low','medium','high')),
  reviewer_note text
);

create table compliance.requested_items (
  item_id           uuid primary key default gen_random_uuid(),
  case_id           uuid not null references compliance.cases(case_id) on delete cascade,
  customer_id       bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  label             text not null,
  instructions      text,
  submission_status text not null default 'not_submitted' check (submission_status in ('not_submitted','submitted_awaiting_review','accepted','rejected','unknown')),
  client_mark       text not null default 'not_prepared' check (client_mark in ('prepared','not_prepared')),
  client_marked_at  timestamptz,
  sort_order        smallint not null default 0,
  updated_at        timestamptz not null default now()
);
create index requested_items_case_id_idx on compliance.requested_items (case_id);
create index requested_items_customer_id_idx on compliance.requested_items (customer_id);

create table compliance.status_history (
  id          bigint generated always as identity primary key,
  case_id     uuid not null references compliance.cases(case_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  status      text not null,
  note        text,
  changed_at  timestamptz not null
);
create index status_history_case_id_idx on compliance.status_history (case_id, changed_at desc);
create index status_history_customer_id_idx on compliance.status_history (customer_id);

create table compliance.info_requests (
  request_id   uuid primary key default gen_random_uuid(),
  case_id      uuid not null references compliance.cases(case_id) on delete cascade,
  customer_id  bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  question     text not null,
  instructions text,
  due_date     date,
  status       text not null default 'open' check (status in ('open','responded','closed')),
  responded_at timestamptz,
  created_at   timestamptz not null default now()
);
create index info_requests_case_id_idx on compliance.info_requests (case_id);
create index info_requests_customer_id_idx on compliance.info_requests (customer_id);

create table compliance.documents (
  document_id    uuid primary key default gen_random_uuid(),
  case_id        uuid not null references compliance.cases(case_id) on delete cascade,
  item_id        uuid references compliance.requested_items(item_id) on delete set null,
  customer_id    bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  storage_path   text not null unique,  -- customer_id/case_id/document_id in Document Storage
  file_name      text not null,
  mime_type      text not null check (mime_type in ('application/pdf','image/jpeg','image/png')),
  size_bytes     int not null check (size_bytes between 1 and 10485760),
  precheck_status text not null default 'not_checked' check (precheck_status in ('not_checked','pending','passed','issues_found')),
  precheck_notes text,
  uploaded_at    timestamptz not null default now()
);
create index documents_case_id_idx on compliance.documents (case_id);
create index documents_item_id_idx on compliance.documents (item_id);
create index documents_customer_id_idx on compliance.documents (customer_id);

-- rm: David's relationship manager, slots, agenda, summaries and requests (RC1-RC5, X6).
create table rm.advisors (
  advisor_id uuid primary key default gen_random_uuid(),
  full_name  text not null,
  role       text not null,
  team_name  text not null,
  email      text,
  phone      text,
  timezone   text not null default 'Asia/Singapore',
  is_active  boolean not null default true
);

create table rm.assignments (
  assignment_id          uuid primary key default gen_random_uuid(),
  customer_id            bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id             uuid not null references rm.advisors(advisor_id),
  started_on             date not null,
  ended_on               date,
  transition_reason_code text,  -- the customer-facing wording comes from an approved article, not this code
  check (ended_on is null or ended_on >= started_on)
);
create index assignments_customer_id_idx on rm.assignments (customer_id);
create index assignments_advisor_id_idx on rm.assignments (advisor_id);
create unique index assignments_one_current_idx on rm.assignments (customer_id) where ended_on is null;

create table rm.working_hours (
  advisor_id uuid not null references rm.advisors(advisor_id) on delete cascade,
  weekday    smallint not null check (weekday between 1 and 7),  -- ISO: 1 = Monday
  starts_at  time not null,
  ends_at    time not null check (ends_at > starts_at),
  primary key (advisor_id, weekday)
);

create table rm.advisor_blocks (
  block_id   uuid primary key default gen_random_uuid(),
  advisor_id uuid not null references rm.advisors(advisor_id) on delete cascade,
  starts_at  timestamptz not null,
  ends_at    timestamptz not null check (ends_at > starts_at)
);
create index advisor_blocks_advisor_id_idx on rm.advisor_blocks (advisor_id, starts_at);

create table rm.agendas (
  agenda_id   uuid primary key default gen_random_uuid(),
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id  uuid not null references rm.advisors(advisor_id),
  topics      jsonb not null default '[]' check (jsonb_typeof(topics) = 'array'),
  status      text not null default 'draft' check (status in ('draft','shared')),
  shared_at   timestamptz,
  updated_at  timestamptz not null default now()
);
create index agendas_customer_id_idx on rm.agendas (customer_id);
create index agendas_advisor_id_idx on rm.agendas (advisor_id);

create table rm.appointments (
  appointment_id  uuid primary key default gen_random_uuid(),
  customer_id     bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id      uuid not null references rm.advisors(advisor_id),
  starts_at       timestamptz not null,
  ends_at         timestamptz not null check (ends_at > starts_at),
  format          text not null check (format in ('video','phone','in_person')),
  status          text not null check (status in ('held','confirmed','cancelled','expired')),
  hold_expires_at timestamptz,
  joining_details text,
  agenda_id       uuid references rm.agendas(agenda_id) on delete set null,
  created_at      timestamptz not null default now(),
  check (status <> 'held' or hold_expires_at is not null)
);
create index appointments_customer_id_idx on rm.appointments (customer_id);
create index appointments_advisor_id_idx on rm.appointments (advisor_id, starts_at);
create index appointments_agenda_id_idx on rm.appointments (agenda_id);

create table rm.requests (
  request_id  uuid primary key default gen_random_uuid(),
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id  uuid references rm.advisors(advisor_id),
  who         text not null,
  what        text not null,
  urgency     text not null default 'normal' check (urgency in ('low','normal','high')),
  deadline    date,
  country     char(2),
  currency    char(3),
  status      text not null default 'sent' check (status in ('sent','seen','answered','closed')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index requests_customer_id_idx on rm.requests (customer_id);
create index requests_advisor_id_idx on rm.requests (advisor_id);

-- consent: grants are written only by the Action Service, so every grant has a confirmation and a receipt.
create table consent.requests (
  request_id  uuid primary key default gen_random_uuid(),
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  purpose     text not null,
  recipient   text not null,
  categories  text[] not null default '{}',
  status      text not null default 'pending' check (status in ('pending','granted','declined','expired')),
  created_at  timestamptz not null default now()
);
create index consent_requests_customer_id_idx on consent.requests (customer_id);

create table consent.grants (
  grant_id    uuid primary key default gen_random_uuid(),
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  purpose     text not null,
  recipient   text not null,
  categories  text[] not null default '{}',
  request_id  uuid references consent.requests(request_id) on delete set null,
  command_id  uuid,  -- the confirmed actions.commands row that created this grant
  granted_at  timestamptz not null default now(),
  expires_at  timestamptz,
  revoked_at  timestamptz
);
create index grants_lookup_idx on consent.grants (customer_id, purpose, recipient);
create index grants_request_id_idx on consent.grants (request_id);

create table rm.shared_summaries (
  summary_id       uuid primary key default gen_random_uuid(),
  customer_id      bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id       uuid not null references rm.advisors(advisor_id),
  content          text not null,
  consent_grant_id uuid not null references consent.grants(grant_id),
  shared_at        timestamptz not null default now()
);
create index shared_summaries_customer_id_idx on rm.shared_summaries (customer_id);
create index shared_summaries_advisor_id_idx on rm.shared_summaries (advisor_id);
create index shared_summaries_grant_id_idx on rm.shared_summaries (consent_grant_id);

-- conversation: redacted chat history (X1). Guests have customer_id null and are never visible to anyone.
create table conversation.sessions (
  session_id     uuid primary key default gen_random_uuid(),
  customer_id    bigint references banking.customer_profiles(customer_id) on delete cascade,
  started_at     timestamptz not null default now(),
  last_active_at timestamptz not null default now(),
  expires_at     timestamptz
);
create index sessions_customer_id_idx on conversation.sessions (customer_id, last_active_at desc);

create table conversation.messages (
  message_id    uuid primary key default gen_random_uuid(),
  session_id    uuid not null references conversation.sessions(session_id) on delete cascade,
  customer_id   bigint references banking.customer_profiles(customer_id) on delete cascade,
  role          text not null check (role in ('user','assistant','system')),
  redacted_text text not null,
  created_at    timestamptz not null default now()
);
create index messages_session_id_idx on conversation.messages (session_id, created_at);
create index messages_customer_id_idx on conversation.messages (customer_id);

create table conversation.checkpoints (
  session_id    uuid not null references conversation.sessions(session_id) on delete cascade,
  checkpoint_id text not null,
  customer_id   bigint references banking.customer_profiles(customer_id) on delete cascade,
  data          jsonb not null,
  created_at    timestamptz not null default now(),
  primary key (session_id, checkpoint_id)
);
create index checkpoints_customer_id_idx on conversation.checkpoints (customer_id);

create table conversation.cases (
  case_id     uuid primary key default gen_random_uuid(),
  session_id  uuid not null references conversation.sessions(session_id) on delete cascade,
  customer_id bigint references banking.customer_profiles(customer_id) on delete cascade,
  topic       text not null,
  priority    text not null check (priority in ('P0','P1','P2','P3')),
  status      text not null default 'open' check (status in ('open','closed')),
  created_at  timestamptz not null default now()
);
create index conv_cases_session_id_idx on conversation.cases (session_id);
create index conv_cases_customer_id_idx on conversation.cases (customer_id);

-- actions: propose -> confirm -> execute -> receipt (contracts.ActionCommand / Receipt).
create table actions.commands (
  command_id      uuid primary key default gen_random_uuid(),
  customer_id     bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  case_id         uuid references conversation.cases(case_id) on delete set null,
  type            text not null,
  target          jsonb not null default '{}' check (jsonb_typeof(target) = 'object'),
  idempotency_key text not null unique,  -- case_id:task_id:type
  state           text not null default 'proposed' check (state in ('proposed','confirmed','succeeded','failed','expired')),
  proposed_at     timestamptz not null default now(),
  expires_at      timestamptz not null,
  confirmed_at    timestamptz
);
create index commands_customer_id_idx on actions.commands (customer_id, proposed_at desc);
create index commands_case_id_idx on actions.commands (case_id);

create table actions.receipts (
  receipt_id  text primary key,
  command_id  uuid not null unique references actions.commands(command_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  outcome     text not null check (outcome in ('succeeded','failed')),
  result      jsonb not null default '{}',
  created_at  timestamptz not null default now()
);
create index receipts_customer_id_idx on actions.receipts (customer_id);

-- support: human handoffs. One open handoff per case and reason; queue order P0 first, then age.
create table support.handoffs (
  handoff_id  uuid primary key default gen_random_uuid(),
  customer_id bigint references banking.customer_profiles(customer_id) on delete cascade,
  case_id     uuid references conversation.cases(case_id) on delete set null,
  reason      text not null,
  priority    text not null check (priority in ('P0','P1','P2','P3')),
  summary     text not null,  -- redacted
  status      text not null default 'open' check (status in ('open','claimed','closed')),
  claimed_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create unique index handoffs_one_open_idx on support.handoffs (case_id, reason) where status <> 'closed';
create index handoffs_queue_idx on support.handoffs (priority, created_at) where status = 'open';
create index handoffs_customer_id_idx on support.handoffs (customer_id);

-- notify: templates, preferences and the in-app inbox.
create table notify.templates (
  template_key text not null,
  version      int not null default 1,
  locale       text not null default 'en',
  channel      text not null check (channel in ('in_app','email')),
  title        text not null,
  body         text not null,
  is_service   boolean not null default false,  -- service messages ignore opt-outs and quiet hours
  primary key (template_key, version, locale, channel)
);

create table notify.preferences (
  customer_id bigint primary key references banking.customer_profiles(customer_id) on delete cascade,
  channels    text[] not null default '{in_app}',
  quiet_start time,
  quiet_end   time,
  updated_at  timestamptz not null default now()
);

create table notify.inbox (
  notification_id uuid primary key default gen_random_uuid(),
  customer_id     bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  template_key    text not null,
  title           text not null,
  body            text not null,
  is_service      boolean not null default false,
  read_at         timestamptz,
  created_at      timestamptz not null default now()
);
create index inbox_customer_id_idx on notify.inbox (customer_id, created_at desc);

-- events: outbox polled by the background worker (SELECT ... FOR UPDATE SKIP LOCKED).
create table events.outbox (
  event_id     bigint generated always as identity primary key,
  event_type   text not null,
  customer_id  bigint references banking.customer_profiles(customer_id) on delete cascade,
  payload      jsonb not null default '{}' check (jsonb_typeof(payload) = 'object'),
  status       text not null default 'pending' check (status in ('pending','processing','done','failed')),
  attempts     smallint not null default 0,
  available_at timestamptz not null default now(),
  created_at   timestamptz not null default now(),
  processed_at timestamptz
);
create index outbox_pending_idx on events.outbox (available_at) where status = 'pending';
create index outbox_customer_id_idx on events.outbox (customer_id);

-- RLS on every table, nothing granted until listed below.
do $$
declare t record;
begin
  for t in select schemaname, tablename from pg_tables
           where schemaname in ('compliance','rm','consent','conversation','actions','support','notify','events')
  loop
    execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
    execute format('revoke all on %I.%I from anon, authenticated', t.schemaname, t.tablename);
  end loop;
end $$;

grant usage on schema compliance, rm, consent, conversation, actions, support, notify to authenticated;

-- Customer tables: read own rows only.
do $$
declare t text;
begin
  foreach t in array array[
    'compliance.cases','compliance.requested_items','compliance.status_history','compliance.info_requests','compliance.documents',
    'rm.assignments','rm.agendas','rm.appointments','rm.requests','rm.shared_summaries',
    'consent.requests','consent.grants',
    'conversation.sessions','conversation.messages','conversation.checkpoints','conversation.cases',
    'actions.commands','actions.receipts','support.handoffs','notify.preferences','notify.inbox']
  loop
    execute format('grant select on %s to authenticated', t);
    execute format('create policy own_rows on %s for select to authenticated using (customer_id = (select banking.my_customer_id()))', t);
  end loop;
end $$;

-- Advisors and their working hours: visible only when the advisor is (or was) assigned to the customer.
create or replace function rm.is_my_advisor(p_advisor_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$ select exists (select 1 from rm.assignments a
                     where a.advisor_id = p_advisor_id and a.customer_id = banking.my_customer_id()) $$;
revoke execute on function rm.is_my_advisor(uuid) from public, anon;
grant execute on function rm.is_my_advisor(uuid) to authenticated;

grant select on rm.advisors, rm.working_hours to authenticated;
create policy my_advisor on rm.advisors for select to authenticated using (rm.is_my_advisor(advisor_id));
create policy my_advisor on rm.working_hours for select to authenticated using (rm.is_my_advisor(advisor_id));
