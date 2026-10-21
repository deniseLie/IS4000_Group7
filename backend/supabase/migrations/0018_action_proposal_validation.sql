-- Canonicalise before duplicate lookup without re-reading a target that may have changed or disappeared.
-- A completed command remains replayable from its receipt; new commands still verify current ownership.
create function actions.normalise_card_target(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'card_id') is distinct from 'string' or (p_target->>'card_id') !~ '^card_[1-9][0-9]{0,14}$' then
    raise exception 'card.temporary_block requires a card_id from the customer card reader' using errcode='22023';
  end if;
  return jsonb_build_object('card_id',p_target->>'card_id');
end $$;

create function actions.normalise_handoff_target(p_target jsonb)
returns jsonb language plpgsql volatile set search_path=''
as $$
declare result jsonb:=actions.handoff_target(p_target,0);
begin
  if result->>'summary' ~ '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+)' then
    raise exception 'remove credentials from the handoff summary' using errcode='22023';
  end if;
  return result;
end $$;

alter table actions.executors add column normaliser regprocedure;
update actions.executors set normaliser='actions.normalise_card_target(jsonb)'::regprocedure where type='card.temporary_block';
update actions.executors set normaliser='actions.normalise_handoff_target(jsonb)'::regprocedure where type='handoff.create';
alter table actions.executors alter column normaliser set not null;

create or replace function public.action_propose(p_command_id uuid,p_case_id uuid,p_type text,p_target jsonb,p_idempotency_key text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); entry actions.executors; canonical jsonb; previous_target jsonb; command actions.commands;
  key_parts text[]:=string_to_array(p_idempotency_key,':');
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  if p_command_id is null or p_type is null or p_target is null or jsonb_typeof(p_target)<>'object' or octet_length(p_target::text)>16384
      or cardinality(key_parts) is distinct from 3 or key_parts[1] is distinct from p_case_id::text
      or key_parts[2] !~ '^[A-Za-z0-9_-]{1,40}$' or key_parts[3] is distinct from p_type then
    raise exception 'action requires an id, owned case, target and case:task:type idempotency key' using errcode='22023';
  end if;
  select * into entry from actions.executors where type=p_type;
  if not found then raise exception 'action type is not registered for this customer service' using errcode='22023'; end if;
  execute format('select %s($1)',entry.normaliser::regproc) into canonical using p_target;
  perform pg_advisory_xact_lock(hashtext('actions.command'),hashtext(p_idempotency_key));
  select * into command from actions.commands where idempotency_key=p_idempotency_key for update;
  if found then
    if command.customer_id<>cid then raise exception 'command belongs to another customer scope' using errcode='42501'; end if;
    execute format('select %s($1)',entry.normaliser::regproc) into previous_target using command.target;
    if command.case_id is distinct from p_case_id or command.type<>p_type or previous_target<>canonical then
      raise exception 'idempotency key already names a different action' using errcode='23505';
    end if;
    if command.state='proposed' and command.expires_at<=clock_timestamp() then
      update actions.commands set state='expired' where command_id=command.command_id;
      perform audit.record('action','action.expired','success','actions.commands',command.command_id::text,jsonb_build_object('type',command.type),command.case_id);
    end if;
    return actions.outcome(command.command_id,cid);
  end if;
  perform actions.require_case(p_case_id);
  execute format('select %s($1,$2)',entry.validator::regproc) into canonical using canonical,cid;
  insert into actions.commands(command_id,customer_id,case_id,type,target,idempotency_key,expires_at)
    values(p_command_id,cid,p_case_id,p_type,canonical,p_idempotency_key,clock_timestamp()+interval '5 minutes') returning * into command;
  perform audit.record('action','action.proposed','success','actions.commands',command.command_id::text,jsonb_build_object('type',p_type),p_case_id);
  if entry.confirmation_required then return actions.outcome(command.command_id,cid); end if;
  return actions.execute_command(command);
end $$;
revoke all on function actions.normalise_card_target(jsonb),actions.normalise_handoff_target(jsonb) from public,anon,authenticated;
