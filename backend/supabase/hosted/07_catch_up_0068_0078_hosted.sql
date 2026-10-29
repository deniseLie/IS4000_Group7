-- Catch-up for the hosted project (29 Oct 2026). Run once in the Supabase SQL editor. It is one transaction, so if
-- anything fails nothing changes; it is safe to re-run (steps already recorded are skipped).
--
-- A check on the hosted project showed the repository's 0062, 0063, 0065, 0066, 0067 and 0069 already in place (applied
-- without being recorded) and 0064 is Alex-only, so this applies just what is missing:
--   0068 card self-service (made repeatable, in case part of it exists), 0070 merchant country on transactions and the
--   login-help article v2 (Alex's rows left out), then 0075 Michelle's demo charges and pending transfer,
--   0076 bank decisions, 0077 statements, 0078 reminder clean-up.

-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0068$
declare previous supabase_migrations.schema_migrations; script text:=$source_0068$-- Released source SHA256: 5307dd136bc6133508925ed9e676643216cf525a8265f312a9c0519020835eb3
-- Hosted copy: parts of this may already exist on the hosted project, so creates are made repeatable; the SQL is otherwise the released file.
-- Customer self-service for the customer's own synthetic card: unblock (temporarily_blocked -> active) and permanent
-- cancellation (active or temporarily_blocked -> cancelled; cancelled is terminal). Both are confirmed customer actions.
-- card.unblock and card.cancel_permanently keep their staff executors (0030): a target naming a handoff_id is a staff
-- operation (run_staff inserts it already confirmed and never reads confirmation_required); a target without one is the
-- customer's reviewed change. Unblocking is allowed even after a fraud block, so a stolen session could reverse one;
-- README and docs/workflows/workflow-1-alex.md record this trade-off. Open card handoffs get an append-only note.

create table if not exists support.handoff_notes(
  note_id uuid primary key default gen_random_uuid(),
  handoff_id uuid not null references support.handoffs(handoff_id),
  customer_id bigint not null references banking.customer_profiles(customer_id),
  command_id uuid not null references actions.commands(command_id),
  kind text not null check(kind in ('card_unblocked_by_customer','card_cancelled_by_customer')),
  card_last4 text not null check(card_last4 ~ '^[0-9]{4}$'),
  created_at timestamptz not null default clock_timestamp(),
  unique(handoff_id,command_id)
);
alter table support.handoff_notes enable row level security;
revoke all on support.handoff_notes from public,anon,authenticated;
grant select on support.handoff_notes to authenticated;
drop policy if exists handoff_notes_own on support.handoff_notes;
create policy handoff_notes_own on support.handoff_notes for select to authenticated using (customer_id=banking.my_customer_id());

create or replace function actions.normalise_card_change(p_target jsonb)
returns jsonb language plpgsql stable security definer set search_path=''
as $$
begin
  if p_target ? 'handoff_id' then return actions.normalise_staff_card(p_target); end if;
  return actions.normalise_card_target(p_target);
end $$;

-- The customer's own card, re-resolved from the session and the explicit customer scope; from_status is the optimistic check.
create or replace function actions.customer_card_change(p_target jsonb,p_customer_id bigint,p_operation text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare card banking.credit_cards;
begin
  if p_customer_id is null or p_customer_id is distinct from banking.my_customer_id() then
    raise exception 'a card change needs the signed-in customer''s own scope' using errcode='42501';
  end if;
  select * into card from banking.credit_cards where customer_id=p_customer_id and card_id=substring(p_target->>'card_id' from 6)::bigint;
  if not found then raise exception 'card is not available in this customer scope' using errcode='42501'; end if;
  if p_operation='unblock' and card.status<>'temporarily_blocked' then
    raise exception 'only a temporarily blocked synthetic card can be unblocked' using errcode='22023';
  end if;
  if p_operation='cancel' and card.status not in ('active','temporarily_blocked') then
    raise exception 'only an active or temporarily blocked synthetic card can be cancelled' using errcode='22023';
  end if;
  return jsonb_build_object('card_id',p_target->>'card_id','last4',card.last4,'from_status',card.status);
end $$;

create or replace function actions.unblock_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_target ? 'handoff_id' then return actions.staff_card_target(p_target,p_customer_id); end if;
  return actions.customer_card_change(p_target,p_customer_id,'unblock');
end $$;

create or replace function actions.cancel_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_target ? 'handoff_id' then return actions.staff_card_target(p_target,p_customer_id); end if;
  return actions.customer_card_change(p_target,p_customer_id,'cancel');
end $$;

-- One locked, confirmed customer change. A different current status records a failed receipt (P1001), nothing else.
create or replace function actions.execute_customer_card_change(p_command actions.commands,p_to text,p_kind text,p_event text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare card banking.credit_cards; noted jsonb;
begin
  if p_command.confirmed_at is null or exists(select 1 from actions.staff_commands where command_id=p_command.command_id)
      or p_command.customer_id is distinct from banking.my_customer_id() then
    raise exception 'a customer card change needs the customer''s own confirmation' using errcode='42501';
  end if;
  select * into card from banking.credit_cards where card_id=substring(p_command.target->>'card_id' from 6)::bigint
    and customer_id=p_command.customer_id and customer_id=banking.my_customer_id() for update;
  if not found or card.status is distinct from p_command.target->>'from_status' or card.last4 is distinct from p_command.target->>'last4' then
    raise exception 'the card changed after this proposal; review a new one' using errcode='P1001';
  end if;
  update banking.credit_cards set status=p_to where card_id=card.card_id and customer_id=p_command.customer_id;
  -- Staff reviewing an open case for this card see the customer's change; the handoff stays open.
  with linked as (
    select h.handoff_id from support.handoffs h where h.customer_id=p_command.customer_id and h.status in ('open','claimed')
      and h.reason in ('card_verification','card_dispute') and exists(select 1 from actions.commands c where c.case_id=h.case_id
        and c.customer_id=h.customer_id and c.type in ('card.temporary_block','card.dispute') and c.target->>'card_id'=p_command.target->>'card_id')
    for update of h),
  inserted as (
    insert into support.handoff_notes(handoff_id,customer_id,command_id,kind,card_last4)
    select handoff_id,p_command.customer_id,p_command.command_id,p_kind,card.last4 from linked returning handoff_id),
  touched as (update support.handoffs set updated_at=clock_timestamp() where handoff_id in (select handoff_id from inserted) returning handoff_id)
  select coalesce(jsonb_agg(to_jsonb(handoff_id) order by handoff_id),'[]') into noted from touched;
  perform audit.record('action','action.'||p_kind,'success','actions.commands',p_command.command_id::text,
    jsonb_build_object('handoffs_noted',jsonb_array_length(noted)),p_command.case_id);
  return jsonb_build_object('result',jsonb_build_object('card_id',p_command.target->>'card_id','last4',card.last4,'status',p_to,
    'from_status',card.status,'handoffs_noted',noted),'event_type',p_event);
end $$;

create or replace function actions.execute_card_unblock(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_command.target ? 'handoff_id' then return actions.execute_staff_unblock(p_command); end if;
  return actions.execute_customer_card_change(p_command,'active','card_unblocked_by_customer','card.unblocked_by_customer');
end $$;

create or replace function actions.execute_card_cancel(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_command.target ? 'handoff_id' then return actions.execute_staff_cancel(p_command); end if;
  return actions.execute_customer_card_change(p_command,'cancelled','card_cancelled_by_customer','card.cancelled_by_customer');
end $$;

update actions.executors set validator='actions.unblock_target(jsonb,bigint)'::regprocedure,
  executor='actions.execute_card_unblock(actions.commands)'::regprocedure,normaliser='actions.normalise_card_change(jsonb)'::regprocedure,
  confirmation_required=true,event_types=array['card.unblocked','card.unblocked_by_customer'] where type='card.unblock';
update actions.executors set validator='actions.cancel_target(jsonb,bigint)'::regprocedure,
  executor='actions.execute_card_cancel(actions.commands)'::regprocedure,normaliser='actions.normalise_card_change(jsonb)'::regprocedure,
  confirmation_required=true,event_types=array['card.cancelled','card.cancelled_by_customer'] where type='card.cancel_permanently';

-- Staff see the customer's card changes on each queued handoff (kind, masked card and time only).
create or replace function public.staff_handoff_queue(p_limit int)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare staff uuid:=support.require_staff(); result jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'staff queue limit must be 1-100' using errcode='22023'; end if;
  select coalesce(jsonb_agg(to_jsonb(h) order by h.priority,h.created_at,h.handoff_id),'[]') into result from(
    select q.handoff_id,q.customer_id,q.case_id,q.reason,q.priority,q.summary,q.status,q.claimed_by,q.created_at,q.updated_at,
      coalesce((select jsonb_agg(jsonb_build_object('kind',n.kind,'card_last4',n.card_last4,'created_at',n.created_at) order by n.created_at,n.note_id)
        from support.handoff_notes n where n.handoff_id=q.handoff_id),'[]') customer_updates
    from support.handoffs q where q.status in ('open','claimed') order by q.priority,q.created_at,q.handoff_id limit p_limit)h;
  perform audit.record('policy','policy.decided','success','support.handoffs',null,
    jsonb_build_object('tool','handoff.queue','reasons',jsonb_build_array('support_role','aal2'),'version','staff_v1'),
    p_actor_type=>'staff',p_actor_id=>staff::text);
  perform audit.record('data','data.read','success','support.handoffs',null,
    jsonb_build_object('tool','handoff.queue','result_count',jsonb_array_length(result)),p_actor_type=>'staff',p_actor_id=>staff::text);
  return result;
end $$;

revoke all on function actions.normalise_card_change(jsonb),actions.customer_card_change(jsonb,bigint,text),actions.unblock_target(jsonb,bigint),
  actions.cancel_target(jsonb,bigint),actions.execute_customer_card_change(actions.commands,text,text,text),
  actions.execute_card_unblock(actions.commands),actions.execute_card_cancel(actions.commands) from public,anon,authenticated;
$source_0068$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0068';
  if found then
    if previous.name is distinct from 'customer_card_self_service' or previous.statements is distinct from array[script] then
      raise exception 'migration 0068 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0068 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0068','customer_card_self_service',array[script]);
  end if;
end $apply_0068$;

do $apply_0070$
declare previous supabase_migrations.schema_migrations; script text:=$source_0070$-- Released source SHA256: 993aad71642b97296c3796a0b04dd854baf718f8e8495694d6dde8f99c9dcb7d
-- Hosted copy: rows for Alex (1001), removed by 06_keep_only_michelle.sql, are left out; everything else is the released file.
-- Workflow 1 Part B, revised (docs/workflows/workflow-1-alex.md): "impossible travel". On Fri 17 Oct 2026 card 4821 is used
-- in Singapore at 7:15 pm SGT and then twice in the United States at 9:42 pm and 9:58 pm SGT. The rule in fraud_records flags
-- the later charges, so each card charge now records its merchant country and the reader returns the charge time.
-- 0064's two small card-testing charges (100115/100116) are rewritten in place rather than deleted. Invented merchants; synthetic data only.

alter table banking.transactions add column merchant_country text check (merchant_country ~ '^[A-Z]{2}$');

-- Same checks, filters and order as 0028; adds the charge time and merchant country for the travel rule.
create or replace function public.customer_read_transactions(p_customer_id bigint,p_from date,p_to date,p_filters jsonb)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  if (p_from is not null and p_to is not null and p_from>p_to) or jsonb_typeof(p_filters) is distinct from 'object' then
    raise exception 'transaction reads require ordered dates and an object of filters' using errcode='22023';
  end if;
  if exists(select 1 from banking.transactions t where t.customer_id=cid and (
      (t.card_id is not null and not exists(select 1 from banking.credit_cards c where c.customer_id=cid and c.card_id=t.card_id)) or
      (t.account_id is not null and not exists(select 1 from banking.bank_accounts a where a.customer_id=cid and a.account_id=t.account_id)))) then
    raise exception 'a customer transaction references an unavailable owned account or card' using errcode='23514';
  end if;
  select coalesce(jsonb_agg(r.data order by r.txn_date desc,r.transaction_id desc),'[]') into data from(
    select t.txn_date,t.transaction_id,jsonb_build_object('txn_id','txn_'||t.transaction_id,
      'date',(t.txn_date at time zone 'Asia/Singapore')::date,'txn_at',t.txn_date,'description',t.merchant,'amount',t.amount,'currency',t.currency,
      'status',t.status,'card_id',case when c.card_id is not null then 'card_'||c.card_id end,'card_last4',c.last4,
      'is_overseas',t.is_overseas,'merchant_country',t.merchant_country,'failure_reason',t.failure_reason) data
    from banking.transactions t left join banking.credit_cards c on c.card_id=t.card_id and c.customer_id=cid
    where t.customer_id=cid and (p_from is null or t.txn_date>=p_from::timestamp at time zone 'Asia/Singapore')
      and (p_to is null or t.txn_date<(p_to+1)::timestamp at time zone 'Asia/Singapore')
      and (not(p_filters?'status') or t.status=p_filters->>'status')
      and (not(p_filters?'txn_id') or 'txn_'||t.transaction_id=p_filters->>'txn_id')
      and (not(p_filters?'card_id') or 'card_'||t.card_id=p_filters->>'card_id')
      and (not(p_filters?'currency') or t.currency=p_filters->>'currency')
      and (not(p_filters?'is_overseas') or t.is_overseas=(p_filters->>'is_overseas')::boolean)
    order by t.txn_date desc,t.transaction_id desc limit 200)r;
  return banking.customer_result(cid,'customer.get_transactions','banking.transactions',data);
end $$;

-- App login help after an unexpected one-time code: the lock is protective, reset only in the official app, never share the code.
-- The assistant has no sign-in or one-time-code records, so the guidance says so; any link to card charges comes from the customer.
update kb.articles set status='retired' where article_id='app-login-recovery' and version=1 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('app-login-recovery',2,'Mobile app login recovery',array['retail','fraud'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('app-login-recovery',2,'steps','Mobile app login recovery','If you are locked out of the mobile app: 1. Close and reopen the official app and check that it is updated. 2. On the sign-in screen choose Forgot password and follow the steps; a one-time code is sent only to your registered phone. 3. Enter that code only in the official app, never in chat, email or a phone call. 4. If the app says your access is locked after several attempts, wait for the stated time or use the official app''s Help service to unlock it. 5. If you did not try to sign in yourself, treat it as a security issue and contact your bank through its official app or website. This chat never needs your password, PIN or one-time code.','locked locked out lockout app mobile phone login log in sign in forgot password unlock access'),
  ('app-login-recovery',2,'unexpected-code','A one-time code you did not ask for','If you received a one-time code (OTP) by SMS that you did not request, someone else may have tried to sign in to your account with your details. If the app then says your account is locked for your security, the lock is protecting you: it stops further sign-in attempts and is not a penalty. If card charges you do not recognise appeared around the same time, the two are likely linked; report both to the bank. Reset your password only inside the official DemoBank app with Forgot password. Never use a link in an SMS, email or chat message, even one that looks like it is from DemoBank. Never share a one-time code with anyone, including callers, bank staff and this chat; DemoBank never asks for it. This chat cannot see sign-in attempts, devices or where a sign-in came from.','otp one-time code code sms text message did not request didnt ask weird unexpected locked for your security account locked someone else sign in attempt reset password link');
$source_0070$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0070';
  if found then
    if previous.name is distinct from 'workflow1_impossible_travel' or previous.statements is distinct from array[script] then
      raise exception 'migration 0070 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0070 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0070','workflow1_impossible_travel',array[script]);
  end if;
end $apply_0070$;

do $apply_0075$
declare previous supabase_migrations.schema_migrations; script text:=$source_0075$-- Released source SHA256: 09447ac796268fdfde43f49419b765673178231c3836ba5a33b282fde263a06f
-- Michelle's own demo activity for urgent card help (CR12) and a pending payment (CR13), so the client persona can
-- demo them without another customer. On Mon 27 Oct 2026 her Signature Card (7310) is used in Singapore at 12:40 pm SGT and
-- then twice in the United Kingdom at 2:05 pm and 2:21 pm SGT, which the impossible-travel rule flags. A transfer to her
-- interior designer is still pending. Invented merchants; synthetic data only.

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas, merchant_country) values
  (100214, 1002, null, 10021, '2026-10-27 12:40+08', 'Orchard Bistro, Singapore', 'Card purchase', -68.20, 'posted', false, 'SG'),
  (100215, 1002, null, 10021, '2026-10-27 14:05+08', 'MAYFAIR AUDIO LONDON',     'Card purchase', -1.00, 'posted', true, 'GB'),
  (100216, 1002, null, 10021, '2026-10-27 14:21+08', 'LUXEHAUS LONDON',          'Card purchase', -3850.00, 'posted', true, 'GB'),
  (100217, 1002, 10021, null, '2026-10-28 09:10+08', 'Transfer to Lim Interiors', 'FAST transfer, renovation deposit', -12000.00, 'pending', false, null)
on conflict (transaction_id) do nothing;

-- The card balance follows the new charges (1,250.40 + 68.20 + 1.00 + 3,850.00); a card changed since its seed stays untouched.
update banking.credit_cards set outstanding_balance = 5169.60 where card_id = 10021 and customer_id = 1002 and outstanding_balance = 1250.40;
$source_0075$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0075';
  if found then
    if previous.name is distinct from 'michelle_demo_activity' or previous.statements is distinct from array[script] then
      raise exception 'migration 0075 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0075 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0075','michelle_demo_activity',array[script]);
  end if;
end $apply_0075$;

do $apply_0076$
declare previous supabase_migrations.schema_migrations; script text:=$source_0076$-- Released source SHA256: baa60b6133bcf55030ba420d833a059256b8139658abcc2ff4ac31d86c6ee0fe
-- The bank's decision on a submitted document (CR3, CR6). Until the relationship-manager view exists (Workflow 2), the
-- bank's reviewer records it with one call in the SQL editor:
--   select compliance.record_bank_decision('<item_id>','accepted');
--   select compliance.record_bank_decision('<item_id>','rejected','The bill is older than 3 months.');
-- Accepting marks the item "Accepted by bank". Asking for a correction marks it "Correction requested by bank" and opens a
-- "The bank asked you" request on the client's review, which closes by itself when she submits a new file for that item.
-- Only the database owner can call it: no client, guest or assistant path can approve or reject a document.

alter table compliance.info_requests add column item_id uuid references compliance.requested_items(item_id) on delete cascade;

create function compliance.record_bank_decision(p_item_id uuid,p_decision text,p_note text default null,p_due date default null)
returns text language plpgsql security definer set search_path='' as $$
declare item compliance.requested_items; review compliance.cases;
begin
  if p_decision not in ('accepted','rejected') then raise exception 'decision must be accepted or rejected' using errcode='22023'; end if;
  if p_decision='rejected' and nullif(btrim(coalesce(p_note,'')),'') is null then
    raise exception 'say what the client needs to correct' using errcode='22023';
  end if;
  select * into item from compliance.requested_items where item_id=p_item_id for update;
  if not found then raise exception 'requested item not found' using errcode='P0002'; end if;
  select * into review from compliance.cases where case_id=item.case_id for share;
  if review.status in ('completed','closed') then raise exception 'the review is closed' using errcode='22023'; end if;
  if item.submission_status<>'submitted_awaiting_review' then
    raise exception 'only a submitted document can be decided (current: %)',item.submission_status using errcode='22023';
  end if;
  update compliance.requested_items set submission_status=p_decision,updated_at=clock_timestamp() where item_id=item.item_id;
  if p_decision='rejected' then
    insert into compliance.info_requests(case_id,customer_id,item_id,question,instructions,due_date)
    values(item.case_id,item.customer_id,item.item_id,format('Please send a new %s. %s',item.label,btrim(p_note)),
      'Upload the corrected file under Compliance review.',coalesce(p_due,review.due_date));
  end if;
  update compliance.cases set status_updated_at=clock_timestamp() where case_id=item.case_id;
  perform audit.record('document','document.bank_decided','success','compliance.requested_items',item.item_id::text,
    jsonb_build_object('decision',p_decision),p_customer_id=>item.customer_id);
  return case p_decision when 'accepted' then format('%s accepted',item.label) else format('%s: correction requested',item.label) end;
end $$;
revoke all on function compliance.record_bank_decision(uuid,text,text,date) from public,anon,authenticated;

-- A correction request is answered once the client submits a new file for that item.
create function compliance.close_answered_corrections() returns trigger language plpgsql security definer set search_path='' as $$
begin
  update compliance.info_requests set status='responded',responded_at=clock_timestamp()
  where item_id=new.item_id and customer_id=new.customer_id and status='open';
  return null;
end $$;
revoke all on function compliance.close_answered_corrections() from public,anon,authenticated;
create trigger close_answered_corrections after update of submission_status on compliance.requested_items
  for each row when (new.submission_status='submitted_awaiting_review' and old.submission_status='rejected')
  execute function compliance.close_answered_corrections();
$source_0076$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0076';
  if found then
    if previous.name is distinct from 'bank_document_decisions' or previous.statements is distinct from array[script] then
      raise exception 'migration 0076 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0076 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0076','bank_document_decisions',array[script]);
  end if;
end $apply_0076$;

do $apply_0077$
declare previous supabase_migrations.schema_migrations; script text:=$source_0077$-- Released source SHA256: a7395c06b957535183a6e945daa0358f0b0cd7c20aac39f99ee281848b766b5e
-- Statements for the client persona (CR15). Michelle's monthly statements for the last 3 full months are built in the app
-- from her own synthetic transactions (Overview → Statements), so she needs a few months of everyday activity: her
-- consulting income on the 3rd, rent on the 14th, and Signature Card (7310) spending paid off in full on the 15th. Card
-- balances follow on: each month's charges equal the next month's payment, and September's charges (1,250.40) are her card
-- balance before the 27 Oct charges in 0075. Invented merchants; synthetic data only.
--
-- Older or certified copies are a "Statement or document copy" request (0071). The statements article v2 says so and
-- replaces the generic v1 text that pointed to "your bank's official app".

insert into banking.transactions (transaction_id, customer_id, account_id, card_id, txn_date, merchant, description, amount, status, is_overseas) values
  (100218, 1002, 10021, null, '2026-07-03 09:30+08', 'Demo Consulting Clients', 'Invoice payment received', 18500.00, 'posted', false),
  (100219, 1002, 10021, null, '2026-07-14 16:10+08', 'Demo Property Mgmt', 'Rental payment', -6200.00, 'posted', false),
  (100220, 1002, 10021, null, '2026-07-15 08:00+08', 'Signature Card payment', 'Card payment, card ending 7310', -860.15, 'posted', false),
  (100221, 1002, null, 10021, '2026-07-06 18:22+08', 'Riverside Grocer', 'Card purchase', -212.35, 'posted', false),
  (100222, 1002, null, 10021, '2026-07-12 22:41+08', 'Metro Cab', 'Card purchase', -48.60, 'posted', false),
  (100223, 1002, null, 10021, '2026-07-15 08:00+08', 'Payment received, thank you', 'Card payment', 860.15, 'posted', false),
  (100224, 1002, null, 10021, '2026-07-24 20:05+08', 'Lotus Dining', 'Card purchase', -136.80, 'posted', false),
  (100225, 1002, 10021, null, '2026-08-03 09:30+08', 'Demo Consulting Clients', 'Invoice payment received', 18500.00, 'posted', false),
  (100226, 1002, 10021, null, '2026-08-14 16:10+08', 'Demo Property Mgmt', 'Rental payment', -6200.00, 'posted', false),
  (100227, 1002, 10021, null, '2026-08-15 08:00+08', 'Signature Card payment', 'Card payment, card ending 7310', -397.75, 'posted', false),
  (100228, 1002, null, 10021, '2026-08-02 11:15+08', 'Riverside Grocer', 'Card purchase', -188.90, 'posted', false),
  (100229, 1002, null, 10021, '2026-08-15 08:00+08', 'Payment received, thank you', 'Card payment', 397.75, 'posted', false),
  (100230, 1002, null, 10021, '2026-08-16 15:30+08', 'Harbour Books', 'Card purchase', -64.20, 'posted', false),
  (100231, 1002, null, 10021, '2026-08-27 19:48+08', 'Lotus Dining', 'Card purchase', -152.40, 'posted', false),
  (100232, 1002, 10021, null, '2026-09-03 09:30+08', 'Demo Consulting Clients', 'Invoice payment received', 18500.00, 'posted', false),
  (100233, 1002, 10021, null, '2026-09-14 16:10+08', 'Demo Property Mgmt', 'Rental payment', -6200.00, 'posted', false),
  (100234, 1002, 10021, null, '2026-09-15 08:00+08', 'Signature Card payment', 'Card payment, card ending 7310', -405.50, 'posted', false),
  (100235, 1002, null, 10021, '2026-09-05 10:02+08', 'Riverside Grocer', 'Card purchase', -201.15, 'posted', false),
  (100236, 1002, null, 10021, '2026-09-15 08:00+08', 'Payment received, thank you', 'Card payment', 405.50, 'posted', false),
  (100237, 1002, null, 10021, '2026-09-19 14:20+08', 'Bayfront Hotel Spa', 'Card purchase', -780.00, 'posted', false),
  (100238, 1002, null, 10021, '2026-09-26 23:10+08', 'Metro Cab', 'Card purchase', -269.25, 'posted', false)
on conflict (transaction_id) do nothing;

update kb.articles set status='retired' where article_id='statements' and version=1 and status='published';
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('statements',2,'Getting a statement',array['retail','document'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('statements',2,'main','Getting a statement','To get a monthly statement in DemoBank: 1. Open Overview. 2. Scroll to Statements. 3. Choose the account or card and the month. 4. Select View statement, then Print or save as PDF. Statements for the last three full months are available there. For an older month, a certified copy or a paper statement, raise a Statement or document copy request under My requests with the account or card, the period and the format; the operations team prepares it and you can follow it under My requests. The assistant cannot open, download or send a statement itself. These are synthetic demo statements, not real bank documents.','statement statements e-statement monthly statement download print pdf certified copy older statement');
$source_0077$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0077';
  if found then
    if previous.name is distinct from 'michelle_statements' or previous.statements is distinct from array[script] then
      raise exception 'migration 0077 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0077 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0077','michelle_statements',array[script]);
  end if;
end $apply_0077$;

do $apply_0078$
declare previous supabase_migrations.schema_migrations; script text:=$source_0078$-- Released source SHA256: fdeeeefe22e6e6414c8390164acc80ddeba09ed07b552172946527516f0cdfae
-- Reminders disappear from the Inbox once she has done what they asked (CR7). New reminders were already limited to
-- outstanding items (0060), and a queued one that went stale was already dropped at delivery; this clears the ones she
-- has already received:
--   - a deadline reminder, once no requested item on that review is still to send (none not submitted, rejected or unknown);
--   - a "the bank asked you" reminder, once that request is no longer open (for a correction: when she sends a new file, 0076).
-- Cleared reminders are hidden from the Inbox list, not deleted, so the audit trail and delivery records stay complete.

alter table notify.inbox add column cleared_at timestamptz;

create function notify.clear_answered_reminders(p_customer_id bigint,p_case_id uuid)
returns int language plpgsql volatile security definer set search_path='' as $$
declare cleared int;
begin
  update notify.inbox i set cleared_at=clock_timestamp(),read_at=coalesce(i.read_at,clock_timestamp())
  from events.outbox o
  where i.event_id=o.event_id and i.customer_id=p_customer_id and o.customer_id=p_customer_id and i.cleared_at is null
    and o.payload->>'case_id'=p_case_id::text
    and ((o.event_type='compliance.deadline_reminder'
          and not exists(select 1 from compliance.requested_items r where r.customer_id=p_customer_id and r.case_id=p_case_id
            and r.submission_status in ('not_submitted','rejected','unknown')))
      or (o.event_type='compliance.information_reminder'
          and not exists(select 1 from compliance.info_requests q where q.customer_id=p_customer_id and q.case_id=p_case_id
            and q.request_id::text=o.payload->>'request_id' and q.status='open')));
  get diagnostics cleared=row_count;
  if cleared>0 then
    perform audit.record('notification','notification.reminders_cleared','success','notify.inbox',null,
      jsonb_build_object('case_id',p_case_id,'cleared_count',cleared),p_actor_type=>'system',p_actor_id=>'notification_worker',p_customer_id=>p_customer_id);
  end if;
  return cleared;
end $$;
revoke all on function notify.clear_answered_reminders(bigint,uuid) from public,anon,authenticated;

create function notify.clear_reminders_after_item() returns trigger language plpgsql security definer set search_path='' as $$
begin perform notify.clear_answered_reminders(new.customer_id,new.case_id); return null; end $$;
revoke all on function notify.clear_reminders_after_item() from public,anon,authenticated;
create trigger clear_reminders_after_item after update of submission_status on compliance.requested_items
  for each row when (new.submission_status in ('submitted_awaiting_review','accepted') and old.submission_status is distinct from new.submission_status)
  execute function notify.clear_reminders_after_item();

create function notify.clear_reminders_after_request() returns trigger language plpgsql security definer set search_path='' as $$
begin perform notify.clear_answered_reminders(new.customer_id,new.case_id); return null; end $$;
revoke all on function notify.clear_reminders_after_request() from public,anon,authenticated;
create trigger clear_reminders_after_request after update of status on compliance.info_requests
  for each row when (old.status='open' and new.status<>'open')
  execute function notify.clear_reminders_after_request();

-- Same list as 0024, without cleared reminders.
create or replace function public.notification_list_customer(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'notification scope must match the signed-in customer' using errcode='42501';
  end if;
  select coalesce(jsonb_agg(to_jsonb(n) order by n.created_at desc,n.notification_id desc),'[]') into result
    from(select notification_id,customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,read_at,created_at
      from notify.inbox where customer_id=cid and cleared_at is null order by created_at desc,notification_id desc limit 50)n;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','notify.inbox','details',jsonb_build_object('tool','notifications.list','result_count',jsonb_array_length(result)))));
  return result;
end $$;
$source_0078$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0078';
  if found then
    if previous.name is distinct from 'clear_answered_reminders' or previous.statements is distinct from array[script] then
      raise exception 'migration 0078 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0078 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0078','clear_answered_reminders',array[script]);
  end if;
end $apply_0078$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
