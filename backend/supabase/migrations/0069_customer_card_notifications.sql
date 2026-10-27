-- In-app service notices for the customer's own card changes; every other event keeps its released delivery.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('card.unblocked_by_customer',1,'en','in_app','You unblocked your card','You reactivated your synthetic card ending {last4}; new card payments can go through again. If bank staff are reviewing a case for this card, they can see this change. See Actions for the receipt. No real bank card is connected.',true),
 ('card.cancelled_by_customer',1,'en','in_app','You cancelled your card','You permanently cancelled your synthetic card ending {last4}. It cannot be reactivated. If bank staff are reviewing a case for this card, they can see this change. See Actions for the receipt. No real bank card is connected.',true);
alter function notify.deliver(events.outbox) rename to deliver_before_card_self_service;
revoke all on function notify.deliver_before_card_self_service(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; command actions.commands; receipt actions.receipts; template notify.templates;
 v_locale text; notification uuid; expected text;
begin
 if p_event.event_type not in ('card.unblocked_by_customer','card.cancelled_by_customer') then return notify.deliver_before_card_self_service(p_event); end if;
 if cid is null then raise exception 'customer required' using errcode='P2002'; end if;
 expected:=case when p_event.event_type='card.unblocked_by_customer' then 'active' else 'cancelled' end;
 select * into command from actions.commands where command_id=p_event.command_id and customer_id=cid;
 select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=cid;
 if command.type is distinct from (case when expected='active' then 'card.unblock' else 'card.cancel_permanently' end)
  or command.target ? 'handoff_id' or command.confirmed_at is null or receipt.outcome is distinct from 'succeeded'
  or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id or p_event.payload->>'command_id' is distinct from command.command_id::text
  or receipt.result->>'status' is distinct from expected or receipt.result->>'card_id' is distinct from command.target->>'card_id' then
  raise exception 'card change event disagrees with its receipt' using errcode='P2002';
 end if;
 select preferred_language into v_locale from banking.customer_profiles where customer_id=cid;
 select * into template from notify.templates where template_key=p_event.event_type and channel='in_app' and locale=v_locale order by version desc limit 1;
 if template.template_key is null then raise exception 'notification locale unavailable' using errcode='P2001'; end if;
 insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
 values(cid,p_event.event_id,template.template_key,template.version,v_locale,'P1',template.title,
  notify.render(template.body,jsonb_build_object('last4',command.target->>'last4')),true,'event:'||p_event.event_id)
 on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
 if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
 perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
  jsonb_build_object('event_id',p_event.event_id,'template',template.template_key,'version',template.version,'priority','P1'),
  p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
 return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;
