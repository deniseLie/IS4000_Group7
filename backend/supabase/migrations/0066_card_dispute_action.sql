-- Workflow 1 Part B: a confirmed dispute draft lists owned posted charges on one synthetic card for bank staff.
-- The customer answers "Do you still have the card?" when confirming; staff decide any refund or cancellation.
-- No business table: the receipt, audit event and outbox row record the dispute, and the turn's card_dispute
-- handoff carries the staff case summary. A confirmation answer is validated and stored with the command it confirms.

alter table actions.commands add column confirmation_input jsonb;
alter table actions.executors add column input_validator regprocedure;

create function actions.normalise_dispute(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare ids jsonb;
begin
  if jsonb_typeof(p_target->'card_id') is distinct from 'string' or (p_target->>'card_id') !~ '^card_[1-9][0-9]{0,14}$'
      or jsonb_typeof(p_target->'transaction_ids') is distinct from 'array' then
    raise exception 'card.dispute requires a card_id and transaction ids from the customer records' using errcode='22023';
  end if;
  if jsonb_array_length(p_target->'transaction_ids') not between 1 and 10 or exists(select 1 from jsonb_array_elements(p_target->'transaction_ids') e
      where jsonb_typeof(e) is distinct from 'string' or e#>>'{}' !~ '^txn_[1-9][0-9]{0,17}$') then
    raise exception 'card.dispute requires 1-10 transaction ids from the customer records' using errcode='22023';
  end if;
  select jsonb_agg(to_jsonb(id) order by substring(id from 5)::bigint) into ids
    from (select distinct e#>>'{}' id from jsonb_array_elements(p_target->'transaction_ids') e) d;
  if jsonb_array_length(ids)<>jsonb_array_length(p_target->'transaction_ids') then
    raise exception 'card.dispute transaction ids must be unique' using errcode='22023';
  end if;
  return jsonb_build_object('card_id',p_target->>'card_id','transaction_ids',ids);
end $$;

create function actions.dispute_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_dispute(p_target); card banking.credit_cards; items jsonb; matched int;
begin
  select * into card from banking.credit_cards where customer_id=p_customer_id and card_id=substring(target->>'card_id' from 6)::bigint;
  if not found then raise exception 'card is not available in this customer scope' using errcode='42501'; end if;
  select count(*),jsonb_agg(jsonb_build_object('transaction_id','txn_'||t.transaction_id,'date',(t.txn_date at time zone 'Asia/Singapore')::date,
      'merchant',t.merchant,'amount',t.amount,'currency',t.currency) order by t.txn_date,t.transaction_id)
    into matched,items from banking.transactions t
    where t.customer_id=p_customer_id and t.card_id=card.card_id and t.status='posted' and t.amount<0
      and t.txn_date>=clock_timestamp()-interval '120 days'
      and 'txn_'||t.transaction_id in (select e#>>'{}' from jsonb_array_elements(target->'transaction_ids') e);
  if matched<>jsonb_array_length(target->'transaction_ids') then
    raise exception 'every disputed charge must be an owned posted debit on this card from the last 120 days' using errcode='42501';
  end if;
  return target||jsonb_build_object('last4',card.last4,'items',items);
end $$;

create function actions.dispute_input(p_input jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
begin
  if jsonb_typeof(p_input) is distinct from 'object' then
    raise exception 'card.dispute confirmation requires card_in_possession yes, no or unsure' using errcode='22023';
  end if;
  if (select count(*) from jsonb_object_keys(p_input))<>1 or p_input->>'card_in_possession' is null
      or p_input->>'card_in_possession' not in ('yes','no','unsure') then
    raise exception 'card.dispute confirmation requires card_in_possession yes, no or unsure' using errcode='22023';
  end if;
  return jsonb_build_object('card_in_possession',p_input->>'card_in_possession');
end $$;

-- A missing answer is not a changed target: it raises 22023, so the whole confirmation rolls back and stays proposed.
create function actions.execute_dispute(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_command.confirmation_input is null or actions.dispute_input(p_command.confirmation_input)<>p_command.confirmation_input then
    raise exception 'a dispute draft needs the customer''s answer about the card; confirm it with that answer' using errcode='22023';
  end if;
  perform 1 from banking.credit_cards where customer_id=p_command.customer_id and card_id=substring(p_command.target->>'card_id' from 6)::bigint for share;
  begin
    if actions.dispute_target(p_command.target,p_command.customer_id)<>p_command.target then
      raise exception 'the disputed charges or card changed; review a new dispute draft' using errcode='P1001';
    end if;
  exception when sqlstate '42501' then
    raise exception 'a disputed charge is no longer an owned posted debit on this card; review a new dispute draft' using errcode='P1001';
  end;
  perform audit.record('action','action.card_dispute_recorded','success','actions.commands',p_command.command_id::text,
    jsonb_build_object('items',jsonb_array_length(p_command.target->'items')),p_command.case_id);
  return jsonb_build_object('result',p_command.target||p_command.confirmation_input||jsonb_build_object('status','submitted_for_review'),
    'event_type','card.dispute_recorded');
end $$;

-- Types with an input validator are confirmed only here; the shared confirmation path then executes them unchanged.
create function public.action_confirm_with_input(p_command_id uuid,p_input jsonb)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); command actions.commands; entry actions.executors; validated jsonb;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  perform actions.require_workflow_decision(p_command_id);
  select * into command from actions.commands where command_id=p_command_id and customer_id=cid for update;
  if not found then raise exception 'command not found in this customer scope' using errcode='P0002'; end if;
  select * into entry from actions.executors where type=command.type;
  if entry.input_validator is null then raise exception 'this action takes no confirmation answers' using errcode='22023'; end if;
  if command.state='proposed' then
    execute format('select %s($1)',entry.input_validator::regproc) into validated using p_input;
    update actions.commands set confirmation_input=validated where command_id=p_command_id;
  end if;
  return actions.confirm_recorded(p_command_id);
end $$;

-- The staff handoff for a confirmed dispute carries the turn's case summary under its own reason.
create or replace function actions.handoff_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'reason') is distinct from 'string' or p_target->>'reason' not in ('card_verification','card_dispute','general_support')
      or jsonb_typeof(p_target->'priority') is distinct from 'string' or p_target->>'priority' not in ('P0','P1','P2','P3')
      or jsonb_typeof(p_target->'summary') is distinct from 'string' or length(btrim(p_target->>'summary')) not between 1 and 1000 then
    raise exception 'handoff.create requires a registered reason, priority and redacted summary of at most 1000 characters' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_target->>'summary');
  return jsonb_build_object('reason',p_target->>'reason','priority',p_target->>'priority','summary',p_target->>'summary');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types,input_validator) values
  ('card.dispute','actions.dispute_target(jsonb,bigint)'::regprocedure,'actions.execute_dispute(actions.commands)'::regprocedure,
    true,'actions.normalise_dispute(jsonb)'::regprocedure,array['card.dispute_recorded'],'actions.dispute_input(jsonb)'::regprocedure);
revoke all on function actions.normalise_dispute(jsonb),actions.dispute_target(jsonb,bigint),actions.dispute_input(jsonb),
  actions.execute_dispute(actions.commands),actions.handoff_target(jsonb,bigint) from public,anon,authenticated;
revoke all on function public.action_confirm_with_input(uuid,jsonb) from public,anon;
grant execute on function public.action_confirm_with_input(uuid,jsonb) to authenticated;
