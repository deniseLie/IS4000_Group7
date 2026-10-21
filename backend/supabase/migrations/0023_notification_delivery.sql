-- A worker login joins this NOLOGIN role. It receives narrow function access, never table access or BYPASSRLS.
do $$ begin
  if not exists(select 1 from pg_roles where rolname='demobank_worker') then create role demobank_worker nologin; end if;
end $$;
grant usage on schema events to demobank_worker;
alter table events.outbox add column dedup_key text;
alter table events.outbox add column last_error_code text;
alter table events.outbox add column disposition text check(disposition in ('delivered','suppressed','deferred','stale','duplicate'));
create unique index outbox_dedup_idx on events.outbox(customer_id,event_type,dedup_key) where dedup_key is not null;
alter table notify.inbox add column event_id bigint references events.outbox(event_id);
alter table notify.inbox add column template_version int not null default 1;
alter table notify.inbox add column locale text not null default 'en';
alter table notify.inbox add column priority text not null default 'P3' check(priority in ('P0','P1','P2','P3'));
alter table notify.inbox add column dedup_key text;
create unique index inbox_dedup_idx on notify.inbox(customer_id,dedup_key) where dedup_key is not null;

-- New immutable template versions describe recorded synthetic events rather than current real-world card state.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('card.blocked',2,'en','in_app','Synthetic card block recorded','Your confirmed demo command temporarily blocked synthetic card ending {last4}. It did not reverse any transaction. See Actions for the receipt.',true),
  ('handoff.recorded',1,'en','in_app','Demo support handoff recorded','A support handoff is recorded for your synthetic case. This demo has no staffed support queue.',true),
  ('action.failed',1,'en','in_app','Demo action did not complete','A confirmed demo command failed because its target changed. This command did not change the target. See Actions for the recorded result.',true),
  ('compliance.deadline_reminder',2,'en','in_app','Synthetic review deadline reminder','Your synthetic {case_title} has outstanding checklist items due by {due_date}. This is a reminder, not a compliance decision.',true),
  ('rm.assignment_changed',2,'en','in_app','Synthetic relationship manager assignment','{advisor_name} is recorded as your relationship manager in this demo. No unrecorded reason for the change is implied.',true),
  ('consent.requested',2,'en','in_app','Review a demo sharing request','A synthetic data-sharing request is waiting for your review. No consent was granted by this notification.',false);

create function notify.render(p_template text,p_vars jsonb)
returns text language plpgsql immutable security invoker set search_path=''
as $$
declare result text:=p_template; key text; value text;
begin
  if p_template is null or jsonb_typeof(p_vars) is distinct from 'object' then
    raise exception 'notification rendering requires a template and named variables' using errcode='P2001';
  end if;
  for key,value in select k,v#>>'{}' from jsonb_each(p_vars) as x(k,v) loop
    if key !~ '^[a-z_]{1,30}$' or jsonb_typeof(p_vars->key)<>'string' or value is null or length(value)>200
        or value ~ '[{}]' or position('{'||key||'}' in p_template)=0 then
      raise exception 'notification variable % is invalid or not declared by the template',key using errcode='P2001';
    end if;
    result:=replace(result,'{'||key||'}',value);
  end loop;
  if result ~ '[{}]' or length(result)>1500 then raise exception 'notification template has missing variables or exceeds its size limit' using errcode='P2001'; end if;
  perform conversation.require_redacted(result);
  return result;
end $$;
revoke all on function notify.render(text,jsonb) from public,anon,authenticated,demobank_worker;

create function notify.deliver(p_event events.outbox)
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
  if p_event.event_type in ('card.blocked','handoff.created','handoff.reused','action.failed') then
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
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated,demobank_worker;

create function events.process_next_notification()
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare event events.outbox; result jsonb; failure text;
begin
  select * into event from events.outbox where status='pending' and available_at<=clock_timestamp()
    order by available_at,event_id for update skip locked limit 1;
  if not found then return null; end if;
  update events.outbox set status='processing',attempts=attempts+1,last_error_code=null where event_id=event.event_id returning * into event;
  begin
    result:=notify.deliver(event);
    update events.outbox set status=case when result->>'disposition'='deferred' then 'pending' else 'done' end,
      disposition=result->>'disposition',available_at=coalesce((result->>'available_at')::timestamptz,available_at),
      attempts=case when result->>'disposition'='deferred' then attempts-1 else attempts end,
      processed_at=case when result->>'disposition'='deferred' then null else clock_timestamp() end where event_id=event.event_id;
    if result->>'disposition'<>'delivered' then
      perform audit.record('notification','notification.'||(result->>'disposition'),'success','events.outbox',event.event_id::text,
        jsonb_build_object('event_id',event.event_id),p_customer_id=>event.customer_id,p_actor_type=>'system',p_actor_id=>'notification_worker');
    end if;
  exception when sqlstate 'P2001' or sqlstate 'P2002' or sqlstate 'P2003' or sqlstate 'P2004' or sqlstate '22023' then
    get stacked diagnostics failure=returned_sqlstate;
    update events.outbox set status=case when attempts>=5 then 'failed' else 'pending' end,last_error_code=failure,
      available_at=clock_timestamp()+make_interval(secs=>least(300,power(2,event.attempts)::int)),
      processed_at=case when attempts>=5 then clock_timestamp() else null end where event_id=event.event_id;
    perform audit.record('notification','notification.failed','failed','events.outbox',event.event_id::text,
      jsonb_build_object('event_id',event.event_id,'error_code',failure,'attempts',event.attempts),
      p_customer_id=>event.customer_id,p_actor_type=>'system',p_actor_id=>'notification_worker');
    result:=jsonb_build_object('disposition',null,'notification_id',null,'available_at',null);
  end;
  return (select jsonb_build_object('event_id',e.event_id,'status',e.status,'attempts',e.attempts,
    'disposition',e.disposition,'error_code',e.last_error_code,'notification_id',result->'notification_id') from events.outbox e where e.event_id=event.event_id);
end $$;
revoke all on function events.process_next_notification() from public,anon,authenticated;
grant execute on function events.process_next_notification() to demobank_worker;
