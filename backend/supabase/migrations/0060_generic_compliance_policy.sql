-- Generic owned-notice checking and operator-controlled policy. Historical ledgers are preserved.
create table compliance.check_policy(
 singleton boolean primary key default true check(singleton),
 timezone text not null default 'Asia/Singapore',
 validity_hours int not null default 24 check(validity_hours between 1 and 168),
 status_stale_days int not null default 7 check(status_stale_days between 1 and 365),
 reminders_are_service boolean not null default true,
 notification_fallback_locale text not null default 'en' check(notification_fallback_locale ~ '^[a-z]{2}(-[A-Z]{2})?$'),
 reminder_days int not null default 7 check(reminder_days between 0 and 90),
 statement_period_mode text not null default 'completed_months' check(statement_period_mode in ('completed_months','rolling_months')),
 date_formats text[] not null default array['%Y-%m-%d','%Y/%m/%d','%d %B %Y','%d %b %Y','%B %d, %Y','%b %d, %Y','%B %d %Y','%b %d %Y','%d/%m/%Y','%m/%d/%Y','%Y-%m','%B %Y','%b %Y'],
 numeric_date_order text not null default 'reject_ambiguous' check(numeric_date_order in ('reject_ambiguous','day_first','month_first'))
);
insert into compliance.check_policy(singleton) values(true);
alter table compliance.check_policy enable row level security;
revoke all on compliance.check_policy from public,anon,authenticated;
grant select on compliance.check_policy to authenticated;
create policy published_check_policy on compliance.check_policy for select to authenticated using(true);
create function compliance.policy() returns jsonb language sql stable security invoker set search_path='' as $$
 select to_jsonb(p)-'singleton' from compliance.check_policy p where singleton
$$;
revoke all on function compliance.policy() from public,anon;
grant execute on function compliance.policy() to authenticated;

-- Additional bank-published checking guidance is data, not application templates.
alter table compliance.requested_items add column check_guidance text;
create or replace function compliance.notice_text(p_case_id uuid,p_item_id uuid) returns text
language sql stable security invoker set search_path='' as $$
 select c.notice_text||E'\n'||i.label||E'\n'||coalesce(i.instructions,'')||E'\n'||coalesce(i.check_guidance,'')
 from compliance.cases c join compliance.requested_items i on i.case_id=c.case_id and i.customer_id=c.customer_id
 where c.case_id=p_case_id and i.item_id=p_item_id and c.customer_id=banking.my_customer_id()
$$;

-- No persona/customer UUIDs and no preconfigured document-type allow-list.
insert into compliance.precheck_rules(rule_id,version,title,fields,expected_pages,scope)
 values('notice',1,'Recorded notice requirements',array['Recorded notice requirements'],1,'Preliminary notice-based text checks; never formal approval.');
update compliance.requested_items set precheck_rule_id='notice';
alter table compliance.requested_items alter column precheck_rule_id set default 'notice';
create function compliance.enable_notice_check() returns trigger language plpgsql set search_path='' as $$
begin new.precheck_rule_id:='notice'; return new; end $$;
revoke all on function compliance.enable_notice_check() from public,anon,authenticated;
create trigger enable_notice_check before insert or update of precheck_rule_id on compliance.requested_items
 for each row execute function compliance.enable_notice_check();

create or replace function compliance.notice_digest(p_case_id uuid,p_item_id uuid) returns text
language sql stable security invoker set search_path='' as $$
 select encode(sha256(convert_to(compliance.notice_text(p_case_id,p_item_id)||E'\n'||compliance.policy()::text,'UTF8')),'hex')
$$;
drop index compliance.prechecks_notice_daily_unique;

create or replace function public.customer_precheck_context(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
 select jsonb_build_object('document_id',d.document_id,'case_id',d.case_id,'item_id',d.item_id,
  'current_document_id',i.current_document_id,'policy',compliance.policy(),'notice',compliance.notice_text(d.case_id,d.item_id),'requirements_hash',compliance.notice_digest(d.case_id,d.item_id),'rule',case when r.rule_id is null then null else to_jsonb(r) end) into data
 from compliance.documents d join compliance.requested_items i on i.item_id=d.item_id and i.case_id=d.case_id and i.customer_id=cid
 left join compliance.precheck_rules r on r.rule_id=i.precheck_rule_id
 where d.document_id=p_document_id and d.customer_id=cid;
 return banking.customer_result(cid,'documents.precheck','compliance.documents',data);
end $$;

create or replace function public.customer_precheck_read(p_customer_id bigint,p_document_id uuid)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
 select p.report into data from compliance.prechecks p join compliance.requested_items i
 on i.item_id=p.item_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
 where p.customer_id=cid and p.document_id=p_document_id and p.report->>'scope'='notice_based' and p.requirements_hash=compliance.notice_digest(p.case_id,p.item_id) order by p.created_at desc limit 1;
 return banking.customer_result(cid,'documents.precheck','compliance.prechecks',data);
end $$;

create or replace function public.document_precheck_record(p_payload text,p_signature text)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.my_customer_id(); payload jsonb; document compliance.documents; item compliance.requested_items;
 rule compliance.precheck_rules; previous compliance.prechecks; signing_secret text;
begin
 if auth.uid() is null or cid is null then raise exception 'linked customer required' using errcode='42501'; end if;
 if p_payload is null or octet_length(p_payload)>16384 or p_signature is null then raise exception 'invalid precheck envelope' using errcode='22023'; end if;
 select secret into strict signing_secret from compliance.precheck_keys where key_id='v1';
 if compliance.precheck_hmac(convert_to(p_payload,'UTF8'),convert_to(signing_secret,'UTF8'))<>p_signature then
  raise exception 'precheck signature invalid' using errcode='42501';
 end if;
 payload:=p_payload::jsonb;
 perform 1 from compliance.check_policy for share;
 select * into document from compliance.documents where document_id=(payload->>'document_id')::uuid and customer_id=cid;
 perform 1 from compliance.cases where case_id=document.case_id and customer_id=cid and status not in ('completed','closed') for share;
 if not found then raise exception 'owned open case required' using errcode='42501'; end if;
 select * into item from compliance.requested_items where item_id=document.item_id and case_id=document.case_id and customer_id=cid for update;
 select * into rule from compliance.precheck_rules where rule_id=item.precheck_rule_id;
 if document.document_id is null or item.item_id is null or rule.rule_id is null
  or (payload->>'customer_id')::bigint is distinct from cid or payload->>'case_id' is distinct from document.case_id::text
  or payload->>'item_id' is distinct from item.item_id::text or payload->>'rule_id' is distinct from rule.rule_id
  or (payload->>'rule_version')::int is distinct from rule.version or payload->>'scope' is distinct from 'notice_based'
  or payload->>'requirements_hash' is distinct from compliance.notice_digest(document.case_id,item.item_id)
  or (payload->>'expires_at')::timestamptz is distinct from (payload->>'checked_at')::timestamptz+make_interval(hours=>(compliance.policy()->>'validity_hours')::int)
  or (payload->>'expires_at')::timestamptz<=clock_timestamp()
  or (payload->>'checked_at')::timestamptz>clock_timestamp()+interval '1 minute'
  or (payload->>'checked_at')::timestamptz<clock_timestamp()-interval '10 minutes' then
  raise exception 'precheck source or rule changed' using errcode='22023';
 end if;
 select * into previous from compliance.prechecks where check_id=(payload->>'check_id')::uuid;
 if found then
  if previous.customer_id<>cid or previous.report is distinct from payload then raise exception 'document bytes changed' using errcode='23514'; end if;
  return banking.customer_result(cid,'documents.precheck','compliance.prechecks',previous.report);
 end if;
 insert into compliance.prechecks(check_id,document_id,customer_id,case_id,item_id,rule_id,rule_version,sha256,status,report)
 values((payload->>'check_id')::uuid,document.document_id,cid,document.case_id,item.item_id,rule.rule_id,rule.version,payload->>'sha256',payload->>'status',payload);
 update compliance.documents set precheck_status=case payload->>'status' when 'no_issues' then 'passed' when 'issues_found' then 'issues_found' else 'not_checked' end
 where document_id=document.document_id and customer_id=cid;
 perform audit.record('document','document.prechecked','success','compliance.documents',document.document_id::text,
  jsonb_build_object('status',payload->>'status','rule_id',rule.rule_id,'rule_version',rule.version));
 return banking.customer_result(cid,'documents.precheck','compliance.prechecks',payload);
end $$;


create or replace function public.customer_compliance_workspace(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); review jsonb; data jsonb;
begin
 review:=public.customer_read_compliance(cid)->'data';
 data:=jsonb_build_object('policy',compliance.policy(),'review',review,'documents',public.customer_read_documents(cid)->'data',
  'rules',coalesce((select jsonb_object_agg(i.item_id::text,to_jsonb(r)) from compliance.requested_items i
    join compliance.precheck_rules r on r.rule_id=i.precheck_rule_id
    where i.customer_id=cid and i.case_id::text=review->>'case_id'),'{}'),
  'current_documents',coalesce((select jsonb_object_agg(i.item_id::text,i.current_document_id) from compliance.requested_items i
    where i.customer_id=cid and i.case_id::text=review->>'case_id' and i.current_document_id is not null),'{}'),
  'checks',coalesce((select jsonb_agg(p.report order by p.created_at desc) from (select distinct on (document_id) * from compliance.prechecks
      where customer_id=cid and report->>'scope'='notice_based' and requirements_hash=compliance.notice_digest(case_id,item_id) order by document_id,checked_on desc,created_at desc) p
    join compliance.requested_items i on i.current_document_id=p.document_id and i.customer_id=cid and i.precheck_rule_id=p.rule_id
    where p.customer_id=cid and p.case_id::text=review->>'case_id'),'[]'));
 return banking.customer_result(cid,'compliance.workspace','compliance.cases',data);
end $$;

-- Final execution checks current notice/policy, expiry and most recent assessment under locks.
create or replace function actions.execute_document_submit(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare latest compliance.prechecks;
begin
 perform 1 from compliance.check_policy for share;
 perform 1 from compliance.cases where case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for share;
 perform 1 from compliance.requested_items where item_id=(p_command.target->>'item_id')::uuid
  and case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for update;
 select * into latest from compliance.prechecks p where p.document_id=(p_command.target->>'document_id')::uuid
  and p.customer_id=p_command.customer_id and p.report->>'scope'='notice_based'
  and p.requirements_hash=compliance.notice_digest(p.case_id,p.item_id) order by created_at desc limit 1;
 if latest.check_id is distinct from (p_command.target->>'check_id')::uuid
  or (latest.report->>'expires_at')::timestamptz is null or (latest.report->>'expires_at')::timestamptz<=clock_timestamp() then
  raise exception 'check expired or a newer assessment/notice exists; recheck current upload' using errcode='P1001';
 end if;
 return actions.execute_document_submit_before_freshness(p_command);
end $$;

create or replace function events.enqueue_case_reminders()
returns int language plpgsql volatile security definer set search_path=''
as $$
declare today date:=(clock_timestamp() at time zone (compliance.policy()->>'timezone'))::date; inserted int;
begin
  insert into events.outbox(event_type,customer_id,payload,dedup_key)
    select 'compliance.deadline_reminder',c.customer_id,jsonb_build_object('case_id',c.case_id,'due_date',c.due_date),
      'compliance:'||c.case_id::text||':'||c.due_date::text||':'||today::text
    from compliance.cases c where c.due_date between today and today+(compliance.policy()->>'reminder_days')::int
      and c.status in ('review_opened','documents_requested','documents_partially_received','awaiting_client_documents','information_requested')
      and exists(select 1 from compliance.requested_items i where i.customer_id=c.customer_id and i.case_id=c.case_id
        and i.submission_status in ('not_submitted','rejected','unknown'))
    on conflict(customer_id,event_type,dedup_key) where dedup_key is not null do nothing;
  get diagnostics inserted=row_count;
  if inserted>0 then
    perform audit.record('notification','notification.reminders_enqueued','success','events.outbox',null,jsonb_build_object('event_count',inserted),
      p_actor_type=>'system',p_actor_id=>'notification_worker');
  end if;
  return inserted;
end $$;

create or replace function events.enqueue_due_reminders() returns int language plpgsql volatile security definer set search_path='' as $$
declare today date:=(clock_timestamp() at time zone (compliance.policy()->>'timezone'))::date; inserted int; cases int;
begin
 cases:=events.enqueue_case_reminders();
 insert into events.outbox(event_type,customer_id,payload,dedup_key)
 select 'compliance.information_reminder',r.customer_id,jsonb_build_object('case_id',r.case_id,'request_id',r.request_id,'due_date',r.due_date),
  'information:'||r.request_id::text||':'||r.due_date::text||':'||today::text
 from compliance.info_requests r join compliance.cases c on c.case_id=r.case_id and c.customer_id=r.customer_id
 where r.status='open' and r.due_date between today and today+(compliance.policy()->>'reminder_days')::int and c.status not in ('completed','closed')
 on conflict(customer_id,event_type,dedup_key) where dedup_key is not null do nothing;
 get diagnostics inserted=row_count;
 if inserted>0 then perform audit.record('notification','notification.reminders_enqueued','success','events.outbox',null,
  jsonb_build_object('event_count',inserted),p_actor_type=>'system',p_actor_id=>'notification_worker'); end if;
 return cases+inserted;
end $$;

create or replace function public.customer_read_compliance(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select jsonb_build_object('case_id',c.case_id,'case_type',c.case_type,'title',c.title,'notice_text',c.notice_text,
    'notice_date',c.notice_date,'due_date',c.due_date,'status',c.status,'status_updated_at',c.status_updated_at,
    'requested_items',coalesce((select jsonb_agg(to_jsonb(i) order by i.sort_order,i.item_id) from(
      select item_id,label,instructions,check_guidance,submission_status,client_mark,client_marked_at,sort_order,updated_at
      from compliance.requested_items where customer_id=cid and case_id=c.case_id order by sort_order,item_id limit 200)i),'[]'),
    'status_history',coalesce((select jsonb_agg(to_jsonb(h) order by h.changed_at desc) from(
      select status,note,changed_at from compliance.status_history where customer_id=cid and case_id=c.case_id order by changed_at desc,id desc limit 200)h),'[]'),
    'info_requests',coalesce((select jsonb_agg(to_jsonb(r) order by r.created_at desc,r.request_id) from(
      select request_id,question,instructions,due_date,status,responded_at,created_at
      from compliance.info_requests where customer_id=cid and case_id=c.case_id order by created_at desc,request_id limit 200)r),'[]'))
    into data from compliance.cases c where c.customer_id=cid order by c.notice_date desc,c.case_id limit 1;
  return banking.customer_result(cid,'compliance.get_case','compliance.cases',data);
end $$;


create or replace function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; review compliance.cases; request compliance.info_requests;
 command actions.commands; receipt actions.receipts; template notify.templates; v_locale text; key text; vars jsonb:='{}';
 action_text text; due_date date; notification uuid; priority text:='P2'; prefs notify.preferences; v_service boolean; local_now timestamp;
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
 if template.template_key is null then
  v_locale:=compliance.policy()->>'notification_fallback_locale';
  select * into template from notify.templates where template_key=key and channel='in_app' and locale=v_locale order by version desc limit 1;
 end if;
 if template.template_key is null then raise exception 'approved notification locale unavailable' using errcode='P2001'; end if;
 v_service:=p_event.event_type='document.submitted' or (compliance.policy()->>'reminders_are_service')::boolean;
 if not v_service then
  select * into prefs from notify.preferences where customer_id=cid;
  if prefs.customer_id is null then raise exception 'notification preferences unavailable' using errcode='P2004'; end if;
  if not ('in_app'=any(prefs.channels)) then return jsonb_build_object('disposition','suppressed','notification_id',null,'available_at',null); end if;
  local_now:=clock_timestamp() at time zone (compliance.policy()->>'timezone');
  if prefs.quiet_start is not null and ((prefs.quiet_start<prefs.quiet_end and local_now::time>=prefs.quiet_start and local_now::time<prefs.quiet_end)
    or (prefs.quiet_start>prefs.quiet_end and (local_now::time>=prefs.quiet_start or local_now::time<prefs.quiet_end))) then
   return jsonb_build_object('disposition','deferred','notification_id',null,'available_at',clock_timestamp()+interval '1 minute');
  end if;
 end if;
 insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
 values(cid,p_event.event_id,key,template.version,v_locale,priority,template.title,notify.render(template.body,vars),v_service,'event:'||p_event.event_id)
 on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
 if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
 perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
  jsonb_build_object('event_id',p_event.event_id,'template',key,'version',template.version,'priority',priority),
  p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
 return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
