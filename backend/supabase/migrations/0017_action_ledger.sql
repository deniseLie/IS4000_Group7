-- Action Service transaction boundary for the public-key/user-JWT connector.
-- App roles may read their ledger through RLS, but only these narrow RPCs can write it.
alter table actions.commands drop constraint commands_state_check;
alter table actions.commands add check(state in ('proposed','confirmed','succeeded','failed','expired','rejected'));
alter table actions.receipts add column audit_event_id bigint references audit.events(event_id);
alter table events.outbox add column command_id uuid references actions.commands(command_id);
create unique index outbox_command_idx on events.outbox(command_id) where command_id is not null;

create table actions.executors (
  type text primary key,
  validator regprocedure not null,
  executor regprocedure not null,
  confirmation_required boolean not null,
  event_type text not null
);
alter table actions.executors enable row level security;
revoke all on actions.executors from public,anon,authenticated;

create function actions.require_case(p_case_id uuid)
returns bigint language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id();
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  if not exists(select 1 from conversation.cases c join conversation.sessions s using(session_id)
      where c.case_id=p_case_id and c.customer_id=cid and s.customer_id=cid and c.status='open'
        and (s.expires_at is null or s.expires_at>clock_timestamp())) then
    raise exception 'an open owned conversation case is required' using errcode='42501';
  end if;
  return cid;
end $$;

create function actions.card_block_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare card banking.credit_cards;
begin
  if jsonb_typeof(p_target->'card_id') is distinct from 'string' or (p_target->>'card_id') !~ '^card_[1-9][0-9]{0,14}$' then
    raise exception 'card.temporary_block requires a card_id from the customer card reader' using errcode='22023';
  end if;
  select * into card from banking.credit_cards where customer_id=p_customer_id and card_id=substring(p_target->>'card_id' from 6)::bigint;
  if not found then raise exception 'card is not available in this customer scope' using errcode='42501'; end if;
  return jsonb_build_object('card_id',p_target->>'card_id','last4',card.last4);
end $$;

create function actions.handoff_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'reason') is distinct from 'string' or p_target->>'reason' not in ('card_verification','general_support')
      or jsonb_typeof(p_target->'priority') is distinct from 'string' or p_target->>'priority' not in ('P0','P1','P2','P3')
      or jsonb_typeof(p_target->'summary') is distinct from 'string' or length(btrim(p_target->>'summary')) not between 1 and 1000 then
    raise exception 'handoff.create requires a registered reason, priority and redacted summary of at most 1000 characters' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_target->>'summary');
  return jsonb_build_object('reason',p_target->>'reason','priority',p_target->>'priority','summary',p_target->>'summary');
end $$;

create function actions.execute_card_block(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare card banking.credit_cards;
begin
  select * into card from banking.credit_cards where customer_id=p_command.customer_id
    and card_id=substring(p_command.target->>'card_id' from 6)::bigint for update;
  if not found then raise exception 'card is no longer in the customer scope' using errcode='P1001'; end if;
  if card.status<>'active' then raise exception 'only an active synthetic card can be temporarily blocked' using errcode='P1001'; end if;
  update banking.credit_cards set status='temporarily_blocked' where customer_id=p_command.customer_id and card_id=card.card_id;
  return jsonb_build_object('card_id',p_command.target->>'card_id','status','temporarily_blocked');
end $$;

create function actions.execute_handoff(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare handoff support.handoffs;
begin
  insert into support.handoffs(customer_id,case_id,reason,priority,summary)
    values(p_command.customer_id,p_command.case_id,p_command.target->>'reason',p_command.target->>'priority',p_command.target->>'summary')
    on conflict(case_id,reason) where status<>'closed' do nothing returning * into handoff;
  if not found then
    select * into handoff from support.handoffs where customer_id=p_command.customer_id and case_id=p_command.case_id
      and reason=p_command.target->>'reason' and status<>'closed';
  end if;
  if handoff.handoff_id is null then raise exception 'the open handoff changed; retry this command' using errcode='40001'; end if;
  perform audit.record('handoff','handoff.created','success','support.handoffs',handoff.handoff_id::text,
    jsonb_build_object('priority',handoff.priority),p_command.case_id);
  return jsonb_build_object('handoff_id',handoff.handoff_id,'priority',handoff.priority,'status',handoff.status);
end $$;

insert into actions.executors values
  ('card.temporary_block','actions.card_block_target(jsonb,bigint)'::regprocedure,'actions.execute_card_block(actions.commands)'::regprocedure,true,'card.blocked'),
  ('handoff.create','actions.handoff_target(jsonb,bigint)'::regprocedure,'actions.execute_handoff(actions.commands)'::regprocedure,false,'handoff.created');

create function actions.outcome(p_command_id uuid,p_customer_id bigint)
returns jsonb language sql stable security invoker set search_path=''
as $$
  select jsonb_build_object('command',to_jsonb(c),'receipt',(select to_jsonb(r) from actions.receipts r
    where r.command_id=c.command_id and r.customer_id=p_customer_id))
  from actions.commands c where c.command_id=p_command_id and c.customer_id=p_customer_id
$$;

-- Called only while the command row is locked. Unknown database/provider failures roll back the entire transaction.
-- The one caught code is an executor's explicit target-state conflict; its business writes roll back before the failed receipt is recorded.
create function actions.execute_command(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare entry actions.executors; result jsonb; outcome text:='succeeded'; event_id bigint; receipt_id text:='rcpt_'||replace(p_command.command_id::text,'-','');
begin
  select * into strict entry from actions.executors where type=p_command.type;
  begin
    execute format('select %s($1)',entry.executor::regproc) into result using p_command;
  exception when sqlstate 'P1001' then
    result:='{"error_code":"target_changed"}'::jsonb; outcome:='failed';
  end;
  update actions.commands set state=outcome where command_id=p_command.command_id and customer_id=p_command.customer_id;
  event_id:=audit.record('action',case when outcome='succeeded' then 'action.executed' else 'action.failed' end,
    case when outcome='succeeded' then 'success' else 'failed' end,'actions.commands',p_command.command_id::text,
    jsonb_build_object('type',p_command.type,'outcome',outcome),p_command.case_id);
  insert into actions.receipts(receipt_id,command_id,customer_id,outcome,result,audit_event_id)
    values(receipt_id,p_command.command_id,p_command.customer_id,outcome,result,event_id);
  insert into events.outbox(event_type,customer_id,command_id,payload)
    values(case when outcome='succeeded' then entry.event_type else 'action.failed' end,p_command.customer_id,p_command.command_id,
      jsonb_build_object('command_id',p_command.command_id,'receipt_id',receipt_id));
  return actions.outcome(p_command.command_id,p_command.customer_id);
end $$;

create function public.action_propose(p_command_id uuid,p_case_id uuid,p_type text,p_target jsonb,p_idempotency_key text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=actions.require_case(p_case_id); entry actions.executors; canonical jsonb; command actions.commands;
  key_parts text[]:=string_to_array(p_idempotency_key,':');
begin
  if p_command_id is null or p_type is null or p_target is null or jsonb_typeof(p_target)<>'object' or octet_length(p_target::text)>16384
      or cardinality(key_parts) is distinct from 3 or key_parts[1] is distinct from p_case_id::text
      or key_parts[2] !~ '^[A-Za-z0-9_-]{1,40}$' or key_parts[3] is distinct from p_type then
    raise exception 'action requires an id, owned case, target and case:task:type idempotency key' using errcode='22023';
  end if;
  select * into entry from actions.executors where type=p_type;
  if not found then raise exception 'action type is not registered for this customer service' using errcode='22023'; end if;
  execute format('select %s($1,$2)',entry.validator::regproc) into canonical using p_target,cid;
  -- Serialise this idempotency key, including concurrent requests with different command ids.
  perform pg_advisory_xact_lock(hashtext('actions.command'),hashtext(p_idempotency_key));
  select * into command from actions.commands where idempotency_key=p_idempotency_key for update;
  if found then
    if command.customer_id<>cid or command.case_id is distinct from p_case_id or command.type<>p_type or command.target<>canonical then
      raise exception 'idempotency key already names a different action' using errcode='23505';
    end if;
    return actions.outcome(command.command_id,cid);
  end if;
  insert into actions.commands(command_id,customer_id,case_id,type,target,idempotency_key,expires_at)
    values(p_command_id,cid,p_case_id,p_type,canonical,p_idempotency_key,clock_timestamp()+interval '5 minutes') returning * into command;
  perform audit.record('action','action.proposed','success','actions.commands',command.command_id::text,
    jsonb_build_object('type',p_type),p_case_id);
  if entry.confirmation_required then return actions.outcome(command.command_id,cid); end if;
  return actions.execute_command(command);
end $$;

create function public.action_confirm(p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command actions.commands; entry actions.executors;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  select * into command from actions.commands where command_id=p_command_id and customer_id=cid for update;
  if not found then raise exception 'command not found in this customer scope' using errcode='P0002'; end if;
  if command.state<>'proposed' then return actions.outcome(command.command_id,cid); end if;
  perform actions.require_case(command.case_id);
  if command.expires_at<=clock_timestamp() then
    update actions.commands set state='expired' where command_id=p_command_id;
    perform audit.record('action','action.expired','success','actions.commands',p_command_id::text,jsonb_build_object('type',command.type),command.case_id);
    return actions.outcome(command.command_id,cid);
  end if;
  select * into entry from actions.executors where type=command.type;
  if not found then raise exception 'command executor is no longer registered' using errcode='42501'; end if;
  update actions.commands set state='confirmed',confirmed_at=clock_timestamp() where command_id=p_command_id returning * into command;
  perform audit.record('action','action.confirmed','success','actions.commands',p_command_id::text,jsonb_build_object('type',command.type),command.case_id);
  return actions.execute_command(command);
end $$;

create function public.action_reject(p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command actions.commands; next_state text;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  select * into command from actions.commands where command_id=p_command_id and customer_id=cid for update;
  if not found then raise exception 'command not found in this customer scope' using errcode='P0002'; end if;
  if command.state<>'proposed' then return actions.outcome(command.command_id,cid); end if;
  perform actions.require_case(command.case_id);
  next_state:=case when command.expires_at<=clock_timestamp() then 'expired' else 'rejected' end;
  update actions.commands set state=next_state where command_id=p_command_id;
  perform audit.record('action','action.'||next_state,'success','actions.commands',p_command_id::text,jsonb_build_object('type',command.type),command.case_id);
  return actions.outcome(command.command_id,cid);
end $$;

-- Internal functions cannot be called directly through the Data API, even for an owned card.
revoke all on function actions.require_case(uuid),actions.card_block_target(jsonb,bigint),actions.handoff_target(jsonb,bigint),
  actions.execute_card_block(actions.commands),actions.execute_handoff(actions.commands),actions.execute_command(actions.commands),actions.outcome(uuid,bigint)
  from public,anon,authenticated;
revoke all on function public.action_propose(uuid,uuid,text,jsonb,text),public.action_confirm(uuid),public.action_reject(uuid) from public,anon;
grant execute on function public.action_propose(uuid,uuid,text,jsonb,text),public.action_confirm(uuid),public.action_reject(uuid) to authenticated;

-- A command and its receipt are read in one statement snapshot, including concurrent confirmations.
grant execute on function actions.outcome(uuid,bigint) to authenticated;
create function public.action_read_customer(p_command_id uuid,p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'ledger scope must match the signed-in customer' using errcode='42501';
  end if;
  result:=actions.outcome(p_command_id,cid);
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','actions.commands','resource_id',p_command_id::text,
    'details',jsonb_build_object('tool','actions.read','result_count',case when result is null then 0 else 1 end))));
  return result;
end $$;
create function public.action_list_customer(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'ledger scope must match the signed-in customer' using errcode='42501';
  end if;
  select coalesce(jsonb_agg(actions.outcome(c.command_id,cid) order by c.proposed_at desc),'[]'::jsonb) into result
    from(select command_id,proposed_at from actions.commands where customer_id=cid order by proposed_at desc limit 50)c;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','actions.commands','details',jsonb_build_object('tool','actions.list','result_count',jsonb_array_length(result)))));
  return result;
end $$;
revoke all on function public.action_read_customer(uuid,bigint),public.action_list_customer(bigint) from public,anon;
grant execute on function public.action_read_customer(uuid,bigint),public.action_list_customer(bigint) to authenticated;
