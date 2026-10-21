-- Executors return an explicit business result and event type. Reusing an open handoff is not another creation.
alter table actions.executors add column event_types text[];
update actions.executors set event_types=array[event_type];
update actions.executors set event_types=array['handoff.created','handoff.reused'] where type='handoff.create';
alter table actions.executors alter column event_types set not null;
alter table actions.executors drop column event_type;

create or replace function actions.execute_card_block(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare card banking.credit_cards;
begin
  select * into card from banking.credit_cards where customer_id=p_command.customer_id
    and card_id=substring(p_command.target->>'card_id' from 6)::bigint for update;
  if not found then raise exception 'card is no longer in the customer scope' using errcode='P1001'; end if;
  if card.status<>'active' then raise exception 'only an active synthetic card can be temporarily blocked' using errcode='P1001'; end if;
  update banking.credit_cards set status='temporarily_blocked' where customer_id=p_command.customer_id and card_id=card.card_id;
  return jsonb_build_object('result',jsonb_build_object('card_id',p_command.target->>'card_id','status','temporarily_blocked'),'event_type','card.blocked');
end $$;

create or replace function actions.execute_handoff(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare handoff support.handoffs; created boolean;
begin
  insert into support.handoffs(customer_id,case_id,reason,priority,summary)
    values(p_command.customer_id,p_command.case_id,p_command.target->>'reason',p_command.target->>'priority',p_command.target->>'summary')
    on conflict(case_id,reason) where status<>'closed' do nothing returning * into handoff;
  created:=found;
  if not created then
    select * into handoff from support.handoffs where customer_id=p_command.customer_id and case_id=p_command.case_id
      and reason=p_command.target->>'reason' and status<>'closed';
  end if;
  if handoff.handoff_id is null then raise exception 'the open handoff changed; retry this command' using errcode='40001'; end if;
  if created then
    perform audit.record('handoff','handoff.created','success','support.handoffs',handoff.handoff_id::text,
      jsonb_build_object('priority',handoff.priority),p_command.case_id);
  end if;
  return jsonb_build_object('result',jsonb_build_object('handoff_id',handoff.handoff_id,'priority',handoff.priority,'status',handoff.status,'created',created),
    'event_type',case when created then 'handoff.created' else 'handoff.reused' end);
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
    jsonb_build_object('type',p_command.type,'outcome',outcome),p_command.case_id);
  insert into actions.receipts(receipt_id,command_id,customer_id,outcome,result,audit_event_id)
    values(receipt_id,p_command.command_id,p_command.customer_id,outcome,result,event_id);
  insert into events.outbox(event_type,customer_id,command_id,payload)
    values(event_type,p_command.customer_id,p_command.command_id,jsonb_build_object('command_id',p_command.command_id,'receipt_id',receipt_id));
  return actions.outcome(p_command.command_id,p_command.customer_id);
end $$;
