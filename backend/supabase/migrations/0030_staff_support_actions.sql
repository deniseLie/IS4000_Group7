-- Staff tools use a verified Auth JWT and administrator-owned app_metadata, never user_metadata or a service key.
-- Provision app_metadata.demobank_role='support' through an administrator; enrol MFA and use an aal2 token.
create function support.require_staff()
returns uuid language plpgsql stable security definer set search_path=''
as $$
begin
  if auth.uid() is null or auth.jwt()->>'role' is distinct from 'authenticated'
      or auth.jwt()#>>'{app_metadata,demobank_role}' is distinct from 'support'
      or auth.jwt()->>'aal' is distinct from 'aal2' then
    raise exception 'support tools require an administrator-assigned support role and a verified aal2 session' using errcode='42501';
  end if;
  return auth.uid();
end $$;
revoke all on function support.require_staff() from public,anon,authenticated;

-- Private provenance prevents customer proposal/confirmation RPCs from executing a staff operation.
create table actions.staff_commands(
  command_id uuid primary key references actions.commands(command_id),
  staff_id uuid not null references auth.users(id)
);
alter table actions.staff_commands enable row level security;
revoke all on actions.staff_commands from public,anon,authenticated;

create function actions.require_staff_command(p_command actions.commands)
returns uuid language plpgsql stable security definer set search_path=''
as $$
declare staff uuid:=support.require_staff();
begin
  if not exists(select 1 from actions.staff_commands where command_id=p_command.command_id and staff_id=staff) then
    raise exception 'staff commands must originate from the staff Action Service' using errcode='42501';
  end if;
  return staff;
end $$;

create function actions.normalise_staff_handoff(p_target jsonb)
returns jsonb language plpgsql stable security definer set search_path=''
as $$
begin
  perform support.require_staff();
  if jsonb_typeof(p_target->'handoff_id') is distinct from 'string'
      or (p_target->>'handoff_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'staff handoff action requires a handoff UUID' using errcode='22023';
  end if;
  return jsonb_build_object('handoff_id',(p_target->>'handoff_id')::uuid);
end $$;

create function actions.normalise_staff_card(p_target jsonb)
returns jsonb language plpgsql stable security definer set search_path=''
as $$
begin
  return actions.normalise_staff_handoff(p_target)||actions.normalise_card_target(p_target);
end $$;

create function actions.staff_handoff_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_staff_handoff(p_target);
begin
  if not exists(select 1 from support.handoffs where handoff_id=(target->>'handoff_id')::uuid and customer_id=p_customer_id) then
    raise exception 'staff handoff target does not match the recorded customer' using errcode='42501';
  end if;
  return target;
end $$;

create function actions.staff_card_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  return actions.staff_handoff_target(p_target,p_customer_id)||actions.card_block_target(p_target,p_customer_id);
end $$;

create function actions.execute_staff_claim(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare staff uuid:=actions.require_staff_command(p_command); handoff support.handoffs;
begin
  select * into handoff from support.handoffs where handoff_id=(p_command.target->>'handoff_id')::uuid
    and customer_id=p_command.customer_id and case_id=p_command.case_id for update;
  if not found or handoff.status='closed' or handoff.status='claimed' and handoff.claimed_by is distinct from staff::text then
    raise exception 'handoff is closed or already claimed by another staff member' using errcode='P1001';
  end if;
  update support.handoffs set status='claimed',claimed_by=staff::text,updated_at=clock_timestamp() where handoff_id=handoff.handoff_id;
  perform audit.record('handoff','handoff.claimed','success','support.handoffs',handoff.handoff_id::text,'{}',p_command.case_id,
    p_actor_type=>'staff',p_actor_id=>staff::text,p_customer_id=>p_command.customer_id);
  return jsonb_build_object('result',jsonb_build_object('handoff_id',handoff.handoff_id,'status','claimed'),'event_type','handoff.claimed');
end $$;

create function actions.require_claimed_handoff(p_command actions.commands)
returns support.handoffs language plpgsql volatile security definer set search_path=''
as $$
declare staff uuid:=actions.require_staff_command(p_command); handoff support.handoffs;
begin
  select * into handoff from support.handoffs where handoff_id=(p_command.target->>'handoff_id')::uuid
    and customer_id=p_command.customer_id and case_id=p_command.case_id for update;
  if not found or handoff.status<>'claimed' or handoff.claimed_by is distinct from staff::text then
    raise exception 'the handoff must be claimed by this staff member before changing its records' using errcode='P1001';
  end if;
  return handoff;
end $$;

create function actions.execute_staff_close(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare handoff support.handoffs:=actions.require_claimed_handoff(p_command); staff uuid:=support.require_staff();
begin
  update support.handoffs set status='closed',updated_at=clock_timestamp() where handoff_id=handoff.handoff_id;
  perform audit.record('handoff','handoff.closed','success','support.handoffs',handoff.handoff_id::text,'{}',p_command.case_id,
    p_actor_type=>'staff',p_actor_id=>staff::text,p_customer_id=>p_command.customer_id);
  return jsonb_build_object('result',jsonb_build_object('handoff_id',handoff.handoff_id,'status','closed'),'event_type','handoff.closed');
end $$;

create function actions.execute_staff_unblock(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare handoff support.handoffs:=actions.require_claimed_handoff(p_command); card banking.credit_cards;
begin
  select * into card from banking.credit_cards where card_id=substring(p_command.target->>'card_id' from 6)::bigint
    and customer_id=p_command.customer_id for update;
  if not found or card.status<>'temporarily_blocked' then
    raise exception 'only a temporarily blocked synthetic card can be unblocked' using errcode='P1001';
  end if;
  update banking.credit_cards set status='active' where card_id=card.card_id and customer_id=p_command.customer_id;
  return jsonb_build_object('result',jsonb_build_object('card_id',p_command.target->>'card_id','status','active'),'event_type','card.unblocked');
end $$;

create function actions.execute_staff_cancel(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare handoff support.handoffs:=actions.require_claimed_handoff(p_command); card banking.credit_cards;
begin
  select * into card from banking.credit_cards where card_id=substring(p_command.target->>'card_id' from 6)::bigint
    and customer_id=p_command.customer_id for update;
  if not found or card.status<>'temporarily_blocked' then
    raise exception 'only a temporarily blocked synthetic card can be permanently cancelled' using errcode='P1001';
  end if;
  update banking.credit_cards set status='cancelled' where card_id=card.card_id and customer_id=p_command.customer_id;
  return jsonb_build_object('result',jsonb_build_object('card_id',p_command.target->>'card_id','status','cancelled'),'event_type','card.cancelled');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('handoff.claim','actions.staff_handoff_target(jsonb,bigint)'::regprocedure,'actions.execute_staff_claim(actions.commands)'::regprocedure,false,
    'actions.normalise_staff_handoff(jsonb)'::regprocedure,array['handoff.claimed']),
  ('handoff.close','actions.staff_handoff_target(jsonb,bigint)'::regprocedure,'actions.execute_staff_close(actions.commands)'::regprocedure,false,
    'actions.normalise_staff_handoff(jsonb)'::regprocedure,array['handoff.closed']),
  ('card.unblock','actions.staff_card_target(jsonb,bigint)'::regprocedure,'actions.execute_staff_unblock(actions.commands)'::regprocedure,false,
    'actions.normalise_staff_card(jsonb)'::regprocedure,array['card.unblocked']),
  ('card.cancel_permanently','actions.staff_card_target(jsonb,bigint)'::regprocedure,'actions.execute_staff_cancel(actions.commands)'::regprocedure,false,
    'actions.normalise_staff_card(jsonb)'::regprocedure,array['card.cancelled']);

-- Type and executor are assigned by the four separate public functions, never by model arguments.
create function actions.run_staff(p_command_id uuid,p_handoff_id uuid,p_type text,p_target jsonb)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare staff uuid:=support.require_staff(); handoff support.handoffs; command actions.commands; entry actions.executors;
  canonical jsonb; previous_target jsonb; result jsonb;
begin
  if p_command_id is null or p_handoff_id is null or p_type not in ('handoff.claim','handoff.close','card.unblock','card.cancel_permanently') then
    raise exception 'staff actions require a command id, handoff id and registered operation' using errcode='22023';
  end if;
  select * into strict entry from actions.executors where type=p_type;
  execute format('select %s($1)',entry.normaliser::regproc) into canonical using p_target;
  if canonical->>'handoff_id' is distinct from p_handoff_id::text then
    raise exception 'staff action target must identify the requested handoff' using errcode='22023';
  end if;
  perform pg_advisory_xact_lock(hashtext('actions.staff'),hashtext(p_command_id::text));
  select * into command from actions.commands where command_id=p_command_id for update;
  if found then
    if not exists(select 1 from actions.staff_commands where command_id=p_command_id and staff_id=staff) then
      raise exception 'command id is not owned by this staff member' using errcode='42501';
    end if;
    execute format('select %s($1)',entry.normaliser::regproc) into previous_target using command.target;
    if command.type<>p_type or previous_target<>canonical then
      raise exception 'staff command id already names a different operation or target' using errcode='23505';
    end if;
    return actions.outcome(command.command_id,command.customer_id);
  end if;
  select * into handoff from support.handoffs where handoff_id=p_handoff_id for update;
  if not found or handoff.customer_id is null or handoff.case_id is null then
    raise exception 'handoff with a recorded customer and conversation case was not found' using errcode='P0002';
  end if;
  execute format('select %s($1,$2)',entry.validator::regproc) into canonical using canonical,handoff.customer_id;
  insert into actions.commands(command_id,customer_id,case_id,type,target,idempotency_key,state,expires_at,confirmed_at)
    values(p_command_id,handoff.customer_id,handoff.case_id,p_type,canonical,
      handoff.case_id::text||':staff_'||replace(p_command_id::text,'-','')||':'||p_type,'confirmed',
      clock_timestamp()+interval '5 minutes',clock_timestamp()) returning * into command;
  insert into actions.staff_commands values(p_command_id,staff);
  perform audit.record('policy','policy.decided','success','actions.commands',p_command_id::text,
    jsonb_build_object('tool',p_type,'reasons',jsonb_build_array('support_role','aal2'),'version','staff_v1'),handoff.case_id,
    p_actor_type=>'staff',p_actor_id=>staff::text,p_customer_id=>handoff.customer_id);
  result:=actions.execute_command(command);
  perform audit.record('action','action.staff_recorded','success','actions.commands',p_command_id::text,
    jsonb_build_object('type',p_type,'outcome',result#>>'{command,state}'),handoff.case_id,
    p_actor_type=>'staff',p_actor_id=>staff::text,p_customer_id=>handoff.customer_id);
  return result;
end $$;

create function public.staff_claim_handoff(p_command_id uuid,p_handoff_id uuid)
returns jsonb language sql volatile security definer set search_path=''
as $$ select actions.run_staff(p_command_id,p_handoff_id,'handoff.claim',jsonb_build_object('handoff_id',p_handoff_id)) $$;
create function public.staff_close_handoff(p_command_id uuid,p_handoff_id uuid)
returns jsonb language sql volatile security definer set search_path=''
as $$ select actions.run_staff(p_command_id,p_handoff_id,'handoff.close',jsonb_build_object('handoff_id',p_handoff_id)) $$;
create function public.staff_unblock_card(p_command_id uuid,p_handoff_id uuid,p_card_id text)
returns jsonb language sql volatile security definer set search_path=''
as $$ select actions.run_staff(p_command_id,p_handoff_id,'card.unblock',jsonb_build_object('handoff_id',p_handoff_id,'card_id',p_card_id)) $$;
create function public.staff_cancel_card(p_command_id uuid,p_handoff_id uuid,p_card_id text)
returns jsonb language sql volatile security definer set search_path=''
as $$ select actions.run_staff(p_command_id,p_handoff_id,'card.cancel_permanently',jsonb_build_object('handoff_id',p_handoff_id,'card_id',p_card_id)) $$;

create function public.staff_handoff_queue(p_limit int)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare staff uuid:=support.require_staff(); result jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'staff queue limit must be 1-100' using errcode='22023'; end if;
  select coalesce(jsonb_agg(to_jsonb(h) order by h.priority,h.created_at,h.handoff_id),'[]') into result from(
    select handoff_id,customer_id,case_id,reason,priority,summary,status,claimed_by,created_at,updated_at
    from support.handoffs where status in ('open','claimed') order by priority,created_at,handoff_id limit p_limit)h;
  perform audit.record('data','data.read','success','support.handoffs',null,
    jsonb_build_object('tool','handoff.queue','result_count',jsonb_array_length(result)),p_actor_type=>'staff',p_actor_id=>staff::text);
  return result;
end $$;

revoke all on function actions.require_staff_command(actions.commands),actions.normalise_staff_handoff(jsonb),actions.normalise_staff_card(jsonb),
  actions.staff_handoff_target(jsonb,bigint),actions.staff_card_target(jsonb,bigint),actions.execute_staff_claim(actions.commands),
  actions.require_claimed_handoff(actions.commands),actions.execute_staff_close(actions.commands),actions.execute_staff_unblock(actions.commands),
  actions.execute_staff_cancel(actions.commands),actions.run_staff(uuid,uuid,text,jsonb) from public,anon,authenticated;
revoke all on function public.staff_claim_handoff(uuid,uuid),public.staff_close_handoff(uuid,uuid),public.staff_unblock_card(uuid,uuid,text),
  public.staff_cancel_card(uuid,uuid,text),public.staff_handoff_queue(int) from public,anon;
grant execute on function public.staff_claim_handoff(uuid,uuid),public.staff_close_handoff(uuid,uuid),public.staff_unblock_card(uuid,uuid,text),
  public.staff_cancel_card(uuid,uuid,text),public.staff_handoff_queue(int) to authenticated;
