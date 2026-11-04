-- Confirmed chat offers retain their provenance; invalid trip ranges produce failed receipts.
create or replace function actions.execute_ticket(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare ticket support.tickets; created boolean:=false; wanted text:=p_command.target->>'category';
begin
  if actions.ticket_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the request category changed; review a new ticket' using errcode='P1001';
  end if;
  if wanted='travel_notice' and ((p_command.target->'details'->>'to_date')::date<(p_command.target->'details'->>'from_date')::date
      or (p_command.target->'details'->>'to_date')::date-(p_command.target->'details'->>'from_date')::date>180) then
    raise exception 'review the travel dates: the trip must end on or after its start and last at most 180 days' using errcode='P1001';
  end if;
  select * into ticket from support.tickets t where t.customer_id=p_command.customer_id and t.category=wanted
    and t.status in ('submitted','in_review','waiting_on_client')
    and case wanted when 'card_replacement' then t.source in ('form','chat') and t.details->>'card'=p_command.target->'details'->>'card'
                      when 'card_dispute' then t.details->>'transaction'=p_command.target->'details'->>'transaction'
                      else t.source in ('form','chat') end
    order by t.created_at limit 1 for update;
  if not found then
    ticket:=support.open_ticket(p_command.customer_id,p_command.case_id,wanted,p_command.target->>'summary',
      p_command.target->'details',p_command.target->>'urgency',case when p_command.offer_group is null then 'form' else 'chat' end,p_command_id=>p_command.command_id);
    created:=true;
  end if;
  return jsonb_build_object('result',jsonb_build_object('ticket_id',ticket.ticket_id,'reference',ticket.reference,'category',ticket.category,
      'status',ticket.status,'owner_team',ticket.owner_team,'target_at',ticket.target_at,'created',created),
    'event_type',case when created then 'ticket.recorded' else 'ticket.reused' end);
end $$;
