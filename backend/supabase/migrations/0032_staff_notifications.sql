-- Recorded staff effects use immutable service templates, canonical receipts and the existing atomic delivery path.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('card.unblocked',1,'en','in_app','Synthetic card unblock recorded','A verified demo support session reactivated synthetic card ending {last4}. See Actions for the recorded receipt. No real bank card is connected.',true),
  ('card.cancelled',1,'en','in_app','Synthetic card cancellation recorded','A verified demo support session permanently cancelled synthetic card ending {last4}. See Actions for the recorded receipt. No real bank card is connected.',true),
  ('handoff.claimed',1,'en','in_app','Demo support case claimed','A verified demo support session claimed your synthetic support case. This records a coursework test operation; no real staffed support service is connected.',true),
  ('handoff.closed',1,'en','in_app','Demo support case closed','A verified demo support session closed your synthetic support case. See Actions for its receipt. Closing a case does not change a card or reverse a transaction.',true);

create or replace function notify.deliver(p_event events.outbox)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare template notify.templates; prefs notify.preferences; command actions.commands; receipt actions.receipts;
  assignment rm.assignments; review compliance.cases; request consent.requests;
  key text; vars jsonb:='{}'; priority text:='P3'; v_locale text; dedup text:='event:'||p_event.event_id;
  body text; title text; notification uuid; local_now timestamp:=clock_timestamp() at time zone 'Asia/Singapore'; until_time timestamp;
begin
  if p_event.customer_id is null then raise exception 'outbox event % requires a customer',p_event.event_id using errcode='P2002'; end if;
  select preferred_language into v_locale from banking.customer_profiles where customer_id=p_event.customer_id;
  if v_locale is null then raise exception 'notification customer % was not found',p_event.customer_id using errcode='P2002'; end if;
  if p_event.event_type in ('card.blocked','card.unblocked','card.cancelled','handoff.created','handoff.reused','handoff.claimed','handoff.closed','action.failed') then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.command_id is null or receipt.command_id is null or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id then
      raise exception 'notification event % does not reference its owned recorded command and receipt',p_event.event_id using errcode='P2002';
    end if;
    if p_event.event_type='card.blocked' then
      if command.type<>'card.temporary_block' or receipt.outcome<>'succeeded' or receipt.result->>'status'<>'temporarily_blocked'
          or receipt.result->>'card_id' is distinct from command.target->>'card_id' then
        raise exception 'card notification event % disagrees with its receipt',p_event.event_id using errcode='P2002';
      end if;
      key:='card.blocked'; priority:='P0'; vars:=jsonb_build_object('last4',command.target->>'last4');
      if vars->>'last4' !~ '^[0-9]{4}$' then raise exception 'card notification requires a masked suffix' using errcode='P2002'; end if;
    elsif p_event.event_type in ('card.unblocked','card.cancelled') then
      if command.type is distinct from (case when p_event.event_type='card.unblocked' then 'card.unblock' else 'card.cancel_permanently' end)
          or receipt.outcome<>'succeeded' or receipt.result->>'status' is distinct from (case when p_event.event_type='card.unblocked' then 'active' else 'cancelled' end)
          or receipt.result->>'card_id' is distinct from command.target->>'card_id'
          or not exists(select 1 from actions.staff_commands where command_id=command.command_id) then
        raise exception 'staff card notification event % disagrees with its receipt',p_event.event_id using errcode='P2002';
      end if;
      key:=p_event.event_type; priority:='P1'; vars:=jsonb_build_object('last4',command.target->>'last4');
      if vars->>'last4' !~ '^[0-9]{4}$' then raise exception 'staff card notification requires a masked suffix' using errcode='P2002'; end if;
    elsif p_event.event_type in ('handoff.claimed','handoff.closed') then
      if command.type is distinct from (case when p_event.event_type='handoff.claimed' then 'handoff.claim' else 'handoff.close' end)
          or receipt.outcome<>'succeeded' or receipt.result->>'status' is distinct from (case when p_event.event_type='handoff.claimed' then 'claimed' else 'closed' end)
          or receipt.result->>'handoff_id' is distinct from command.target->>'handoff_id'
          or not exists(select 1 from actions.staff_commands where command_id=command.command_id) then
        raise exception 'staff handoff notification event % disagrees with its receipt',p_event.event_id using errcode='P2002';
      end if;
      select h.priority into priority from support.handoffs h where h.customer_id=p_event.customer_id and h.handoff_id::text=receipt.result->>'handoff_id';
      if priority is null then raise exception 'staff handoff notification has no owned handoff' using errcode='P2002'; end if;
      key:=p_event.event_type;
    elsif p_event.event_type in ('handoff.created','handoff.reused') then
      if command.type<>'handoff.create' or receipt.outcome<>'succeeded' or not exists(select 1 from support.handoffs h
          where h.handoff_id::text=receipt.result->>'handoff_id' and h.customer_id=p_event.customer_id) then
        raise exception 'handoff notification event % disagrees with its receipt',p_event.event_id using errcode='P2002';
      end if;
      key:='handoff.recorded'; priority:=command.target->>'priority'; dedup:='handoff:'||(receipt.result->>'handoff_id');
    else
      if receipt.outcome<>'failed' then raise exception 'failure notification event % has no failed receipt',p_event.event_id using errcode='P2002'; end if;
      key:='action.failed'; priority:='P1';
    end if;
  elsif p_event.event_type='compliance.deadline_reminder' then
    select * into review from compliance.cases where customer_id=p_event.customer_id and case_id::text=p_event.payload->>'case_id';
    if review.case_id is null then raise exception 'reminder event % has no owned review case',p_event.event_id using errcode='P2002'; end if;
    if review.status in ('completed','closed','under_review') or review.due_date is null
        or review.due_date::text is distinct from p_event.payload->>'due_date' or not exists(select 1 from compliance.requested_items i
          where i.customer_id=p_event.customer_id and i.case_id=review.case_id and i.submission_status in ('not_submitted','rejected','unknown')) then
      return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null);
    end if;
    key:='compliance.deadline_reminder'; priority:='P2'; vars:=jsonb_build_object('case_title',review.title,'due_date',review.due_date::text);
  elsif p_event.event_type='rm.assignment_changed' then
    select * into assignment from rm.assignments where customer_id=p_event.customer_id and assignment_id::text=p_event.payload->>'assignment_id';
    if assignment.assignment_id is null then raise exception 'assignment event % has no owned assignment',p_event.event_id using errcode='P2002'; end if;
    if assignment.ended_on is not null then return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null); end if;
    key:='rm.assignment_changed';
    select jsonb_build_object('advisor_name',full_name) into vars from rm.advisors where advisor_id=assignment.advisor_id;
  elsif p_event.event_type='consent.requested' then
    select * into request from consent.requests where customer_id=p_event.customer_id and request_id::text=p_event.payload->>'request_id';
    if request.request_id is null then raise exception 'consent event % has no owned request',p_event.event_id using errcode='P2002'; end if;
    if request.status<>'pending' then return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null); end if;
    key:='consent.requested';
  else raise exception 'notification event type % is unsupported',p_event.event_type using errcode='P2003';
  end if;
  select * into template from notify.templates where template_key=key and channel='in_app' and locale=v_locale order by version desc limit 1;
  if template.template_key is null then raise exception 'notification template % has no in-app version for locale %',key,v_locale using errcode='P2001'; end if;
  select * into prefs from notify.preferences where customer_id=p_event.customer_id;
  if not template.is_service then
    if prefs.customer_id is null then raise exception 'notification preferences for customer % were not found',p_event.customer_id using errcode='P2004'; end if;
    if not ('in_app'=any(prefs.channels)) then return jsonb_build_object('disposition','suppressed','notification_id',null,'available_at',null); end if;
    if (prefs.quiet_start is null)<>(prefs.quiet_end is null) or prefs.quiet_start=prefs.quiet_end then
      raise exception 'notification quiet hours must have two distinct boundaries' using errcode='P2004';
    end if;
    if prefs.quiet_start is not null and ((prefs.quiet_start<prefs.quiet_end and local_now::time>=prefs.quiet_start and local_now::time<prefs.quiet_end)
        or (prefs.quiet_start>prefs.quiet_end and (local_now::time>=prefs.quiet_start or local_now::time<prefs.quiet_end))) then
      until_time:=local_now::date+prefs.quiet_end;
      if until_time<=local_now then until_time:=until_time+interval '1 day'; end if;
      return jsonb_build_object('disposition','deferred','notification_id',null,'available_at',until_time at time zone 'Asia/Singapore');
    end if;
  end if;
  title:=notify.render(template.title,'{}'); body:=notify.render(template.body,vars);
  insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
    values(p_event.customer_id,p_event.event_id,key,template.version,v_locale,priority,title,body,template.is_service,dedup)
    on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
  if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
  perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
    jsonb_build_object('event_id',p_event.event_id,'template',key,'version',template.version,'priority',priority),
    p_customer_id=>p_event.customer_id,p_actor_type=>'system',p_actor_id=>'notification_worker');
  return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
