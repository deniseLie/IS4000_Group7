-- Consent v2, part 1: grants with a scope and an end (RM copilot build plan 6a; architecture §15.2). Synthetic data only.
--   1. consent.categories: the catalogue the client sees on Data sharing. Three gated categories (transaction_insights,
--      chat_topics, meeting_recording) and the always-shared records her RM sees without a grant, each with the reason.
--      The topic areas a chat_topics grant can cover; health, family and legal are never stored.
--   2. consent.grants gains scope, an end (a date up to 12 months ahead, her next meeting, or the meeting a request she is
--      sending becomes), the notice version she read, basis (consent or notice) and why it ended. One active v2 grant per
--      client, RM and category: a new one ends the one before (end_reason replaced).
--   3. consent.grant takes the v2 target ({category, recipient, scope, notice_version, until, ...}); the 0037 request-based
--      target still works. consent.revoke ends a v2 grant the same way; a notice-based grant cannot be revoked.
--   4. rm.consent_scope: what the active grant covers. rm_transaction_insights and rm_cash_events read only the accounts and
--      the look-back she chose; rm_talking_points shows inferred topics only in her topic areas. rm.consent_active (0105)
--      stays the only on/off check, and without it the readers still return {"shared": false} (never fewer rows).
--   5. Meeting-bound grants: consent.close_meeting_grants(meeting) ends them; the meeting side calls it when the minutes
--      of that meeting are approved (minutes.approved, architecture §15). A cancelled meeting or a declined or withdrawn
--      request ends them here; a scheduled request binds them to its meeting.
--   6. meeting_recording is on by default under the meeting notice: confirming a meeting records the notice version on it
--      and, if she has none, a notice-based grant to that RM.
--   7. Renewal: 7 days before a dated grant ends, one consent.renewal_due notice (worker producer, 0025 pattern).
--   8. customer_read_consent_sharing: the Data sharing cards (catalogue, her grant per category, her accounts, her RM and
--      next meeting) under her JWT.
--   9. Michelle's demo grant in the v2 shape (one grant per category) and rm.reset_meeting_demo re-seeding it.

-- 1. Catalogue -------------------------------------------------------------------------------------------------------
create table consent.categories (
  code           text primary key check (code ~ '^[a-z][a-z_]{0,39}$'),
  label          text not null,
  description    text not null,
  gated          boolean not null,  -- an RM read needs an active grant (rm.consent_active); false: always shared with her RM
  default_on     boolean not null,
  withdrawable   boolean not null,  -- she can switch it off on Data sharing
  max_months     smallint check (max_months between 1 and 12),
  notice_version text check (notice_version ~ '^[a-z0-9.-]{1,20}$'),
  reason         text,              -- why it is always on, or why it cannot be switched off here
  sort           smallint not null unique,
  check (gated or not withdrawable),
  check (not withdrawable or (max_months is not null and notice_version is not null))
);
insert into consent.categories(code,label,description,gated,default_on,withdrawable,max_months,notice_version,reason,sort) values
  ('transaction_insights','Transaction insights','Monthly money in and out, card and overseas spending, large movements and regular payments, worked out '
   'from the accounts and the months you choose. Your relationship manager never sees the description of an ordinary transaction.',
   true,false,true,12,'ti-2026-11',null,1),
  ('chat_topics','Topics from your chats','DemoBank''s assistant notes what you are planning, such as a home loan, in the topic areas you choose. '
   'Your relationship manager sees the topic, how far along you are and when, never your words. Health, family and legal matters are never kept.',
   true,false,true,12,'ct-2026-11',null,2),
  ('meeting_recording','Meeting recording','Meetings with your relationship manager are recorded so the minutes can be written. The notice is in '
   'every meeting invitation.',true,true,false,null,'mr-2026-11',
   'On for every meeting under the meeting notice. Removing a meeting transcript is not available yet; the minutes stay.',3),
  ('profile_basics','Your profile','Your name, preferred language, employment and income range.',false,true,false,null,null,
   'Your relationship manager needs these to look after your relationship with the bank.',4),
  ('products','Your accounts, cards and loans','Which products you hold and their balances.',false,true,false,null,null,
   'Your relationship manager looks after these products with you.',5),
  ('requests_and_meetings','Your requests and meetings','Your requests, tickets and meetings, and the notes your relationship manager keeps about them.',
   false,true,false,null,null,'Your relationship manager needs them to answer you.',6),
  ('compliance_status','Document review status','Whether a document review is open and what is still needed.',false,true,false,null,null,
   'The bank asks for these documents; your relationship manager helps you with them.',7);
alter table consent.categories enable row level security;
revoke all on consent.categories from public,anon,authenticated;
grant select on consent.categories to authenticated;
create policy catalogue on consent.categories for select to authenticated using (true);

create function consent.topic_areas() returns text[] language sql immutable set search_path='' as
$$ select array['home_property','investments','deposits','insurance','cards','everyday_banking'] $$;
create function consent.sensitive_areas() returns text[] language sql immutable set search_path='' as
$$ select array['health','family','legal'] $$;  -- never stored, never a scope
-- The topic area of a talking-point topic (insights.topics(), 0108).
create function consent.topic_area(p_topic text) returns text language sql immutable set search_path='' as $$
  select case p_topic when 'home_loan' then 'home_property' when 'investments' then 'investments' when 'fixed_deposit' then 'deposits'
    when 'insurance' then 'insurance' end
$$;

-- 2. Grants ----------------------------------------------------------------------------------------------------------
alter table consent.grants
  add column scope              jsonb,  -- v2: the category's scope (consent.normal_scope); null for a 0037 grant
  add column until_kind         text check (until_kind in ('date','meeting','request')),
  add column until_meeting_id   uuid references rm.appointments(appointment_id) on delete set null,
  add column until_request_id   uuid references rm.meeting_requests(request_id) on delete set null,
  add column notice_version     text check (notice_version ~ '^[a-z0-9.-]{1,20}$'),
  add column basis              text not null default 'consent' check (basis in ('consent','notice')),
  add column end_reason         text check (end_reason in ('revoked','replaced','meeting_closed','meeting_cancelled','request_closed')),
  add column renewal_noticed_at timestamptz,
  add constraint grants_v2_shape check (scope is null or (jsonb_typeof(scope)='object' and cardinality(categories)=1 and notice_version is not null
    and (basis='notice' or until_kind is not null))),
  add constraint grants_end_reason check (end_reason is null or revoked_at is not null);
create index grants_until_meeting_idx on consent.grants(until_meeting_id) where until_meeting_id is not null;
create index grants_until_request_idx on consent.grants(until_request_id) where until_request_id is not null;
alter table rm.appointments add column recording_notice_version text;  -- the meeting notice in force when it was confirmed

-- One scope per category: transaction_insights {accounts: "all" | [account ids], lookback_months: 3|6|12};
-- chat_topics {topic_areas: [...]} in list order, never a sensitive area; meeting_recording {}.
create function consent.normal_scope(p_category text,p_scope jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
declare accounts jsonb; areas jsonb;
begin
  if jsonb_typeof(p_scope) is distinct from 'object' then raise exception 'a grant needs a scope' using errcode='22023'; end if;
  if p_category='transaction_insights' then
    if p_scope->'accounts'='"all"'::jsonb then accounts:='"all"';
    elsif jsonb_typeof(p_scope->'accounts')='array' and jsonb_array_length(p_scope->'accounts') between 1 and 20
        and not exists(select 1 from jsonb_array_elements(p_scope->'accounts') a where jsonb_typeof(a)<>'number' or a::text !~ '^[1-9][0-9]{0,17}$') then
      select jsonb_agg(v order by v) into accounts from (select distinct (a#>>'{}')::bigint v from jsonb_array_elements(p_scope->'accounts') a) x;
    end if;
    if accounts is null or jsonb_typeof(p_scope->'lookback_months') is distinct from 'number' or p_scope->>'lookback_months' not in ('3','6','12') then
      raise exception 'transaction insights need "all" or 1-20 account ids and a 3, 6 or 12 month look-back' using errcode='22023';
    end if;
    return jsonb_build_object('accounts',accounts,'lookback_months',(p_scope->>'lookback_months')::int);
  elsif p_category='chat_topics' then
    if jsonb_typeof(p_scope->'topic_areas') is distinct from 'array' or jsonb_array_length(p_scope->'topic_areas')=0
        or exists(select 1 from jsonb_array_elements(p_scope->'topic_areas') a where jsonb_typeof(a)<>'string') then
      raise exception 'chat topics need at least one topic area' using errcode='22023';
    end if;
    if exists(select 1 from jsonb_array_elements_text(p_scope->'topic_areas') a where a=any(consent.sensitive_areas())) then
      raise exception 'health, family and legal matters are never kept' using errcode='22023';
    end if;
    if exists(select 1 from jsonb_array_elements_text(p_scope->'topic_areas') a where not (a=any(consent.topic_areas()))) then
      raise exception 'unknown topic area' using errcode='22023';
    end if;
    select jsonb_agg(a order by array_position(consent.topic_areas(),a)) into areas from (select distinct a from jsonb_array_elements_text(p_scope->'topic_areas') a) x;
    return jsonb_build_object('topic_areas',areas);
  elsif p_category='meeting_recording' then return '{}'::jsonb;
  end if;
  raise exception 'unknown consent category' using errcode='22023';
end $$;

-- 3. consent.grant and consent.revoke --------------------------------------------------------------------------------
-- {request_id}: the 0037 grant of a recorded request. Otherwise the v2 target: category (transaction_insights or
-- chat_topics), recipient (her RM), scope, the notice version she read and until: date (expires_on), meeting (meeting_id)
-- or request (chained after the meeting.request it waits for: after_receipt, filled by offer_group_confirm).
create or replace function actions.normalise_consent_grant(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare t jsonb; until_ text:=p_target->>'until'; d date;
begin
  if p_target ? 'request_id' then
    if jsonb_typeof(p_target->'request_id') is distinct from 'string' or p_target->>'request_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
      raise exception 'consent grant requires a recorded request UUID' using errcode='22023';
    end if;
    return jsonb_build_object('request_id',(p_target->>'request_id')::uuid);
  end if;
  if jsonb_typeof(p_target->'category') is distinct from 'string' or p_target->>'category' not in ('transaction_insights','chat_topics')
      or jsonb_typeof(p_target->'recipient') is distinct from 'string' or p_target->>'recipient' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'notice_version') is distinct from 'string' or p_target->>'notice_version' !~ '^[a-z0-9.-]{1,20}$'
      or jsonb_typeof(p_target->'until') is distinct from 'string' or until_ not in ('date','meeting','request') then
    raise exception 'a consent grant needs transaction_insights or chat_topics, her relationship manager, the notice version she read and an end'
      using errcode='22023';
  end if;
  t:=jsonb_build_object('category',p_target->>'category','recipient',(p_target->>'recipient')::uuid,
    'scope',consent.normal_scope(p_target->>'category',p_target->'scope'),'notice_version',p_target->>'notice_version','until',until_);
  if until_='date' then
    begin d:=(p_target->>'expires_on')::date; exception when others then d:=null; end;
    if jsonb_typeof(p_target->'expires_on') is distinct from 'string' or p_target->>'expires_on' !~ '^\d{4}-\d{2}-\d{2}$' or d is null then
      raise exception 'sharing until a date needs expires_on (YYYY-MM-DD)' using errcode='22023';
    end if;
    t:=t||jsonb_build_object('expires_on',d);
  elsif until_='meeting' then
    if jsonb_typeof(p_target->'meeting_id') is distinct from 'string' or p_target->>'meeting_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
      raise exception 'sharing until a meeting needs the meeting UUID' using errcode='22023';
    end if;
    t:=t||jsonb_build_object('meeting_id',(p_target->>'meeting_id')::uuid);
  elsif not (p_target ? 'after_receipt') then
    raise exception 'sharing until a requested meeting is chained after that meeting request' using errcode='22023';
  end if;
  if p_target ? 'after_receipt' then
    if p_target->'after_receipt'<>'null'::jsonb and coalesce(p_target->>'after_receipt','') !~ '^rcpt_[a-f0-9]{32}$' then
      raise exception 'after_receipt is null or a receipt id' using errcode='22023';
    end if;
    t:=t||jsonb_build_object('after_receipt',p_target->'after_receipt');
  end if;
  return t;
end $$;

create or replace function actions.consent_grant_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' set timezone='UTC'
as $$
declare target jsonb:=actions.normalise_consent_grant(p_target); request consent.requests; recipient_name text; cat consent.categories;
  advisor uuid; expires timestamptz; meeting rm.appointments; replaces uuid; today date:=rm.today();
begin
  if target ? 'request_id' then  -- 0037
    select * into request from consent.requests where request_id=(target->>'request_id')::uuid and customer_id=p_customer_id;
    if not found then raise exception 'consent request is outside the customer scope' using errcode='42501'; end if;
    if request.grant_expires_at is null or request.command_id is null then
      raise exception 'this legacy request has no reviewed expiry; record a new scoped request' using errcode='22023';
    end if;
    select full_name into recipient_name from rm.advisors where advisor_id::text=request.recipient;
    return target||jsonb_build_object('purpose',request.purpose,'recipient',request.recipient,'recipient_name',recipient_name,
      'categories',(select jsonb_agg(c order by c) from unnest(request.categories)c),'expires_at',request.grant_expires_at);
  end if;
  select * into cat from consent.categories where code=target->>'category';
  if target->>'notice_version' is distinct from cat.notice_version then
    raise exception 'the sharing notice changed; read the current notice first' using errcode='22023';
  end if;
  advisor:=(target->>'recipient')::uuid;
  select a.full_name into recipient_name from rm.assignments r join rm.advisors a using(advisor_id)
    where r.customer_id=p_customer_id and r.advisor_id=advisor and r.ended_on is null and r.started_on<=today and a.is_active;
  if recipient_name is null then raise exception 'consent recipient is not the client''s current relationship manager' using errcode='42501'; end if;
  if jsonb_typeof(target->'scope'->'accounts')='array' and exists(select 1 from jsonb_array_elements(target->'scope'->'accounts') x
      where not exists(select 1 from banking.bank_accounts b where b.account_id=(x#>>'{}')::bigint and b.customer_id=p_customer_id and b.status<>'closed')) then
    raise exception 'every shared account must be one of the client''s open accounts' using errcode='42501';
  end if;
  if target->>'until'='date' then
    if (target->>'expires_on')::date<=today or (target->>'expires_on')::date>(today+make_interval(months=>cat.max_months))::date then
      raise exception 'choose an end date from tomorrow up to % months ahead',cat.max_months using errcode='22023';
    end if;
    expires:=((target->>'expires_on')::date+1)::timestamp at time zone 'Asia/Singapore';  -- through the end of that day in Singapore
  elsif target->>'until'='meeting' then
    select * into meeting from rm.appointments where appointment_id=(target->>'meeting_id')::uuid and customer_id=p_customer_id
      and advisor_id=advisor and status='confirmed' and starts_at>clock_timestamp();
    if not found then raise exception 'the meeting must be her upcoming confirmed meeting with that relationship manager' using errcode='42501'; end if;
    expires:=meeting.ends_at+interval '30 days';  -- the backstop if the minutes are never approved
  end if;
  select g.grant_id into replaces from consent.grants g where g.customer_id=p_customer_id and g.purpose='rm_brief' and g.recipient=advisor::text
    and g.scope is not null and g.categories=array[target->>'category'] and g.revoked_at is null and (g.expires_at is null or g.expires_at>clock_timestamp())
    order by g.granted_at desc limit 1;
  return target||jsonb_build_object('purpose','rm_brief','categories',jsonb_build_array(target->>'category'),'category_label',cat.label,
    'recipient_name',recipient_name,'replaces',replaces,'expires_at',expires,'meeting_starts_at',meeting.starts_at);
end $$;

-- The meeting request a chained grant waits for: the receipt just before it is that meeting.request, or another grant
-- chained to the same request.
create function consent.chained_request(p_receipt text,p_customer bigint,p_advisor uuid) returns uuid
language sql stable security definer set search_path='' as $$
  select q.request_id from actions.receipts r join actions.commands c on c.command_id=r.command_id and c.customer_id=p_customer
    join rm.meeting_requests q on q.customer_id=p_customer and q.advisor_id=p_advisor and q.status in ('requested','countered','scheduled')
      and q.request_id::text=case c.type when 'meeting.request' then r.result->>'request_id' when 'consent.grant' then r.result->>'until_request_id' end
  where r.receipt_id=p_receipt and r.customer_id=p_customer and r.outcome='succeeded'
$$;

create or replace function actions.execute_consent_grant(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare request consent.requests; v_grant consent.grants; t jsonb:=p_command.target; bound_request uuid; expires timestamptz; replaced int; narrowed int;
  category text:=p_command.target->>'category';
begin
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(p_command.customer_id::text));
  if t ? 'request_id' then  -- 0037
    select * into request from consent.requests where request_id=(t->>'request_id')::uuid and customer_id=p_command.customer_id for update;
    if not found or request.status<>'pending' or request.grant_expires_at is null or request.grant_expires_at<=clock_timestamp()
        or actions.consent_grant_target(t,p_command.customer_id)<>t then
      raise exception 'the reviewed consent request expired or changed' using errcode='P1001';
    end if;
    perform consent.require_current_recipient(p_command.customer_id,(request.recipient)::uuid);
    insert into consent.grants(customer_id,purpose,recipient,categories,request_id,command_id,granted_at,expires_at)
      values(p_command.customer_id,request.purpose,request.recipient,request.categories,request.request_id,p_command.command_id,clock_timestamp(),request.grant_expires_at)
      returning * into v_grant;
    update consent.requests set status='granted' where request_id=request.request_id and customer_id=p_command.customer_id;
    perform audit.record('consent','consent.granted','success','consent.grants',v_grant.grant_id::text,
      jsonb_build_object('category_count',cardinality(v_grant.categories)),p_command.case_id);
    return jsonb_build_object('result',t||jsonb_build_object('grant_id',v_grant.grant_id,'status','active'),'event_type','consent.granted');
  end if;
  if actions.consent_grant_target(t,p_command.customer_id)<>t then raise exception 'the reviewed sharing choice changed' using errcode='P1001'; end if;
  perform consent.require_current_recipient(p_command.customer_id,(t->>'recipient')::uuid);
  expires:=(t->>'expires_at')::timestamptz;
  if t->>'until'='request' then
    bound_request:=consent.chained_request(t->>'after_receipt',p_command.customer_id,(t->>'recipient')::uuid);
    if bound_request is null then raise exception 'sharing until a requested meeting needs that request recorded first' using errcode='P1001'; end if;
    expires:=clock_timestamp()+interval '90 days';  -- the backstop; scheduling the request binds the grant to its meeting
  end if;
  -- The new grant first, then the one it replaces, so chat topics stay on throughout (0108 forgets inferred topics only
  -- when no chat_topics grant is left).
  insert into consent.grants(customer_id,purpose,recipient,categories,command_id,granted_at,expires_at,scope,until_kind,until_meeting_id,until_request_id,notice_version)
    values(p_command.customer_id,'rm_brief',t->>'recipient',array[category],p_command.command_id,clock_timestamp(),expires,t->'scope',t->>'until',
      (t->>'meeting_id')::uuid,bound_request,t->>'notice_version') returning * into v_grant;
  update consent.grants set revoked_at=clock_timestamp(),end_reason='replaced' where customer_id=p_command.customer_id and purpose='rm_brief'
    and recipient=t->>'recipient' and categories=array[category] and scope is not null and revoked_at is null and grant_id<>v_grant.grant_id;
  get diagnostics replaced=row_count;
  -- A 0037 grant covering this category stops covering it: from now on the v2 grant alone decides.
  update consent.grants set categories=array_remove(categories,category),
      revoked_at=case when categories=array[category] then clock_timestamp() end,end_reason=case when categories=array[category] then 'replaced' end
    where customer_id=p_command.customer_id and purpose='rm_brief' and recipient=t->>'recipient' and scope is null and category=any(categories) and revoked_at is null;
  get diagnostics narrowed=row_count;
  perform audit.record('consent','consent.granted','success','consent.grants',v_grant.grant_id::text,
    jsonb_build_object('category',category,'until',t->>'until','notice_version',t->>'notice_version','replaced',replaced+narrowed,
      'accounts',case when t->'scope'->'accounts'='"all"'::jsonb then 'all' when t->'scope' ? 'accounts' then jsonb_array_length(t->'scope'->'accounts')::text end,
      'lookback_months',(t->'scope'->>'lookback_months')::int,'topic_areas',t->'scope'->'topic_areas'),p_command.case_id);
  return jsonb_build_object('result',t||jsonb_build_object('grant_id',v_grant.grant_id,'status','active','active_until',v_grant.expires_at,
    'until_request_id',bound_request),'event_type','consent.granted');
end $$;

-- 0040's revocation target, refusing the notice-based recording grant (6c will remove a transcript instead).
create or replace function actions.consent_revoke_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' set timezone='UTC'
as $$
declare target jsonb:=actions.normalise_consent_revoke(p_target); v_grant consent.grants; recipient_name text;
begin
  select * into v_grant from consent.grants where grant_id=(target->>'grant_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'consent grant is outside the customer scope' using errcode='42501'; end if;
  if v_grant.basis='notice' then raise exception 'meeting recording is on under the meeting notice and cannot be switched off here' using errcode='22023'; end if;
  select full_name into recipient_name from rm.advisors a where a.advisor_id::text=v_grant.recipient
    and exists(select 1 from rm.assignments r where r.customer_id=p_customer_id and r.advisor_id=a.advisor_id);
  return target||jsonb_build_object('purpose',v_grant.purpose,'recipient',v_grant.recipient,'recipient_name',recipient_name,
    'categories',(select jsonb_agg(c order by c) from unnest(v_grant.categories)c),'expires_at',v_grant.expires_at);
end $$;

create or replace function actions.execute_consent_revoke(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare v_grant consent.grants;
begin
  perform pg_advisory_xact_lock(hashtext('consent.customer'),hashtext(p_command.customer_id::text));
  select * into v_grant from consent.grants where grant_id=(p_command.target->>'grant_id')::uuid and customer_id=p_command.customer_id for update;
  if not found or v_grant.revoked_at is not null or actions.consent_revoke_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the reviewed consent grant was revoked or changed' using errcode='P1001';
  end if;
  update consent.grants set revoked_at=clock_timestamp(),revocation_command_id=p_command.command_id,end_reason='revoked'
    where grant_id=v_grant.grant_id and customer_id=p_command.customer_id returning * into v_grant;
  perform audit.record('consent','consent.revoked','success','consent.grants',v_grant.grant_id::text,
    case when v_grant.scope is null then '{}'::jsonb else jsonb_build_object('category',v_grant.categories[1]) end,p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('revoked_at',v_grant.revoked_at,'status','revoked'),'event_type','consent.revoked');
end $$;

-- 4. Scope in the gated readers -------------------------------------------------------------------------------------
-- The scope of the grant rm.grant_for picks: {} for a 0037 grant (no limits), null when nothing is shared.
create function rm.consent_scope(p_customer bigint,p_category text,p_advisor uuid) returns jsonb
language sql stable security definer set search_path='' as $$
  select coalesce(g.scope,'{}'::jsonb) from consent.grants g where g.grant_id=rm.grant_for(p_customer,p_category,p_advisor)
$$;
-- The transactions a transaction_insights scope allows: her chosen accounts (card transactions only with "all") from the
-- later of p_since and the look-back.
create function rm.scoped_transactions(p_customer bigint,p_scope jsonb,p_since timestamptz) returns setof banking.transactions
language sql stable security definer set search_path='' as $$
  select t.* from banking.transactions t where t.customer_id=p_customer and t.status in ('posted','pending')
    and t.txn_date>=greatest(p_since,clock_timestamp()-make_interval(months=>coalesce((p_scope->>'lookback_months')::int,1200)))
    and (jsonb_typeof(p_scope->'accounts') is distinct from 'array' or t.account_id in (select (x#>>'{}')::bigint from jsonb_array_elements(p_scope->'accounts') x))
$$;
-- What the copilot is told about the scope: her accounts by type and masked number, never ids.
create function rm.scope_json(p_customer bigint,p_scope jsonb,p_since timestamptz) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('accounts',case when jsonb_typeof(p_scope->'accounts')='array' then (select coalesce(jsonb_agg(jsonb_build_object('type',b.account_type,
      'number',b.masked_account_number,'currency',b.currency) order by b.account_id),'[]') from banking.bank_accounts b where b.customer_id=p_customer
      and b.account_id in (select (x#>>'{}')::bigint from jsonb_array_elements(p_scope->'accounts') x)) else '"all"'::jsonb end,
    'cards',jsonb_typeof(p_scope->'accounts') is distinct from 'array','lookback_months',(p_scope->>'lookback_months')::int,
    'from',(greatest(p_since,clock_timestamp()-make_interval(months=>coalesce((p_scope->>'lookback_months')::int,1200))) at time zone 'Asia/Singapore')::date)
$$;

-- 0107's reader, limited to the scope; up to 365 days so a 12-month look-back can be read whole. Same result plus scope.
create or replace function public.rm_transaction_insights(p_customer_id bigint,p_purpose text,p_days int default 90) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; scope jsonb; since timestamptz; days int:=coalesce(p_days,90);
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if days not between 30 and 365 then raise exception 'choose 30 to 365 days' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.transaction_insights'); end if;
  g:=rm.grant_for(p_customer_id,'transaction_insights',advisor);
  if g is null then return rm.gated(p_customer_id,'rm.transaction_insights','banking.transactions','transaction_insights',null,p_purpose,null); end if;
  scope:=rm.consent_scope(p_customer_id,'transaction_insights',advisor);
  since:=greatest(clock_timestamp()-make_interval(days=>days),clock_timestamp()-make_interval(months=>coalesce((scope->>'lookback_months')::int,1200)));
  return rm.gated(p_customer_id,'rm.transaction_insights','banking.transactions','transaction_insights',g,p_purpose,(
    with t as (select * from rm.scoped_transactions(p_customer_id,scope,since))
    select jsonb_build_object('days',rm.today()-(since at time zone 'Asia/Singapore')::date,'from',(since at time zone 'Asia/Singapore')::date,'to',rm.today(),
      'scope',rm.scope_json(p_customer_id,scope,since),'transactions',(select count(*) from t),
      'monthly',(select coalesce(jsonb_agg(jsonb_build_object('month',m,'currency',c,'money_in',i,'money_out',o,'count',n) order by m,c),'[]') from (
          select to_char(txn_date at time zone 'Asia/Singapore','YYYY-MM') m,currency c,coalesce(sum(amount) filter (where amount>0),0) i,
            coalesce(abs(sum(amount) filter (where amount<0)),0) o,count(*) n from t group by 1,2) x),
      'card_spending',(select coalesce(jsonb_agg(jsonb_build_object('currency',c,'amount',s,'count',n) order by c),'[]') from (
          select currency c,abs(sum(amount)) s,count(*) n from t where card_id is not null and amount<0 group by 1) x),
      'overseas',jsonb_build_object('count',(select count(*) from t where is_overseas and amount<0),
        'amounts',(select coalesce(jsonb_object_agg(c,s),'{}') from (select currency c,abs(sum(amount)) s from t where is_overseas and amount<0 group by 1) x)),
      'large_movements',(select coalesce(jsonb_agg(jsonb_build_object('date',(txn_date at time zone 'Asia/Singapore')::date,
          'date_text',rm.day((txn_date at time zone 'Asia/Singapore')::date),'direction',case when amount>0 then 'incoming' else 'outgoing' end,
          'amount',abs(amount),'currency',currency,'counterparty',merchant,'status',status) order by txn_date desc),'[]') from t where abs(amount)>=10000),
      'recurring',(select coalesce(jsonb_agg(jsonb_build_object('counterparty',merchant,'direction',d,'count',n,'typical_amount',a,'currency',c,
          'last_date',l) order by n desc,merchant),'[]') from (
          select merchant,case when amount>0 then 'incoming' else 'outgoing' end d,currency c,count(*) n,round(avg(abs(amount)),2) a,
            max((txn_date at time zone 'Asia/Singapore')::date) l from t group by 1,2,3 having count(*)>=3) x))));
end $$;

-- 0106's large movements, limited to the same scope.
create or replace function public.rm_cash_events(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; scope jsonb;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.cash_events'); end if;
  g:=rm.grant_for(p_customer_id,'transaction_insights',advisor);
  scope:=rm.consent_scope(p_customer_id,'transaction_insights',advisor);
  return rm.gated(p_customer_id,'rm.cash_events','banking.transactions','transaction_insights',g,'client_view',case when g is not null then (
    select coalesce(jsonb_agg(jsonb_build_object(
      'date',(t.txn_date at time zone 'Asia/Singapore')::date,'date_text',rm.day((t.txn_date at time zone 'Asia/Singapore')::date),
      'direction',case when t.amount>0 then 'incoming' else 'outgoing' end,'amount',abs(t.amount),'currency',t.currency,
      'description',t.description,'counterparty',t.merchant,'overseas',t.is_overseas,'status',t.status,
      'recurring',(select count(*)>=3 from rm.scoped_transactions(p_customer_id,scope,clock_timestamp()-interval '180 days') r where r.merchant=t.merchant))
      order by t.txn_date desc),'[]')
    from rm.scoped_transactions(p_customer_id,scope,clock_timestamp()-interval '180 days') t where abs(t.amount)>=10000) end);
end $$;

-- 0108's reader: inferred topics only in the topic areas she chose (a 0037 grant has no areas: all of them).
create or replace function public.rm_talking_points(p_customer_id bigint,p_purpose text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; paused boolean; points jsonb; commands jsonb; pending boolean; r jsonb; areas jsonb;
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.talking_points'); end if;
  g:=rm.grant_for(p_customer_id,'chat_topics',advisor);
  areas:=rm.consent_scope(p_customer_id,'chat_topics',advisor)->'topic_areas';
  paused:=insights.paused(p_customer_id);
  select coalesce(jsonb_agg(insights.point_json(p) order by p.origin desc,p.last_seen desc),'[]'),
      coalesce(jsonb_agg(p.share_command_id) filter (where p.share_command_id is not null),'[]')
    into points,commands from insights.talking_points p
    where p.customer_id=p_customer_id and p.expires_at>clock_timestamp()
      and (p.origin='confirmed' or (g is not null and not paused and p.visible_from<=clock_timestamp() and (areas is null or areas ? consent.topic_area(p.topic))));
  if g is null then
    r:=rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',null,p_purpose,null);
    if jsonb_array_length(points)>0 then
      perform audit.record('data','data.read','success','insights.talking_points',r->>'call_id',
        jsonb_build_object('tool','rm.talking_points','result_count',jsonb_array_length(points),'basis','client_shared',
          'command_ids',commands,'purpose',p_purpose),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
    end if;
    return r||jsonb_build_object('data',points,'pending',false);
  end if;
  pending:=exists(select 1 from insights.talking_points p where p.customer_id=p_customer_id and p.origin='inferred'
    and p.expires_at>clock_timestamp() and (paused or p.visible_from>clock_timestamp()) and (areas is null or areas ? consent.topic_area(p.topic)));
  return rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',g,p_purpose,points)||jsonb_build_object('pending',pending);
end $$;

-- 5. Meeting-bound grants -------------------------------------------------------------------------------------------
-- Ends her consent grants bound to one meeting. The meeting side calls it when the minutes of that meeting are approved
-- (minutes.approved; as the meeting runner's role or the RM's JWT); a cancelled meeting calls it below. Returns how many
-- ended; audited per grant with ids and codes only.
create function consent.close_meeting_grants(p_meeting_id uuid,p_reason text default 'meeting_closed') returns int
language plpgsql volatile security definer set search_path='' as $$
declare g consent.grants; n int:=0;
begin
  if p_meeting_id is null or p_reason is null or p_reason not in ('meeting_closed','meeting_cancelled') then
    raise exception 'a meeting id and meeting_closed or meeting_cancelled' using errcode='22023';
  end if;
  for g in update consent.grants set revoked_at=clock_timestamp(),end_reason=p_reason
      where until_meeting_id=p_meeting_id and basis='consent' and revoked_at is null returning * loop
    perform audit.record('consent','consent.closed','success','consent.grants',g.grant_id::text,
      jsonb_build_object('reason',p_reason,'meeting_id',p_meeting_id,'category',g.categories[1]),p_customer_id=>g.customer_id);
    n:=n+1;
  end loop;
  return n;
end $$;

create function consent.before_appointment() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if new.status='confirmed' and new.recording_notice_version is null then
    new.recording_notice_version:=(select notice_version from consent.categories where code='meeting_recording');
  end if;
  return new;
end $$;
create trigger recording_notice before insert or update of status on rm.appointments for each row execute function consent.before_appointment();

-- Confirmed: meeting recording is on under the notice (one notice-based grant per client and RM). Moved: a bound grant
-- keeps its 30-day backstop after the meeting. Cancelled: bound grants end.
create function consent.after_appointment() returns trigger language plpgsql security definer set search_path='' as $$
declare g uuid;
begin
  if new.status='confirmed' and (tg_op='INSERT' or old.status is distinct from 'confirmed') and not exists(select 1 from consent.grants x
      where x.customer_id=new.customer_id and x.purpose='rm_brief' and x.recipient=new.advisor_id::text and 'meeting_recording'=any(x.categories)
        and x.revoked_at is null and (x.expires_at is null or x.expires_at>clock_timestamp())) then
    insert into consent.grants(customer_id,purpose,recipient,categories,granted_at,scope,basis,notice_version)
      values(new.customer_id,'rm_brief',new.advisor_id::text,array['meeting_recording'],clock_timestamp(),'{}','notice',new.recording_notice_version)
      returning grant_id into g;
    perform audit.record('consent','consent.noticed','success','consent.grants',g::text,
      jsonb_build_object('category','meeting_recording','notice_version',new.recording_notice_version,'meeting_id',new.appointment_id),p_customer_id=>new.customer_id);
  end if;
  if tg_op='UPDATE' and new.status='confirmed' and new.ends_at is distinct from old.ends_at then
    update consent.grants set expires_at=new.ends_at+interval '30 days' where until_meeting_id=new.appointment_id and revoked_at is null;
  end if;
  if tg_op='UPDATE' and new.status='cancelled' and old.status<>'cancelled' then
    perform consent.close_meeting_grants(new.appointment_id,'meeting_cancelled');
  end if;
  return null;
end $$;
create trigger consent_meeting after insert or update of status,ends_at on rm.appointments for each row execute function consent.after_appointment();

-- A request she shared until: scheduled binds the grants to its meeting; declined or withdrawn ends them.
create function consent.after_meeting_request() returns trigger language plpgsql security definer set search_path='' as $$
declare g consent.grants;
begin
  if new.status='scheduled' and new.appointment_id is not null then
    update consent.grants set until_meeting_id=new.appointment_id,expires_at=(select a.ends_at+interval '30 days' from rm.appointments a where a.appointment_id=new.appointment_id)
      where until_request_id=new.request_id and revoked_at is null;
  elsif new.status in ('declined','withdrawn') then
    for g in update consent.grants set revoked_at=clock_timestamp(),end_reason='request_closed'
        where until_request_id=new.request_id and revoked_at is null returning * loop
      perform audit.record('consent','consent.closed','success','consent.grants',g.grant_id::text,
        jsonb_build_object('reason','request_closed','request_id',new.request_id,'category',g.categories[1]),p_customer_id=>g.customer_id);
    end loop;
  end if;
  return null;
end $$;
create trigger consent_meeting_request after update of status,appointment_id on rm.meeting_requests for each row
  when (new.status is distinct from old.status or new.appointment_id is distinct from old.appointment_id) execute function consent.after_meeting_request();

-- 6. Renewal notice --------------------------------------------------------------------------------------------------
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('consent.renewal_due',1,'en','in_app','Sharing ends soon','You share {category} with {rm} until {date}. To keep sharing, choose a new end date in '
   'Data sharing. If you do nothing, sharing stops then.',true),
  ('consent.granted',2,'en','in_app','Sharing choice recorded','Your relationship manager can now see what you chose, until the end you set. See Data sharing '
   'for everything you share and Activity for the receipt. You can change or stop it there at any time.',true);

-- One notice per dated grant, 7 days before it ends (ids and the end date only in the event).
create function consent.enqueue_renewals() returns int language plpgsql volatile security definer set search_path='' as $$
declare g consent.grants; n int:=0;
begin
  for g in select * from consent.grants where scope is not null and basis='consent' and until_kind='date' and revoked_at is null
      and renewal_noticed_at is null and expires_at>clock_timestamp() and expires_at<=clock_timestamp()+interval '7 days' for update skip locked loop
    insert into events.outbox(event_type,customer_id,payload,dedup_key)
      values('consent.renewal_due',g.customer_id,jsonb_build_object('grant_id',g.grant_id,'expires_at',g.expires_at),'renewal:'||g.grant_id)
      on conflict(customer_id,event_type,dedup_key) where dedup_key is not null do nothing;
    update consent.grants set renewal_noticed_at=clock_timestamp() where grant_id=g.grant_id;
    n:=n+1;
  end loop;
  if n>0 then perform audit.record('notification','notification.reminders_enqueued','success','events.outbox',null,
    jsonb_build_object('event_count',n,'kind','consent_renewal'),p_actor_type=>'system',p_actor_id=>'notification_worker'); end if;
  return n;
end $$;
alter function events.enqueue_due_reminders() rename to enqueue_reminders_before_consent;
revoke all on function events.enqueue_reminders_before_consent() from public,anon,authenticated,demobank_worker;
create function events.enqueue_due_reminders() returns int language plpgsql volatile security definer set search_path='' as $$
begin
  return events.enqueue_reminders_before_consent()+consent.enqueue_renewals();
end $$;
revoke all on function events.enqueue_due_reminders() from public,anon,authenticated;
grant execute on function events.enqueue_due_reminders() to demobank_worker;

alter function notify.deliver(events.outbox) rename to deliver_before_consent_v2;
revoke all on function notify.deliver_before_consent_v2(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; g consent.grants; template notify.templates; vars jsonb; notification uuid;
begin
  if p_event.event_type<>'consent.renewal_due' then return notify.deliver_before_consent_v2(p_event); end if;
  select * into g from consent.grants where customer_id=cid and grant_id::text=p_event.payload->>'grant_id';
  if g.grant_id is null then raise exception 'renewal event % has no owned grant',p_event.event_id using errcode='P2002'; end if;
  if g.revoked_at is not null or g.expires_at<=clock_timestamp() or g.expires_at is distinct from (p_event.payload->>'expires_at')::timestamptz then
    return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null);
  end if;
  select jsonb_build_object('category',lower(c.label),'rm',rm.display_name(a.full_name),
      'date',rm.day(((g.expires_at-interval '1 second') at time zone 'Asia/Singapore')::date))
    into vars from consent.categories c,rm.advisors a where c.code=g.categories[1] and a.advisor_id::text=g.recipient;
  select * into template from notify.templates where template_key='consent.renewal_due' and channel='in_app' and locale='en' order by version desc limit 1;
  insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
  values(cid,p_event.event_id,template.template_key,template.version,'en','P2',template.title,notify.render(template.body,vars),true,'renewal:'||g.grant_id)
  on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
  if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
  perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
    jsonb_build_object('event_id',p_event.event_id,'template',template.template_key,'version',template.version,'priority','P2'),
    p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
  return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- 7. Data sharing read -------------------------------------------------------------------------------------------------
-- Her JWT and RLS: the catalogue, her current RM, her latest active grant to that RM per category (v2 or 0037), her open
-- accounts (masked) for the scope picker and her next confirmed meeting with that RM.
create function public.customer_read_consent_sharing(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); advisor uuid; rm_name text; data jsonb; checked timestamptz:=clock_timestamp();
begin
  -- (no rm.* helpers: they are not callable with her JWT since 0089)
  select r.advisor_id,regexp_replace(a.full_name,'\s*\(DEMO\)\s*$','') into advisor,rm_name from rm.assignments r join rm.advisors a using(advisor_id)
    where r.customer_id=cid and r.ended_on is null and r.started_on<=(checked at time zone 'Asia/Singapore')::date and a.is_active order by r.started_on desc limit 1;
  select jsonb_build_object(
    'rm',case when advisor is not null then jsonb_build_object('advisor_id',advisor,'name',rm_name) end,
    'categories',(select coalesce(jsonb_agg(jsonb_build_object('code',c.code,'label',c.label,'description',c.description,'gated',c.gated,
        'default_on',c.default_on,'withdrawable',c.withdrawable,'max_months',c.max_months,'notice_version',c.notice_version,'reason',c.reason,
        'grant',(select jsonb_build_object('grant_id',g.grant_id,'legacy',g.scope is null,'scope',g.scope,'until',g.until_kind,'basis',g.basis,
            'granted_at',g.granted_at,'expires_at',g.expires_at,'until_meeting_id',g.until_meeting_id,'until_request_id',g.until_request_id,
            'notice_version',g.notice_version,'renewal_due',g.expires_at is not null and g.until_kind='date' and g.expires_at<=checked+interval '7 days')
          from consent.grants g where c.gated and advisor is not null and g.customer_id=cid and g.purpose='rm_brief' and g.recipient=advisor::text
            and c.code=any(g.categories) and g.revoked_at is null and g.granted_at<=checked and (g.expires_at is null or g.expires_at>checked)
          order by g.granted_at desc limit 1)) order by c.sort),'[]') from consent.categories c),
    'accounts',(select coalesce(jsonb_agg(jsonb_build_object('account_id',b.account_id,'type',b.account_type,'number',b.masked_account_number,
        'currency',b.currency) order by b.account_id),'[]') from banking.bank_accounts b where b.customer_id=cid and b.status<>'closed'),
    'next_meeting',(select jsonb_build_object('meeting_id',a.appointment_id,'starts_at',a.starts_at) from rm.appointments a
      where a.customer_id=cid and a.advisor_id=advisor and a.status='confirmed' and a.starts_at>checked order by a.starts_at limit 1),
    'topic_areas',to_jsonb(consent.topic_areas()),'today',(checked at time zone 'Asia/Singapore')::date) into data;
  return banking.customer_result(cid,'consent.sharing','consent.grants',data);
end $$;

-- 8. Demo seed --------------------------------------------------------------------------------------------------------
-- Michelle's 0106 grant becomes three v2 grants to Sophie: chat topics (every topic area, the 0106 grant id, so 0108's
-- tests and Today keep it), transaction insights (all accounts, 6 months) and meeting recording (notice-based).
create function consent.seed_michelle(p_at timestamptz) returns void language plpgsql volatile set search_path='' as $$
declare sophie text:='00000000-0000-4000-8000-100200000002';
begin
  update consent.grants set revoked_at=clock_timestamp(),end_reason='replaced' where customer_id=1002 and recipient=sophie and revoked_at is null
    and grant_id not in ('00000000-0000-4000-8000-100200000041','00000000-0000-4000-8000-100200000042','00000000-0000-4000-8000-100200000043')
    and categories&&array['transaction_insights','chat_topics','meeting_recording'];
  insert into consent.grants(grant_id,customer_id,purpose,recipient,categories,granted_at,expires_at,scope,until_kind,basis,notice_version) values
    ('00000000-0000-4000-8000-100200000041',1002,'rm_brief',sophie,array['chat_topics'],p_at-interval '1 day',p_at+interval '180 days',
     jsonb_build_object('topic_areas',to_jsonb(consent.topic_areas())),'date','consent','ct-2026-11'),
    ('00000000-0000-4000-8000-100200000042',1002,'rm_brief',sophie,array['transaction_insights'],p_at-interval '1 day',p_at+interval '180 days',
     '{"accounts":"all","lookback_months":6}','date','consent','ti-2026-11'),
    ('00000000-0000-4000-8000-100200000043',1002,'rm_brief',sophie,array['meeting_recording'],p_at-interval '1 day',null,'{}',null,'notice','mr-2026-11')
  on conflict (grant_id) do update set categories=excluded.categories,granted_at=excluded.granted_at,expires_at=excluded.expires_at,scope=excluded.scope,
    until_kind=excluded.until_kind,basis=excluded.basis,notice_version=excluded.notice_version,until_meeting_id=null,until_request_id=null,
    revoked_at=null,revocation_command_id=null,end_reason=null,renewal_noticed_at=null;
end $$;
select consent.seed_michelle(clock_timestamp());

-- 0108's demo reset with Michelle's grants in the v2 shape. Owner only; hosted 34_reset_meeting_demo.sql calls it.
create or replace function rm.reset_meeting_demo(p_customers bigint[] default array[1002,1007]::bigint[]) returns jsonb
language plpgsql volatile set search_path='' as $$
declare
  sophie uuid:='00000000-0000-4000-8000-100200000002'; today date:=rm.today(); at timestamptz; mon timestamptz; a rm.appointments;
  i int; meetings uuid[]; removed int; michelle timestamptz; mei_ling timestamptz[]; points int;
begin
  if p_customers is null or cardinality(p_customers)=0 or not p_customers<@array[1002,1007]::bigint[] then
    raise exception 'only the demo clients 1002 and 1007' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(sophie::text));
  select coalesce(array_agg(appointment_id),'{}') into meetings from rm.meeting_requests where customer_id=any(p_customers) and appointment_id is not null;
  delete from rm.meeting_requests where customer_id=any(p_customers);
  get diagnostics removed=row_count;
  delete from rm.appointments where appointment_id=any(meetings);
  delete from notify.inbox where customer_id=any(p_customers) and (template_key like 'meeting.%' or template_key='consent.renewal_due');
  delete from insights.talking_points where customer_id=any(p_customers);  -- evidence and Today shares go with them
  delete from insights.topic_mutes where customer_id=any(p_customers);
  delete from insights.opportunity_pauses where customer_id=any(p_customers);
  delete from insights.share_prompts where customer_id=any(p_customers);

  if 1002=any(p_customers) then
    perform consent.seed_michelle(clock_timestamp());
    for i in 0..7 loop
      at:=((today+((4-extract(isodow from today)::int+6)%7)+1+7*i)+time '15:00') at time zone 'Asia/Singapore';
      if rm.slot_problem(sophie,1002,at) is null and rm.client_week_meetings(1002,sophie,at)=0 then michelle:=at; exit; end if;
    end loop;
    if michelle is null then raise exception 'no free Thursday 15:00 for Michelle in the next 8 weeks' using errcode='P0001'; end if;
    insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose,topics)
      values(1002,sophie,michelle,michelle+interval '30 minutes','video','confirmed',
        (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='video'),'Home loan options',
        array['home_loan','term_sheet','compliance_review']) returning * into a;
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,status,respond_by,appointment_id,
        decision_id,decided_at,created_at,updated_at)
      values('00000000-0000-4000-8000-100200000071',1002,sophie,'Home loan options',array['home_loan','term_sheet','compliance_review'],'video',
        array[michelle,michelle+interval '1 day'],'scheduled',rm.next_business_day(clock_timestamp()-interval '1 day'),a.appointment_id,
        '00000000-0000-4000-8000-100200000072',clock_timestamp()-interval '20 hours',clock_timestamp()-interval '1 day',clock_timestamp()-interval '20 hours');
    perform meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,'00000000-0000-4000-8000-100200000071'::uuid,'rm'),
      'decision:00000000-0000-4000-8000-100200000072');
  end if;

  if 1007=any(p_customers) and exists(select 1 from rm.assignments where customer_id=1007 and advisor_id=sophie and ended_on is null) then
    for i in 0..7 loop
      mon:=((today+((1-extract(isodow from today)::int+6)%7)+1+7*i)+time '16:30') at time zone 'Asia/Singapore';
      if rm.slot_problem(sophie,1007,mon) is null and rm.slot_problem(sophie,1007,mon+interval '1 day') is null then
        mei_ling:=array[mon,mon+interval '1 day']; exit;
      end if;
    end loop;
    if mei_ling is null then raise exception 'no free Monday and Tuesday 16:30 for Mei Ling in the next 8 weeks' using errcode='P0001'; end if;
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by)
      values('00000000-0000-4000-8000-100700000071',1007,sophie,'Renewing my fixed deposit before it matures in December',array['fixed_deposit'],'video',
        mei_ling,rm.next_business_day(clock_timestamp()));
  end if;
  points:=insights.seed_demo_points(p_customers);
  return jsonb_build_object('removed_requests',removed,'removed_meetings',cardinality(meetings),'michelle_meeting',michelle,'mei_ling_times',to_jsonb(mei_ling),
    'talking_points',points);
end $$;

-- Grants --------------------------------------------------------------------------------------------------------------
revoke all on function consent.normal_scope(text,jsonb),consent.chained_request(text,bigint,uuid),consent.close_meeting_grants(uuid,text),
  consent.before_appointment(),consent.after_appointment(),consent.after_meeting_request(),consent.enqueue_renewals(),consent.seed_michelle(timestamptz),
  rm.consent_scope(bigint,text,uuid),rm.scoped_transactions(bigint,jsonb,timestamptz),rm.scope_json(bigint,jsonb,timestamptz)
  from public,anon,authenticated;
-- The meeting runner closes meeting-bound grants when the minutes are approved (its private login is a member).
grant usage on schema consent to demobank_meetings;
grant execute on function consent.close_meeting_grants(uuid,text) to demobank_meetings;
-- customer_read_consent_sharing runs with her JWT: the catalogue helpers are immutable lists.
revoke all on function consent.topic_areas(),consent.sensitive_areas(),consent.topic_area(text) from public,anon;
grant execute on function consent.topic_areas(),consent.sensitive_areas(),consent.topic_area(text) to authenticated;
revoke all on function public.customer_read_consent_sharing(bigint) from public,anon;
grant execute on function public.customer_read_consent_sharing(bigint) to authenticated;
revoke all on function rm.reset_meeting_demo(bigint[]) from public,anon,authenticated;
