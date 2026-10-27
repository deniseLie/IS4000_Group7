-- Customer self-service for the customer's own synthetic card: unblock (temporarily_blocked -> active) and permanent
-- cancellation (active or temporarily_blocked -> cancelled; cancelled is terminal). Both are confirmed customer actions.
-- card.unblock and card.cancel_permanently keep their staff executors (0030): a target naming a handoff_id is a staff
-- operation (run_staff inserts it already confirmed and never reads confirmation_required); a target without one is the
-- customer's reviewed change. Unblocking is allowed even after a fraud block, so a stolen session could reverse one;
-- README and docs/workflows/workflow-1-alex.md record this trade-off. Open card handoffs get an append-only note.

create table support.handoff_notes(
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
create policy handoff_notes_own on support.handoff_notes for select to authenticated using (customer_id=banking.my_customer_id());

create function actions.normalise_card_change(p_target jsonb)
returns jsonb language plpgsql stable security definer set search_path=''
as $$
begin
  if p_target ? 'handoff_id' then return actions.normalise_staff_card(p_target); end if;
  return actions.normalise_card_target(p_target);
end $$;

-- The customer's own card, re-resolved from the session and the explicit customer scope; from_status is the optimistic check.
create function actions.customer_card_change(p_target jsonb,p_customer_id bigint,p_operation text)
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

create function actions.unblock_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_target ? 'handoff_id' then return actions.staff_card_target(p_target,p_customer_id); end if;
  return actions.customer_card_change(p_target,p_customer_id,'unblock');
end $$;

create function actions.cancel_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_target ? 'handoff_id' then return actions.staff_card_target(p_target,p_customer_id); end if;
  return actions.customer_card_change(p_target,p_customer_id,'cancel');
end $$;

-- One locked, confirmed customer change. A different current status records a failed receipt (P1001), nothing else.
create function actions.execute_customer_card_change(p_command actions.commands,p_to text,p_kind text,p_event text)
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

create function actions.execute_card_unblock(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
begin
  if p_command.target ? 'handoff_id' then return actions.execute_staff_unblock(p_command); end if;
  return actions.execute_customer_card_change(p_command,'active','card_unblocked_by_customer','card.unblocked_by_customer');
end $$;

create function actions.execute_card_cancel(p_command actions.commands)
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
