-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0101$
declare previous supabase_migrations.schema_migrations; script text:=$source_0101$-- Released source SHA256: db28e19304a446ebfa8c5d7838949c6dc22b0f60e2bb3c9472995084328b0dd9
-- v5 agent dependencies (docs/planning/v5-build-plan.md, phase 7). Synthetic data only.
--
--   1. travel_notice: a customer-raisable request category (card, trip dates, optional destination). The generic ticket
--      executor (0071 actions.ticket_target/execute_ticket) checks the card and the date formats; a trigger checks the
--      trip itself: it ends on or after it starts and lasts at most 180 days.
--   2. public.action_offer keeps a chained offer's "after_receipt": null through the type's validator, so
--      offer_group_confirm (0100) gives it the previous step's receipt. Everything else is unchanged from 0100.

insert into support.request_categories(category,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable) values
 ('travel_notice','Travel notice','Tell the bank you will use your card abroad, so overseas charges on those dates are expected.','Cards and fraud team',
  'No decision: the cards and fraud team records your trip. It does not change your limit or unblock a card.',24,
  '[{"key":"card","label":"Card (last 4 digits)","kind":"card","required":true},
    {"key":"from_date","label":"Leaving on","kind":"date","required":true},
    {"key":"to_date","label":"Back on","kind":"date","required":true},
    {"key":"destination","label":"Where you are going","kind":"text","required":false}]',
  '{}',null,true);

create function support.check_travel_notice()
returns trigger language plpgsql set search_path='' as $$
begin
  if new.category='travel_notice' and ((new.details->>'to_date')::date<(new.details->>'from_date')::date
      or (new.details->>'to_date')::date-(new.details->>'from_date')::date>180) then
    raise exception 'a travel notice ends on or after it starts and lasts at most 180 days' using errcode='22023';
  end if;
  return new;
end $$;
create trigger tickets_travel_notice before insert or update of details on support.tickets
  for each row when (new.category='travel_notice') execute function support.check_travel_notice();

create or replace function public.action_offer(p_command_id uuid,p_case_id uuid,p_type text,p_target jsonb,p_idempotency_key text,
  p_ttl_minutes integer,p_offer_group uuid,p_group_order smallint,p_turn_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); entry actions.executors; canonical jsonb; previous_target jsonb; command actions.commands;
  key_parts text[]:=string_to_array(p_idempotency_key,':');
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  if p_command_id is null or p_type is null or p_target is null or jsonb_typeof(p_target)<>'object' or octet_length(p_target::text)>16384
      or cardinality(key_parts) is distinct from 3 or key_parts[1] is distinct from p_case_id::text
      or key_parts[2] !~ '^[A-Za-z0-9_-]{1,40}$' or key_parts[3] is distinct from p_type
      or p_ttl_minutes is null or p_ttl_minutes not between 1 and 60 or p_offer_group is null or p_group_order is null or p_group_order not between 1 and 10 then
    raise exception 'an offer requires an id, owned case, target, case:task:type key, a 1-60 minute TTL and a group position' using errcode='22023';
  end if;
  select * into entry from actions.executors where type=p_type;
  if not found then raise exception 'action type is not registered for this customer service' using errcode='22023'; end if;
  execute format('select %s($1)',entry.normaliser::regproc) into canonical using p_target;
  perform pg_advisory_xact_lock(hashtext('actions.command'),hashtext(p_idempotency_key));
  select * into command from actions.commands where idempotency_key=p_idempotency_key for update;
  if found then
    if command.customer_id<>cid then raise exception 'command belongs to another customer scope' using errcode='42501'; end if;
    execute format('select %s($1)',entry.normaliser::regproc) into previous_target using command.target;
    if command.case_id is distinct from p_case_id or command.type<>p_type or previous_target<>canonical
        or command.offer_group is distinct from p_offer_group or command.group_order is distinct from p_group_order then
      raise exception 'idempotency key already names a different action' using errcode='23505';
    end if;
    if command.state='proposed' and command.expires_at<=clock_timestamp() then
      update actions.commands set state='expired' where command_id=command.command_id;
      perform audit.record('action','action.expired','success','actions.commands',command.command_id::text,jsonb_build_object('type',command.type),command.case_id);
    end if;
    return actions.outcome(command.command_id,cid);
  end if;
  perform actions.require_case(p_case_id);
  if p_turn_id is not null and not exists(select 1 from conversation.turns where turn_id=p_turn_id and case_id=p_case_id and customer_id=cid) then
    raise exception 'the offer turn must belong to the owned case' using errcode='42501';
  end if;
  if exists(select 1 from actions.commands where offer_group=p_offer_group
      and (customer_id<>cid or case_id is distinct from p_case_id or turn_id is distinct from p_turn_id)) then
    raise exception 'an offer group belongs to one customer, case and turn' using errcode='42501';
  end if;
  execute format('select %s($1,$2)',entry.validator::regproc) into canonical using canonical,cid;
  -- 0101: a chained offer keeps its after_receipt mark through the type's validator (offer_group_confirm fills it).
  if p_target->'after_receipt'='null'::jsonb then canonical:=canonical||'{"after_receipt":null}'::jsonb; end if;
  insert into actions.commands(command_id,customer_id,case_id,type,target,idempotency_key,expires_at,offer_group,group_order,turn_id)
    values(p_command_id,cid,p_case_id,p_type,canonical,p_idempotency_key,clock_timestamp()+make_interval(mins=>p_ttl_minutes),
      p_offer_group,p_group_order,p_turn_id) returning * into command;
  perform audit.record('action','action.proposed','success','actions.commands',command.command_id::text,
    jsonb_build_object('type',p_type,'offer_group',p_offer_group,'group_order',p_group_order),p_case_id);
  return actions.outcome(command.command_id,cid);
end $$;
$source_0101$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0101';
  if found then
    if previous.name is distinct from 'travel_notice' or previous.statements is distinct from array[script] then
      raise exception 'migration 0101 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0101 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0101','travel_notice',array[script]);
  end if;
end $apply_0101$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
