-- New event branches delegate all existing events to the released delivery implementation.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('document.submitted',1,'en','in_app','Demo document submitted','Your checked synthetic document is submitted and awaiting bank review. Open Your compliance review in chat for its recorded status. This is not approval.',true),
 ('compliance.action_reminder',1,'en','in_app','Recorded review action reminder','{action}. Recorded deadline: {due_date}. Open Your compliance review in chat; use Documents to upload for checklist items and the recorded instructions for information requests.',true);
alter function notify.deliver(events.outbox) rename to deliver_before_michelle;
revoke all on function notify.deliver_before_michelle(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; review compliance.cases; request compliance.info_requests;
 command actions.commands; receipt actions.receipts; template notify.templates; v_locale text; key text; vars jsonb:='{}';
 action_text text; due_date date; notification uuid; priority text:='P2';
begin
 if p_event.event_type not in ('document.submitted','compliance.deadline_reminder','compliance.information_reminder') then
  return notify.deliver_before_michelle(p_event);
 end if;
 if cid is null then raise exception 'customer required' using errcode='P2002'; end if;
 select preferred_language into v_locale from banking.customer_profiles where customer_id=cid;
 if p_event.event_type='document.submitted' then
  select * into command from actions.commands where command_id=p_event.command_id and customer_id=cid;
  select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=cid;
  if command.type is distinct from 'document.submit' or receipt.outcome is distinct from 'succeeded'
   or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
   or p_event.payload->>'command_id' is distinct from command.command_id::text
   or receipt.result->>'status' is distinct from 'submitted_awaiting_review' then
   raise exception 'submission event disagrees with receipt' using errcode='P2002';
  end if;
  key:='document.submitted';priority:='P3';
 else
  select * into review from compliance.cases where customer_id=cid and case_id::text=p_event.payload->>'case_id';
  if review.case_id is null then raise exception 'owned review required' using errcode='P2002'; end if;
  if review.status in ('completed','closed') then return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null); end if;
  if p_event.event_type='compliance.information_reminder' then
   select * into request from compliance.info_requests where customer_id=cid and case_id=review.case_id and request_id::text=p_event.payload->>'request_id';
   if request.request_id is null or request.status<>'open' or request.due_date is null or request.due_date::text is distinct from p_event.payload->>'due_date' then
    return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null);
   end if;
   action_text:=left(request.question,190);due_date:=request.due_date;
  else
   if review.status='under_review' or review.due_date is null or review.due_date::text is distinct from p_event.payload->>'due_date' then
    return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null);
   end if;
   select left(string_agg(case when submission_status='unknown' then 'Check status of ' else 'Provide/correct ' end||label,'; ' order by sort_order),190)
    into action_text from compliance.requested_items where customer_id=cid and case_id=review.case_id and submission_status in ('not_submitted','rejected','unknown');
   due_date:=review.due_date;
  end if;
  if action_text is null then return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null); end if;
  key:='compliance.action_reminder';vars:=jsonb_build_object('action',action_text,'due_date',due_date::text);
 end if;
 select * into template from notify.templates where template_key=key and channel='in_app' and locale=v_locale order by version desc limit 1;
 if template.template_key is null then raise exception 'notification locale unavailable' using errcode='P2001'; end if;
 insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
 values(cid,p_event.event_id,key,template.version,v_locale,priority,template.title,notify.render(template.body,vars),true,'event:'||p_event.event_id)
 on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
 if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
 perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
  jsonb_build_object('event_id',p_event.event_id,'template',key,'version',template.version,'priority',priority),
  p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
 return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

alter function events.enqueue_due_reminders() rename to enqueue_case_reminders;
revoke all on function events.enqueue_case_reminders() from public,anon,authenticated,demobank_worker;
create function events.enqueue_due_reminders() returns int language plpgsql volatile security definer set search_path='' as $$
declare today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date; inserted int; cases int;
begin
 cases:=events.enqueue_case_reminders();
 insert into events.outbox(event_type,customer_id,payload,dedup_key)
 select 'compliance.information_reminder',r.customer_id,jsonb_build_object('case_id',r.case_id,'request_id',r.request_id,'due_date',r.due_date),
  'information:'||r.request_id::text||':'||r.due_date::text||':'||today::text
 from compliance.info_requests r join compliance.cases c on c.case_id=r.case_id and c.customer_id=r.customer_id
 where r.status='open' and r.due_date between today and today+7 and c.status not in ('completed','closed')
 on conflict(customer_id,event_type,dedup_key) where dedup_key is not null do nothing;
 get diagnostics inserted=row_count;
 if inserted>0 then perform audit.record('notification','notification.reminders_enqueued','success','events.outbox',null,
  jsonb_build_object('event_count',inserted),p_actor_type=>'system',p_actor_id=>'notification_worker'); end if;
 return cases+inserted;
end $$;
revoke all on function events.enqueue_due_reminders() from public,anon,authenticated;
grant execute on function events.enqueue_due_reminders() to demobank_worker;
