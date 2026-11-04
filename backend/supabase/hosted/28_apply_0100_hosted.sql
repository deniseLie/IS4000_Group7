-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0100$
declare previous supabase_migrations.schema_migrations; script text:=$source_0100$-- Released source SHA256: 0f13ce6890d001b4393735bac3784f848fe1d96138f9178ff1866f55f226db4a
-- v5 offers on the Action Service (docs/planning/v5-build-plan.md, phase 4). Synthetic data only.
--
-- An offer is a proposed command in the existing ledger, with no new offers table:
--   1. actions.commands gains offer_group, group_order and turn_id. One group is everything one reply offers; it is
--      confirmed or cancelled as a whole, in group_order.
--   2. public.action_offer proposes like action_propose but never executes (every agent write needs the client's
--      confirmation, even an automatic type such as handoff.create) and takes a TTL (offers use 30 minutes;
--      action_propose keeps its 5).
--   3. public.customer_open_offers(conversation) returns the client's own unexpired offers in that conversation.
--   4. public.offer_group_confirm confirms a group in order under one lock, so a double confirm records once. A later
--      target that carries "after_receipt": null receives the previous receipt id. If one step does not succeed the
--      rest are not run: they expire with it, or fail with an earlier_offer_failed receipt.
--      public.offer_group_cancel rejects the open offers of a group.
--   5. Commands and agent workflow turns still paused for a decision (v4 interrupt/resume) are expired.

alter table actions.commands add column offer_group uuid, add column group_order smallint, add column turn_id uuid references conversation.turns(turn_id) on delete set null;
alter table actions.commands add constraint commands_offer_group_check check((offer_group is null)=(group_order is null) and (group_order is null or group_order between 1 and 10));
create unique index commands_offer_group_order on actions.commands(offer_group,group_order) where offer_group is not null;

create function public.action_offer(p_command_id uuid,p_case_id uuid,p_type text,p_target jsonb,p_idempotency_key text,
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
  insert into actions.commands(command_id,customer_id,case_id,type,target,idempotency_key,expires_at,offer_group,group_order,turn_id)
    values(p_command_id,cid,p_case_id,p_type,canonical,p_idempotency_key,clock_timestamp()+make_interval(mins=>p_ttl_minutes),
      p_offer_group,p_group_order,p_turn_id) returning * into command;
  perform audit.record('action','action.proposed','success','actions.commands',command.command_id::text,
    jsonb_build_object('type',p_type,'offer_group',p_offer_group,'group_order',p_group_order),p_case_id);
  return actions.outcome(command.command_id,cid);
end $$;

create function public.customer_open_offers(p_conversation_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('command_id',c.command_id,'offer_group',c.offer_group,'group_order',c.group_order,
      'type',c.type,'target',c.target,'case_id',c.case_id,'turn_id',c.turn_id,'proposed_at',c.proposed_at,'expires_at',c.expires_at)
      order by c.proposed_at desc,c.offer_group,c.group_order),'[]'::jsonb) into result
    from actions.commands c join conversation.cases k on k.case_id=c.case_id
    where c.customer_id=cid and k.customer_id=cid and k.session_id=p_conversation_id
      and c.offer_group is not null and c.state='proposed' and c.expires_at>clock_timestamp();
  perform audit.record('data','data.read','success','actions.commands',null,
    jsonb_build_object('tool','offers.open','result_count',jsonb_array_length(result)));
  return result;
end $$;

create function public.offer_group_confirm(p_offer_group uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command actions.commands; result jsonb; outcomes jsonb:='[]'::jsonb;
  previous_receipt text; stopped text; event_id bigint;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  -- Lock the whole group first, in order, so a second confirm waits and then finds every step already decided.
  perform 1 from actions.commands where offer_group=p_offer_group and customer_id=cid order by group_order for update;
  if not found then raise exception 'offer group not found in this customer scope' using errcode='P0002'; end if;
  for command in select * from actions.commands where offer_group=p_offer_group and customer_id=cid order by group_order loop
    if stopped is not null and command.state='proposed' then
      if stopped='expired' or command.expires_at<=clock_timestamp() then
        update actions.commands set state='expired' where command_id=command.command_id;
        perform audit.record('action','action.expired','success','actions.commands',command.command_id::text,jsonb_build_object('type',command.type),command.case_id);
      else
        update actions.commands set state='failed' where command_id=command.command_id;
        event_id:=audit.record('action','action.failed','failed','actions.commands',command.command_id::text,
          jsonb_build_object('type',command.type,'outcome','failed','reason','earlier_offer_failed'),command.case_id);
        insert into actions.receipts(receipt_id,command_id,customer_id,outcome,result,audit_event_id)
          values('rcpt_'||replace(command.command_id::text,'-',''),command.command_id,cid,'failed','{"error_code":"earlier_offer_failed"}'::jsonb,event_id);
      end if;
      outcomes:=outcomes||jsonb_build_array(actions.outcome(command.command_id,cid));
      continue;
    end if;
    if command.state='proposed' and previous_receipt is not null and command.target ? 'after_receipt' and command.target->'after_receipt'='null'::jsonb then
      update actions.commands set target=jsonb_set(target,'{after_receipt}',to_jsonb(previous_receipt)) where command_id=command.command_id;
    end if;
    result:=public.action_confirm(command.command_id);
    outcomes:=outcomes||jsonb_build_array(result);
    if result->'command'->>'state'<>'succeeded' then stopped:=coalesce(result->'command'->>'state','failed'); end if;
    previous_receipt:=result->'receipt'->>'receipt_id';
  end loop;
  return outcomes;
end $$;

create function public.offer_group_cancel(p_offer_group uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command_id uuid; outcomes jsonb:='[]'::jsonb;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  perform 1 from actions.commands where offer_group=p_offer_group and customer_id=cid order by group_order for update;
  if not found then raise exception 'offer group not found in this customer scope' using errcode='P0002'; end if;
  for command_id in select c.command_id from actions.commands c where c.offer_group=p_offer_group and c.customer_id=cid order by c.group_order loop
    outcomes:=outcomes||jsonb_build_array(public.action_reject(command_id));
  end loop;
  return outcomes;
end $$;

revoke all on function public.action_offer(uuid,uuid,text,jsonb,text,integer,uuid,smallint,uuid),public.customer_open_offers(uuid),
  public.offer_group_confirm(uuid),public.offer_group_cancel(uuid) from public,anon;
grant execute on function public.action_offer(uuid,uuid,text,jsonb,text,integer,uuid,smallint,uuid),public.customer_open_offers(uuid),
  public.offer_group_confirm(uuid),public.offer_group_cancel(uuid) to authenticated;

-- v5 has no paused turns: expire every decision still waiting on a v4 workflow, and cancel those workflows.
do $expire$
declare command actions.commands;
begin
  for command in select c.* from actions.commands c join actions.workflow_commands w using(command_id) where c.state='proposed' for update of c loop
    update actions.commands set state='expired' where command_id=command.command_id;
    perform audit.record('action','action.expired','success','actions.commands',command.command_id::text,
      jsonb_build_object('type',command.type,'reason','v5_no_paused_turns'),command.case_id);
  end loop;
  update conversation.agent_workflows set state='cancelled',error_code='cancelled',ready=false,resume_owner=null,lease_until=null
    where state='awaiting_confirmation';
end $expire$;
$source_0100$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0100';
  if found then
    if previous.name is distinct from 'offer_groups' or previous.statements is distinct from array[script] then
      raise exception 'migration 0100 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0100 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0100','offer_groups',array[script]);
  end if;
end $apply_0100$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
