-- A service receipt acknowledges synthetic intake without implying fulfilment or real advisor contact.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('rm.request_recorded',1,'en','in_app','Synthetic relationship request recorded','Your demo relationship request has a confirmed receipt. Check Requests for its recorded status. This is not confirmation that the service is completed or that a real advisor was contacted.',true);

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
  elsif p_event.event_type in ('booking.held','booking.confirmed','booking.cancelled') then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.command_id is null or receipt.command_id is null or receipt.outcome<>'succeeded'
        or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
        or command.type is distinct from (case p_event.event_type when 'booking.held' then 'appointment.hold'
          when 'booking.confirmed' then 'appointment.confirm' else 'appointment.cancel' end)
        or receipt.result->>'status' is distinct from (case p_event.event_type when 'booking.held' then 'held'
          when 'booking.confirmed' then 'confirmed' else 'cancelled' end)
        or not exists(select 1 from rm.appointments a where a.appointment_id::text=receipt.result->>'appointment_id' and a.customer_id=p_event.customer_id)
        or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value) then
      raise exception 'booking notification event % disagrees with its recorded receipt',p_event.event_id using errcode='P2002';
    end if;
    key:=p_event.event_type; priority:='P2';
  elsif p_event.event_type='rm.summary_shared' then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.type is distinct from 'rm.share_summary' or command.state is distinct from 'succeeded' or command.confirmed_at is null
        or receipt.outcome is distinct from 'succeeded' or receipt.result->>'status' is distinct from 'shared'
        or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
        or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value)
        or not exists(select 1 from rm.shared_summaries s where s.customer_id=p_event.customer_id and s.command_id=command.command_id
          and s.summary_id::text=receipt.result->>'summary_id' and s.agenda_id::text=command.target->>'agenda_id'
          and s.agenda_revision=(command.target->>'agenda_revision')::int and s.advisor_id::text=command.target->>'advisor_id'
          and s.content=command.target->>'content' and s.consent_grant_id::text=command.target->>'consent_grant_id'
          and s.shared_at=(receipt.result->>'shared_at')::timestamptz) then
      raise exception 'sharing notification event % disagrees with its reviewed snapshot and receipt',p_event.event_id using errcode='P2002';
    end if;
    key:='rm.summary_shared'; priority:='P1';
  elsif p_event.event_type='compliance.checklist_prepared' then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.type is distinct from 'compliance.update_checklist_item' or command.state is distinct from 'succeeded'
        or receipt.outcome is distinct from 'succeeded' or receipt.result->>'client_marked_at' is null
        or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
        or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value) then
      raise exception 'checklist notification event % disagrees with its recorded receipt',p_event.event_id using errcode='P2002';
    end if;
    key:='compliance.checklist_prepared';
  elsif p_event.event_type='rm.request_recorded' then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.type is distinct from 'requests.create' or command.state is distinct from 'succeeded' or command.confirmed_at is null
        or receipt.outcome is distinct from 'succeeded' or receipt.result->>'status' is distinct from 'sent'
        or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
        or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value)
        or not exists(select 1 from rm.requests r where r.customer_id=p_event.customer_id and r.command_id=command.command_id
          and r.request_id::text=receipt.result->>'request_id') then
      raise exception 'request notification event % disagrees with its confirmed receipt',p_event.event_id using errcode='P2002';
    end if;
    key:='rm.request_recorded'; priority:='P2';
  elsif p_event.event_type='agenda.saved' then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.type is distinct from 'agenda.save' or command.state is distinct from 'succeeded' or receipt.outcome is distinct from 'succeeded'
        or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id or receipt.result->>'status' is distinct from 'draft'
        or (receipt.result->>'revision')::int is distinct from (command.target->>'expected_revision')::int+1
        or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value) then
      raise exception 'agenda notification event % disagrees with its recorded receipt',p_event.event_id using errcode='P2002';
    end if;
    key:='agenda.saved';
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
  elsif p_event.event_type in ('consent.granted','consent.revoked') then
    select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
    select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
    if command.command_id is null or receipt.command_id is null or receipt.outcome<>'succeeded'
        or p_event.payload->>'command_id' is distinct from command.command_id::text
        or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
        or command.type is distinct from (case when p_event.event_type='consent.granted' then 'consent.grant' else 'consent.revoke' end)
        or receipt.result->>'status' is distinct from (case when p_event.event_type='consent.granted' then 'active' else 'revoked' end)
        or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value)
        or not exists(select 1 from consent.grants g where g.customer_id=p_event.customer_id and g.grant_id::text=receipt.result->>'grant_id'
          and (p_event.event_type='consent.granted' and g.command_id=command.command_id
            or p_event.event_type='consent.revoked' and g.revocation_command_id=command.command_id)) then
      raise exception 'consent notification event % disagrees with its recorded receipt',p_event.event_id using errcode='P2002';
    end if;
    key:=p_event.event_type; priority:='P1';
  elsif p_event.event_type='consent.requested' then
    if p_event.command_id is not null then
      select * into command from actions.commands where command_id=p_event.command_id and customer_id=p_event.customer_id;
      select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=p_event.customer_id;
      if command.type is distinct from 'consent.request' or receipt.outcome is distinct from 'succeeded'
          or p_event.payload->>'command_id' is distinct from command.command_id::text
          or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
          or receipt.result->>'status' is distinct from 'pending'
          or exists(select 1 from jsonb_each(command.target) t where receipt.result->t.key is distinct from t.value) then
        raise exception 'consent request notification event % disagrees with its receipt',p_event.event_id using errcode='P2002';
      end if;
      select * into request from consent.requests where customer_id=p_event.customer_id and command_id=command.command_id
        and request_id::text=receipt.result->>'request_id';
    else
      select * into request from consent.requests where customer_id=p_event.customer_id and request_id::text=p_event.payload->>'request_id';
    end if;
    if request.request_id is null then raise exception 'consent event % has no owned request',p_event.event_id using errcode='P2002'; end if;
    if request.status<>'pending' or request.grant_expires_at<=clock_timestamp() then return jsonb_build_object('disposition','stale','notification_id',null,'available_at',null); end if;
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
