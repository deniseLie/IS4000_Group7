-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0080$
declare previous supabase_migrations.schema_migrations; script text:=$source_0080$-- Released source SHA256: b0e6a2a6741f3268d12ee6aef571edc1036adc75eba91e50f0b099690c123ace
-- Michelle answers the bank's questions on her review (CR6), and the bank closes them.
--
-- How a request ("The bank asked you") is passed:
--   1. open       – the bank asked; it shows on her Compliance review with a reply box, and reminders run until it is due.
--   2. responded  – she sends her answer (action compliance.answer_request, receipt in Actions). It shows "Sent to the bank
--                   on … – waiting for review"; reminders for it stop and disappear (0078). An Inbox notice confirms it.
--   3. closed     – the bank's reviewer records the outcome with one call in the SQL editor (owner only, until Workflow 2):
--        select compliance.record_request_decision('<request_id>','accepted');
--        select compliance.record_request_decision('<request_id>','more_info','Please also send the sale agreement.');
--      accepted: shows "Accepted by bank". more_info: shows "The bank asked a follow-up question" and opens a new request.
-- The answer is redacted before it is stored and is never treated as an instruction.

alter table compliance.info_requests
  add column response text check (response is null or length(btrim(response)) between 1 and 2000),
  add column outcome text check (outcome is null or outcome in ('accepted','more_info')),
  add column follow_up_of uuid references compliance.info_requests(request_id) on delete set null;

-- Customer action: answer one open request on her own open review.
create function actions.normalise_request_answer(p_target jsonb)
returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'case_id') is distinct from 'string' or p_target->>'case_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'request_id') is distinct from 'string' or p_target->>'request_id' !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'answer') is distinct from 'string' or length(btrim(p_target->>'answer')) not between 1 and 2000 then
    raise exception 'an answer needs the review, the request and 1 to 2,000 characters of text' using errcode='22023';
  end if;
  return jsonb_build_object('case_id',(p_target->>'case_id')::uuid,'request_id',(p_target->>'request_id')::uuid,'answer',btrim(p_target->>'answer'));
end $$;

create function actions.request_answer_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_request_answer(p_target); review compliance.cases; request compliance.info_requests;
begin
  perform conversation.require_redacted(target->>'answer');
  select * into review from compliance.cases where case_id=(target->>'case_id')::uuid and customer_id=p_customer_id;
  select * into request from compliance.info_requests where request_id=(target->>'request_id')::uuid
    and case_id=(target->>'case_id')::uuid and customer_id=p_customer_id;
  if review.case_id is null or request.request_id is null then
    raise exception 'the request is outside this customer scope' using errcode='42501';
  end if;
  if review.status in ('completed','closed') or request.status<>'open' then
    raise exception 'this request is no longer open' using errcode='22023';
  end if;
  return target||jsonb_build_object('question',left(request.question,500));
end $$;

create function actions.execute_request_answer(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare review compliance.cases; request compliance.info_requests; answered_at timestamptz:=clock_timestamp();
begin
  select * into review from compliance.cases where case_id=(p_command.target->>'case_id')::uuid and customer_id=p_command.customer_id for share;
  select * into request from compliance.info_requests where request_id=(p_command.target->>'request_id')::uuid
    and case_id=review.case_id and customer_id=p_command.customer_id for update;
  if review.case_id is null or request.request_id is null or review.status in ('completed','closed') or request.status<>'open'
      or actions.request_answer_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the request changed; reload before answering' using errcode='P1001';
  end if;
  update compliance.info_requests set status='responded',responded_at=answered_at,response=p_command.target->>'answer'
    where request_id=request.request_id;
  update compliance.cases set status_updated_at=answered_at where case_id=review.case_id;
  perform audit.record('action','action.request_answered','success','compliance.info_requests',request.request_id::text,
    jsonb_build_object('answer_length',length(p_command.target->>'answer')),p_command.case_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','responded','responded_at',answered_at),
    'event_type','compliance.request_answered');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('compliance.answer_request','actions.request_answer_target(jsonb,bigint)'::regprocedure,'actions.execute_request_answer(actions.commands)'::regprocedure,
    false,'actions.normalise_request_answer(jsonb)'::regprocedure,array['compliance.request_answered']);
revoke all on function actions.normalise_request_answer(jsonb),actions.request_answer_target(jsonb,bigint),actions.execute_request_answer(actions.commands)
  from public,anon,authenticated;

-- Inbox notice that the answer reached the bank; every other event keeps its released delivery.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('compliance.request_answered',1,'en','in_app','Your answer was sent to the bank','Your answer to the bank''s question on your compliance review was sent. The bank will review it; you will see the outcome on your Compliance review. See Actions for the receipt.',true);
alter function notify.deliver(events.outbox) rename to deliver_before_request_answers;
revoke all on function notify.deliver_before_request_answers(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; command actions.commands; receipt actions.receipts; template notify.templates;
 v_locale text; notification uuid;
begin
 if p_event.event_type<>'compliance.request_answered' then return notify.deliver_before_request_answers(p_event); end if;
 if cid is null then raise exception 'customer required' using errcode='P2002'; end if;
 select * into command from actions.commands where command_id=p_event.command_id and customer_id=cid;
 select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=cid;
 if command.type is distinct from 'compliance.answer_request' or receipt.outcome is distinct from 'succeeded'
  or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id or p_event.payload->>'command_id' is distinct from command.command_id::text
  or receipt.result->>'status' is distinct from 'responded' then
  raise exception 'answer event disagrees with its receipt' using errcode='P2002';
 end if;
 select preferred_language into v_locale from banking.customer_profiles where customer_id=cid;
 select * into template from notify.templates where template_key=p_event.event_type and channel='in_app' and locale=v_locale order by version desc limit 1;
 if template.template_key is null then
  select * into template from notify.templates where template_key=p_event.event_type and channel='in_app' and locale='en' order by version desc limit 1;
  v_locale:='en';
 end if;
 insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
 values(cid,p_event.event_id,template.template_key,template.version,v_locale,'P3',template.title,template.body,true,'event:'||p_event.event_id)
 on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
 if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
 perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
  jsonb_build_object('event_id',p_event.event_id,'template',template.template_key,'version',template.version,'priority','P3'),
  p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
 return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- The bank's decision on her answer. Owner only.
create function compliance.record_request_decision(p_request_id uuid,p_decision text,p_note text default null,p_due date default null)
returns text language plpgsql security definer set search_path='' as $$
declare request compliance.info_requests; review compliance.cases;
begin
  if p_decision not in ('accepted','more_info') then raise exception 'decision must be accepted or more_info' using errcode='22023'; end if;
  if p_decision='more_info' and nullif(btrim(coalesce(p_note,'')),'') is null then
    raise exception 'say what else the client needs to answer' using errcode='22023';
  end if;
  select * into request from compliance.info_requests where request_id=p_request_id for update;
  if not found then raise exception 'request not found' using errcode='P0002'; end if;
  select * into review from compliance.cases where case_id=request.case_id for share;
  if review.status in ('completed','closed') then raise exception 'the review is closed' using errcode='22023'; end if;
  if request.status<>'responded' then
    raise exception 'only an answered request can be decided (current: %)',request.status using errcode='22023';
  end if;
  update compliance.info_requests set status='closed',outcome=p_decision where request_id=request.request_id;
  if p_decision='more_info' then
    insert into compliance.info_requests(case_id,customer_id,item_id,question,instructions,due_date,follow_up_of)
    values(request.case_id,request.customer_id,request.item_id,btrim(p_note),'Reply under Compliance review.',
      coalesce(p_due,request.due_date,review.due_date),request.request_id);
  end if;
  update compliance.cases set status_updated_at=clock_timestamp() where case_id=request.case_id;
  perform audit.record('document','document.request_decided','success','compliance.info_requests',request.request_id::text,
    jsonb_build_object('decision',p_decision),p_customer_id=>request.customer_id);
  return case p_decision when 'accepted' then 'Answer accepted' else 'Follow-up question sent' end;
end $$;
revoke all on function compliance.record_request_decision(uuid,text,text,date) from public,anon,authenticated;

-- Her review now also returns her own answer and the bank's outcome for each request.
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
      select request_id,question,instructions,due_date,status,responded_at,created_at,response,outcome
      from compliance.info_requests where customer_id=cid and case_id=c.case_id order by created_at desc,request_id limit 200)r),'[]'))
    into data from compliance.cases c where c.customer_id=cid order by c.notice_date desc,c.case_id limit 1;
  return banking.customer_result(cid,'compliance.get_case','compliance.cases',data);
end $$;

-- Michelle's seeded question pointed to "secure messaging", which the demo does not have; it now points to the reply box.
update compliance.info_requests set instructions='Answer in the reply box below. If the bank needs a document as well, it will ask for it here.'
where request_id='00000000-0000-4000-8000-100200000021' and instructions like 'Reply through secure messaging%';
$source_0080$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0080';
  if found then
    if previous.name is distinct from 'answer_bank_requests' or previous.statements is distinct from array[script] then
      raise exception 'migration 0080 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0080 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0080','answer_bank_requests',array[script]);
  end if;
end $apply_0080$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
