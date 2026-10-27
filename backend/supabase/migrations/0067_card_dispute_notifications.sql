-- The confirmed dispute draft gets an in-app service notice; every other event keeps its released delivery.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('card.dispute_recorded',1,'en','in_app','Dispute draft recorded for review','Your dispute draft for {count} charges on the synthetic card ending {last4} is recorded for DemoBank staff review. Staff decide any refund or cancellation; nothing has been refunded. Open Actions for the receipt.',true);
alter function notify.deliver(events.outbox) rename to deliver_before_dispute;
revoke all on function notify.deliver_before_dispute(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; command actions.commands; receipt actions.receipts; template notify.templates;
 v_locale text; vars jsonb; notification uuid;
begin
 if p_event.event_type<>'card.dispute_recorded' then return notify.deliver_before_dispute(p_event); end if;
 if cid is null then raise exception 'customer required' using errcode='P2002'; end if;
 select * into command from actions.commands where command_id=p_event.command_id and customer_id=cid;
 select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=cid;
 if command.type is distinct from 'card.dispute' or receipt.outcome is distinct from 'succeeded' or command.confirmed_at is null
  or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
  or p_event.payload->>'command_id' is distinct from command.command_id::text
  or receipt.result->>'status' is distinct from 'submitted_for_review' or receipt.result->'items' is distinct from command.target->'items' then
  raise exception 'dispute event disagrees with its receipt' using errcode='P2002';
 end if;
 select preferred_language into v_locale from banking.customer_profiles where customer_id=cid;
 vars:=jsonb_build_object('count',jsonb_array_length(command.target->'items')::text,'last4',command.target->>'last4');
 select * into template from notify.templates where template_key='card.dispute_recorded' and channel='in_app' and locale=v_locale order by version desc limit 1;
 if template.template_key is null then raise exception 'notification locale unavailable' using errcode='P2001'; end if;
 insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
 values(cid,p_event.event_id,template.template_key,template.version,v_locale,'P1',template.title,notify.render(template.body,vars),true,'event:'||p_event.event_id)
 on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
 if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
 perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
  jsonb_build_object('event_id',p_event.event_id,'template',template.template_key,'version',template.version,'priority','P1'),
  p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
 return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;
