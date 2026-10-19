-- Preserve canonical staff actors and the affected customer on row/action audits. Existing hash-chain rows remain unchanged.
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

create or replace function actions.execute_command(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare entry actions.executors; execution jsonb; result jsonb; outcome text:='succeeded'; event_type text; event_id bigint;
  receipt_id text:='rcpt_'||replace(p_command.command_id::text,'-','');
begin
  select * into strict entry from actions.executors where type=p_command.type;
  begin
    execute format('select %s($1)',entry.executor::regproc) into execution using p_command;
    if jsonb_typeof(execution->'result') is distinct from 'object' or jsonb_typeof(execution->'event_type') is distinct from 'string'
        or not (execution->>'event_type'=any(entry.event_types)) then
      raise exception 'executor did not return a registered event and structured result' using errcode='22023';
    end if;
    result:=execution->'result'; event_type:=execution->>'event_type';
  exception when sqlstate 'P1001' then
    result:='{"error_code":"target_changed"}'::jsonb; outcome:='failed'; event_type:='action.failed';
  end;
  update actions.commands set state=outcome where command_id=p_command.command_id and customer_id=p_command.customer_id;
  event_id:=audit.record('action',case when outcome='succeeded' then 'action.executed' else 'action.failed' end,
    case when outcome='succeeded' then 'success' else 'failed' end,'actions.commands',p_command.command_id::text,
    jsonb_build_object('type',p_command.type,'outcome',outcome),p_command.case_id,p_customer_id=>p_command.customer_id);
  insert into actions.receipts(receipt_id,command_id,customer_id,outcome,result,audit_event_id)
    values(receipt_id,p_command.command_id,p_command.customer_id,outcome,result,event_id);
  insert into events.outbox(event_type,customer_id,command_id,payload)
    values(event_type,p_command.customer_id,p_command.command_id,jsonb_build_object('command_id',p_command.command_id,'receipt_id',receipt_id));
  return actions.outcome(p_command.command_id,p_command.customer_id);
end $$;
