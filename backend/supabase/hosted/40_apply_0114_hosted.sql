-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0114$
declare previous supabase_migrations.schema_migrations; script text:=$source_0114$-- Released source SHA256: 53943243f3266533f925bbcf046616ced8610a2b5a0fffdbe526aa9cb924af1e
-- Consent v2, part 6 (RM copilot build plan phase 6e): switches inside an offer group.
-- A meeting request in chat may carry "share until this request is answered" consent grants (consent.grant, until
-- 'request', chained after the meeting.request). They are optional: off unless she switches them on. Confirming the group
-- with p_include runs the steps she kept and rejects the optional ones she left off, under the same group lock, so a left-out
-- grant is recorded as her no (action.rejected) and never stops the steps after it. A typed yes keeps none of them.
-- offer_group_confirm(uuid) (0100) is unchanged except that a step already rejected is reported and skipped, and an optional
-- step that fails (she already shares that category, say) neither stops the steps after it nor becomes their receipt.

create or replace function public.offer_group_confirm(p_offer_group uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command actions.commands; result jsonb; outcomes jsonb:='[]'::jsonb;
  previous_receipt text; stopped text; event_id bigint;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  perform 1 from actions.commands where offer_group=p_offer_group and customer_id=cid order by group_order for update;
  if not found then raise exception 'offer group not found in this customer scope' using errcode='P0002'; end if;
  for command in select * from actions.commands where offer_group=p_offer_group and customer_id=cid order by group_order loop
    if command.state='rejected' then  -- 0114: a step she left out stays out and does not stop the rest
      outcomes:=outcomes||jsonb_build_array(actions.outcome(command.command_id,cid));
      continue;
    end if;
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
    -- 0114: an optional step that does not succeed stops nothing, and the next step chains to the last receipt that did.
    if result->'command'->>'state'<>'succeeded' and not actions.optional_offer(command) then stopped:=coalesce(result->'command'->>'state','failed'); end if;
    if result->'command'->>'state'='succeeded' or not actions.optional_offer(command) then previous_receipt:=result->'receipt'->>'receipt_id'; end if;
  end loop;
  return outcomes;
end $$;

-- An optional step: a consent grant that waits for the meeting request before it (until 'request'). Only these can be left off.
create function actions.optional_offer(p_command actions.commands) returns boolean language sql immutable set search_path='' as $$
  select p_command.type='consent.grant' and p_command.target->>'until'='request' and p_command.group_order>1
$$;

create function public.offer_group_confirm(p_offer_group uuid,p_include uuid[])
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command actions.commands;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  perform 1 from actions.commands where offer_group=p_offer_group and customer_id=cid order by group_order for update;
  if not found then raise exception 'offer group not found in this customer scope' using errcode='P0002'; end if;
  if exists(select 1 from unnest(coalesce(p_include,'{}'::uuid[])) i(command_id) where not exists(select 1 from actions.commands c
      where c.command_id=i.command_id and c.offer_group=p_offer_group and c.customer_id=cid and actions.optional_offer(c))) then
    raise exception 'only optional steps of this offer group can be switched on' using errcode='22023';
  end if;
  for command in select * from actions.commands where offer_group=p_offer_group and customer_id=cid and state='proposed'
      and expires_at>clock_timestamp() and not (command_id=any(coalesce(p_include,'{}'::uuid[]))) order by group_order loop
    if actions.optional_offer(command) then perform public.action_reject(command.command_id); end if;
  end loop;
  return public.offer_group_confirm(p_offer_group);
end $$;

revoke all on function actions.optional_offer(actions.commands) from public,anon,authenticated;
revoke all on function public.offer_group_confirm(uuid,uuid[]) from public,anon;
grant execute on function public.offer_group_confirm(uuid,uuid[]) to authenticated;
$source_0114$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0114';
  if found then
    if previous.name is distinct from 'offer_switches' or previous.statements is distinct from array[script] then
      raise exception 'migration 0114 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0114 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0114','offer_switches',array[script]);
  end if;
end $apply_0114$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
