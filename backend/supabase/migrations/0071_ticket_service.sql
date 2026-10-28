-- Ticket Service (F19), request category catalogue (F20) and Persona 1 demo data (CL1, TK1, TK2, EB8).
-- A ticket is a customer-visible record of something the bank still has to do. The chatbot guides and records;
-- people in the owner team decide. The bot never marks a ticket resolved: only staff (or the owner via
-- support.set_ticket_status in the SQL editor) change status, and support handoffs / RM requests keep their
-- own status, which their linked ticket mirrors.

-- 1. Category catalogue (public configuration, versioned, never in prompts).
create table support.request_categories (
  category          text primary key check (category ~ '^[a-z_]{3,40}$'),
  version           int not null default 1 check (version>0),
  label             text not null check (length(label) between 1 and 80),
  description       text not null check (length(description) between 1 and 400),
  owner_team        text not null check (length(owner_team) between 1 and 80),
  decided_by        text not null check (length(decided_by) between 1 and 120),
  target_hours      int not null check (target_hours between 1 and 720),
  fields            jsonb not null default '[]' check (jsonb_typeof(fields)='array' and jsonb_array_length(fields)<=10),
  documents         text[] not null default '{}',
  kb_article        text,
  customer_raisable boolean not null default true
);
alter table support.request_categories enable row level security;
create policy categories_public on support.request_categories for select to anon,authenticated using (true);
grant select on support.request_categories to anon,authenticated;

-- fields: key, label, kind (text|amount|date|choice|card), required, optional choices. 'card' = last 4 digits of one of the customer's cards.
insert into support.request_categories(category,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable) values
 ('card_limit_change','Card limit change','Ask for a higher or lower credit card limit, for a period or permanently.','Cards and credit team',
  'The cards and credit team decides after its own checks. The assistant cannot approve or reject a limit.',72,
  '[{"key":"card","label":"Card (last 4 digits)","kind":"card","required":true},
    {"key":"new_limit","label":"Requested limit (SGD)","kind":"amount","required":true},
    {"key":"duration","label":"Temporary or permanent","kind":"choice","required":true,"choices":["temporary","permanent"]},
    {"key":"until","label":"Needed until (temporary only)","kind":"date","required":false},
    {"key":"reason","label":"Reason","kind":"text","required":true}]',
  array['Recent proof of income, if the bank asks for it'],'card-limit-change',true),
 ('statement_request','Statement or document copy','Ask for a copy of a statement or bank letter.','Operations team',
  'Operations prepares the document; there is no decision to make.',48,
  '[{"key":"account","label":"Account or card","kind":"text","required":true},
    {"key":"period","label":"Period, for example June 2026","kind":"text","required":true},
    {"key":"format","label":"Format","kind":"choice","required":true,"choices":["e-statement","paper"]}]',
  '{}','statements',true),
 ('personal_details_update','Update personal details','Change your address, phone number or email address.','Client records team',
  'The client records team checks the documents and makes the change.',72,
  '[{"key":"change","label":"What changed","kind":"choice","required":true,"choices":["address","phone","email"]},
    {"key":"details","label":"Details of the change","kind":"text","required":true}]',
  array['Proof of address for an address change'],null,true),
 ('payment_issue','Payment or transfer issue','A payment that failed, is still pending or went to the wrong place.','Payments operations',
  'Payments operations investigates. The assistant cannot reverse, cancel or speed up a payment.',48,
  '[{"key":"payment_date","label":"Payment date","kind":"date_past","required":true},
    {"key":"amount","label":"Amount (SGD)","kind":"amount","required":true},
    {"key":"payee","label":"Payee","kind":"text","required":true},
    {"key":"issue","label":"What went wrong","kind":"text","required":true}]',
  '{}','transfer-timing',true),
 ('login_access','Login or app access','You cannot sign in or the app is locked.','Digital banking support',
  'Digital banking support checks the account. The assistant never asks for your password or one-time code.',24,
  '[{"key":"issue","label":"What happens when you sign in","kind":"text","required":true},
    {"key":"device","label":"Device, for example iPhone app","kind":"text","required":false}]',
  '{}','app-login-recovery',true),
 ('compliance_documents','Compliance review question','A question about your compliance review or the documents it asks for.','Compliance review team',
  'The compliance review team decides whether documents are accepted.',72,
  '[{"key":"document","label":"Which document or request","kind":"text","required":true},
    {"key":"question","label":"Your question","kind":"text","required":true}]',
  '{}',null,true),
 ('general_support','Something else','Anything the assistant could not resolve.','Client service team',
  'The client service team reviews it and replies through your usual channel.',72,
  '[{"key":"issue","label":"What you need","kind":"text","required":true}]','{}','support',true),
 ('suspected_fraud','Suspected fraud','Raised automatically when you report a charge you do not recognise.','Fraud operations',
  'Fraud operations verifies your identity and decides on refunds or cancellation.',4,'[]','{}','unrecognised-charge',false),
 ('rm_request','Request to your relationship manager','Raised automatically when you send your relationship manager a request.','Your relationship manager',
  'Your relationship manager handles the request.',48,'[]','{}','rm-change',false);

-- 2. Tickets and their public history.
create sequence support.ticket_reference_seq start 1001;
create table support.tickets (
  ticket_id    uuid primary key default gen_random_uuid(),
  reference    text not null unique default 'TCK-'||lpad(nextval('support.ticket_reference_seq')::text,6,'0'),
  customer_id  bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  case_id      uuid references conversation.cases(case_id) on delete set null,
  category     text not null references support.request_categories(category),
  summary      text not null check (length(summary) between 1 and 500),  -- redacted, customer-visible
  details      jsonb not null default '{}' check (jsonb_typeof(details)='object'),
  urgency      text not null check (urgency in ('low','normal','high','urgent')),
  owner_team   text not null,
  decided_by   text not null,
  status       text not null default 'submitted' check (status in ('submitted','in_review','waiting_on_client','resolved','closed')),
  source       text not null check (source in ('form','chat','handoff','rm_request','seed')),
  target_at    timestamptz not null,
  command_id   uuid unique references actions.commands(command_id),
  handoff_id   uuid unique references support.handoffs(handoff_id) on delete set null,
  request_id   uuid unique references rm.requests(request_id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
-- One open customer-raised ticket per category, so asking twice shows the existing ticket instead of a duplicate.
create unique index tickets_one_open_form_idx on support.tickets(customer_id,category)
  where source in ('form','chat') and status in ('submitted','in_review','waiting_on_client');
create index tickets_customer_idx on support.tickets(customer_id,created_at desc);
create index tickets_queue_idx on support.tickets(owner_team,status,target_at);

create table support.ticket_events (
  event_id    bigint generated always as identity primary key,
  ticket_id   uuid not null references support.tickets(ticket_id) on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  status      text not null check (status in ('submitted','in_review','waiting_on_client','resolved','closed')),
  public_note text check (public_note is null or length(public_note) between 1 and 500),
  internal    boolean not null default false,   -- internal notes are never returned to customers
  created_at  timestamptz not null default now()
);
create index ticket_events_ticket_idx on support.ticket_events(ticket_id,created_at);

alter table support.tickets enable row level security;
alter table support.ticket_events enable row level security;
grant select on support.tickets,support.ticket_events to authenticated;
create policy own_rows on support.tickets for select to authenticated using (customer_id=(select banking.my_customer_id()));
create policy own_public_rows on support.ticket_events for select to authenticated
  using (customer_id=(select banking.my_customer_id()) and not internal);

create function support.status_note(p_status text,p_team text) returns text language sql immutable set search_path='' as $$
  select case p_status
    when 'submitted' then 'Received. The '||lower(p_team)||' will review it.'
    when 'in_review' then 'The '||lower(p_team)||' is reviewing it.'
    when 'waiting_on_client' then 'The bank needs something from you before it can continue.'
    when 'resolved' then 'Marked resolved by the bank.'
    else 'Closed.' end $$;

-- Internal helper used by triggers and the executor: one insert path for every ticket.
create function support.open_ticket(p_customer_id bigint,p_case_id uuid,p_category text,p_summary text,p_details jsonb,p_urgency text,
  p_source text,p_command_id uuid default null,p_handoff_id uuid default null,p_request_id uuid default null)
returns support.tickets language plpgsql volatile security definer set search_path='' as $$
declare cat support.request_categories; ticket support.tickets;
begin
  select * into strict cat from support.request_categories where category=p_category;
  insert into support.tickets(customer_id,case_id,category,summary,details,urgency,owner_team,decided_by,source,target_at,command_id,handoff_id,request_id)
    values(p_customer_id,p_case_id,p_category,p_summary,coalesce(p_details,'{}'),p_urgency,cat.owner_team,cat.decided_by,p_source,
      clock_timestamp()+make_interval(hours=>cat.target_hours),p_command_id,p_handoff_id,p_request_id) returning * into ticket;
  insert into support.ticket_events(ticket_id,customer_id,status,public_note) values(ticket.ticket_id,p_customer_id,'submitted',support.status_note('submitted',cat.owner_team));
  perform audit.record('handoff','handoff.ticket_created','success','support.tickets',ticket.ticket_id::text,
    jsonb_build_object('category',p_category,'source',p_source,'urgency',p_urgency),p_case_id,p_customer_id=>p_customer_id);
  return ticket;
end $$;

create function support.move_ticket(p_ticket_id uuid,p_status text,p_note text default null) returns void
language plpgsql volatile security definer set search_path='' as $$
declare ticket support.tickets;
begin
  select * into ticket from support.tickets where ticket_id=p_ticket_id for update;
  if not found or ticket.status=p_status then return; end if;
  update support.tickets set status=p_status,updated_at=clock_timestamp() where ticket_id=p_ticket_id;
  insert into support.ticket_events(ticket_id,customer_id,status,public_note)
    values(p_ticket_id,ticket.customer_id,p_status,coalesce(p_note,support.status_note(p_status,ticket.owner_team)));
  perform audit.record('handoff','handoff.ticket_status_changed','success','support.tickets',p_ticket_id::text,
    jsonb_build_object('from',ticket.status,'to',p_status),ticket.case_id,p_customer_id=>ticket.customer_id);
end $$;

-- 3. Support handoffs (fraud, validation failures, Talk to a person) and RM requests each get a ticket with a reference.
create function support.ticket_for_handoff() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if tg_op='INSERT' and new.customer_id is not null then
    perform support.open_ticket(new.customer_id,new.case_id,
      case when new.reason in ('card_verification','card_dispute') then 'suspected_fraud' else 'general_support' end,
      case new.reason when 'card_verification' then 'Card blocked after a suspected unauthorised charge: identity check and card decision.'
        when 'card_dispute' then 'Disputed card charges for bank staff review.'
        else 'Support request passed to the client service team.' end,
      '{}',case new.priority when 'P0' then 'urgent' when 'P1' then 'high' else 'normal' end,'handoff',p_handoff_id=>new.handoff_id);
  elsif tg_op='UPDATE' and new.status is distinct from old.status then
    perform support.move_ticket(t.ticket_id,case new.status when 'claimed' then 'in_review' when 'closed' then 'resolved' else 'submitted' end)
      from support.tickets t where t.handoff_id=new.handoff_id;
  end if;
  return null;
end $$;
create trigger ticket_sync after insert or update of status on support.handoffs for each row execute function support.ticket_for_handoff();

create function support.ticket_for_request() returns trigger language plpgsql security definer set search_path='' as $$
begin
  if tg_op='INSERT' then
    perform support.open_ticket(new.customer_id,null,'rm_request','Request to your relationship manager: '||left(new.what,400),
      '{}',case new.urgency when 'high' then 'high' when 'low' then 'low' else 'normal' end,'rm_request',p_request_id=>new.request_id);
  elsif tg_op='UPDATE' and new.status is distinct from old.status then
    perform support.move_ticket(t.ticket_id,case new.status when 'seen' then 'in_review' when 'answered' then 'resolved' when 'closed' then 'closed' else 'submitted' end)
      from support.tickets t where t.request_id=new.request_id;
  end if;
  return null;
end $$;
create trigger ticket_sync after insert or update of status on rm.requests for each row execute function support.ticket_for_request();
-- Existing open RM requests get their ticket too, so My requests is complete from the start.
do $$
declare r rm.requests; t support.tickets;
begin
  for r in select * from rm.requests where status<>'closed' and request_id not in (select request_id from support.tickets where request_id is not null) order by created_at loop
    t:=support.open_ticket(r.customer_id,null,'rm_request','Request to your relationship manager: '||left(r.what,400),'{}',
      case r.urgency when 'high' then 'high' when 'low' then 'low' else 'normal' end,'rm_request',p_request_id=>r.request_id);
    if r.status<>'sent' then perform support.move_ticket(t.ticket_id,case r.status when 'seen' then 'in_review' else 'resolved' end); end if;
  end loop;
end $$;

-- 4. ticket.create action: the customer reviews the exact ticket in Actions and confirms it.
create function actions.normalise_ticket(p_target jsonb)
returns jsonb language plpgsql immutable set search_path='' as $$
declare details jsonb:=coalesce(p_target->'details','{}'); kv record;
begin
  if jsonb_typeof(p_target->'category') is distinct from 'string' or p_target->>'category' !~ '^[a-z_]{3,40}$'
      or jsonb_typeof(p_target->'summary') is distinct from 'string' or length(p_target->>'summary') not between 1 and 500
      or p_target->>'summary' !~ '[^[:space:]]' or p_target->>'urgency' is null or p_target->>'urgency' not in ('low','normal','high')
      or jsonb_typeof(details)<>'object' or (select count(*) from jsonb_object_keys(details))>10 then
    raise exception 'a ticket needs a category, a 1-500 character summary, low/normal/high urgency and up to 10 detail fields' using errcode='22023';
  end if;
  perform conversation.require_redacted(p_target->>'summary');
  for kv in select * from jsonb_each(details) loop
    if kv.key !~ '^[a-z_]{2,30}$' or jsonb_typeof(kv.value)<>'string' or length(kv.value#>>'{}')>300 then
      raise exception 'ticket details must be short text values' using errcode='22023';
    end if;
    perform conversation.require_redacted(kv.value#>>'{}');
  end loop;
  if p_target::text ~ '(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+)' then
    raise exception 'remove credentials from the ticket' using errcode='22023';
  end if;
  return jsonb_build_object('category',p_target->>'category','summary',p_target->>'summary','urgency',p_target->>'urgency',
    'details',(select coalesce(jsonb_object_agg(key,btrim(value#>>'{}')),'{}') from jsonb_each(details) where btrim(value#>>'{}')<>''));
end $$;

create function actions.ticket_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_ticket(p_target); cat support.request_categories; field jsonb; value text; key text;
  today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date;
begin
  select * into cat from support.request_categories where category=target->>'category' and customer_raisable;
  if not found then raise exception 'this request category cannot be raised by a customer' using errcode='22023'; end if;
  for key in select jsonb_object_keys(target->'details') loop
    if not exists(select 1 from jsonb_array_elements(cat.fields) f where f->>'key'=key) then
      raise exception 'ticket detail % is not part of this category',key using errcode='22023';
    end if;
  end loop;
  for field in select * from jsonb_array_elements(cat.fields) loop
    value:=target->'details'->>(field->>'key');
    if value is null then
      if (field->>'required')::boolean then raise exception 'ticket detail % is required',field->>'key' using errcode='22023'; end if;
      continue;
    end if;
    if field->>'kind'='amount' and value !~ '^[0-9]{1,9}(\.[0-9]{1,2})?$'
        or field->>'kind'='choice' and not (field->'choices' ? value)
        or field->>'kind' in ('date','date_past') and value !~ '^\d{4}-\d\d-\d\d$'
        or field->>'kind'='card' and value !~ '^[0-9]{4}$' then
      raise exception 'ticket detail % has the wrong format',field->>'key' using errcode='22023';
    end if;
    if field->>'kind' in ('date','date_past') then
      begin
        if field->>'kind'='date' and value::date<today or field->>'kind'='date_past' and value::date>today then
          raise exception 'ticket date % is outside the allowed range',field->>'key' using errcode='22023';
        end if;
      exception when invalid_datetime_format or datetime_field_overflow then
        raise exception 'ticket date % is not a calendar date',field->>'key' using errcode='22023';
      end;
    end if;
    if field->>'kind'='card' and not exists(select 1 from banking.credit_cards c where c.customer_id=p_customer_id and c.last4=value
        and c.status in ('active','temporarily_blocked')) then
      raise exception 'the card is not one of your open cards' using errcode='42501';
    end if;
  end loop;
  return target||jsonb_build_object('category_label',cat.label,'owner_team',cat.owner_team,'decided_by',cat.decided_by,'target_hours',cat.target_hours);
end $$;

create function actions.execute_ticket(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare ticket support.tickets; created boolean:=false;
begin
  if actions.ticket_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the request category changed; review a new ticket' using errcode='P1001';
  end if;
  select * into ticket from support.tickets where customer_id=p_command.customer_id and category=p_command.target->>'category'
    and source in ('form','chat') and status in ('submitted','in_review','waiting_on_client') for update;
  if not found then
    ticket:=support.open_ticket(p_command.customer_id,p_command.case_id,p_command.target->>'category',p_command.target->>'summary',
      p_command.target->'details',p_command.target->>'urgency','form',p_command_id=>p_command.command_id);
    created:=true;
  end if;
  return jsonb_build_object('result',jsonb_build_object('ticket_id',ticket.ticket_id,'reference',ticket.reference,'category',ticket.category,
      'status',ticket.status,'owner_team',ticket.owner_team,'target_at',ticket.target_at,'created',created),
    'event_type',case when created then 'ticket.recorded' else 'ticket.reused' end);
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('ticket.create','actions.ticket_target(jsonb,bigint)'::regprocedure,'actions.execute_ticket(actions.commands)'::regprocedure,
    true,'actions.normalise_ticket(jsonb)'::regprocedure,array['ticket.recorded','ticket.reused']);

-- 5. Reads: categories for everyone; a customer's own tickets as an audited snapshot. No internal notes.
create function public.read_request_categories()
returns jsonb language sql stable security definer set search_path='' as $$
  select coalesce(jsonb_agg(jsonb_build_object('category',category,'version',version,'label',label,'description',description,
    'owner_team',owner_team,'decided_by',decided_by,'target_hours',target_hours,'fields',fields,'documents',to_jsonb(documents),
    'kb_article',kb_article,'customer_raisable',customer_raisable) order by customer_raisable desc,category),'[]')
  from support.request_categories $$;

create function public.customer_read_tickets(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(item order by (item->>'created_at')::timestamptz desc),'[]') into data from (
    select jsonb_build_object('ticket_id',t.ticket_id,'reference',t.reference,'category',t.category,'category_label',c.label,
      'summary',t.summary,'details',t.details,'urgency',t.urgency,'status',t.status,'owner_team',t.owner_team,'decided_by',t.decided_by,
      'source',t.source,'created_at',t.created_at,'updated_at',t.updated_at,'target_at',t.target_at,
      'overdue',t.status in ('submitted','in_review') and t.target_at<clock_timestamp(),
      'last_update',(select jsonb_build_object('at',e.created_at,'status',e.status,'note',e.public_note) from support.ticket_events e
        where e.ticket_id=t.ticket_id and not e.internal order by e.created_at desc,e.event_id desc limit 1)) item
    from support.tickets t join support.request_categories c using(category)
    where t.customer_id=cid order by t.created_at desc limit 200) x;
  return banking.customer_result(cid,'tickets.list','support.tickets',data);
end $$;
revoke all on function public.customer_read_tickets(bigint) from public,anon;
grant execute on function public.customer_read_tickets(bigint) to authenticated;
grant execute on function public.read_request_categories() to anon,authenticated;

-- Owner/staff tool for the demo (SQL editor only): move a ticket and leave a customer-visible note.
create function support.set_ticket_status(p_reference text,p_status text,p_public_note text default null) returns void
language plpgsql volatile security definer set search_path='' as $$
declare id uuid;
begin
  select ticket_id into id from support.tickets where reference=p_reference;
  if id is null then raise exception 'ticket % not found',p_reference; end if;
  if p_public_note is not null then perform conversation.require_redacted(p_public_note); end if;
  perform support.move_ticket(id,p_status,p_public_note);
end $$;
revoke all on function support.open_ticket(bigint,uuid,text,text,jsonb,text,text,uuid,uuid,uuid),support.move_ticket(uuid,text,text),
  support.set_ticket_status(text,text,text),support.ticket_for_handoff(),support.ticket_for_request(),
  actions.normalise_ticket(jsonb),actions.ticket_target(jsonb,bigint),actions.execute_ticket(actions.commands) from public,anon,authenticated;

-- 6. In-app notices for a recorded or reused ticket.
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('ticket.recorded',1,'en','in_app','Request {reference} recorded','Your demo request {reference} is with the {owner_team}. They decide the outcome; the assistant does not. Check My requests for updates.',true),
 ('ticket.reused',1,'en','in_app','Request {reference} is already open','You already have an open request {reference} of this kind, so no duplicate was created. Check My requests for its status.',true);
alter function notify.deliver(events.outbox) rename to deliver_before_tickets;
revoke all on function notify.deliver_before_tickets(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; command actions.commands; receipt actions.receipts; template notify.templates;
 v_locale text; notification uuid; urgent boolean;
begin
 if p_event.event_type not in ('ticket.recorded','ticket.reused') then return notify.deliver_before_tickets(p_event); end if;
 if cid is null then raise exception 'customer required' using errcode='P2002'; end if;
 select * into command from actions.commands where command_id=p_event.command_id and customer_id=cid;
 select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=cid;
 if command.type is distinct from 'ticket.create' or receipt.outcome is distinct from 'succeeded'
  or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id or p_event.payload->>'command_id' is distinct from command.command_id::text
  or not exists(select 1 from support.tickets t where t.customer_id=cid and t.ticket_id::text=receipt.result->>'ticket_id') then
  raise exception 'ticket event disagrees with its receipt' using errcode='P2002';
 end if;
 select preferred_language into v_locale from banking.customer_profiles where customer_id=cid;
 select * into template from notify.templates where template_key=p_event.event_type and channel='in_app' and locale=v_locale order by version desc limit 1;
 if template.template_key is null then raise exception 'notification locale unavailable' using errcode='P2001'; end if;
 urgent:=command.target->>'urgency'='high';
 insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
 values(cid,p_event.event_id,template.template_key,template.version,v_locale,case when urgent then 'P1' else 'P3' end,
  notify.render(template.title,jsonb_build_object('reference',receipt.result->>'reference')),
  notify.render(template.body,jsonb_build_object('reference',receipt.result->>'reference','owner_team',lower(receipt.result->>'owner_team'))),true,
  'ticket:'||(receipt.result->>'ticket_id')||':'||p_event.event_type)
 on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
 if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
 perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
  jsonb_build_object('event_id',p_event.event_id,'template',template.template_key,'version',template.version,'priority',case when urgent then 'P1' else 'P3' end),
  p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
 return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- 7. Approved card-limit guidance (fictional) for CL1 answers.
insert into kb.articles(article_id,version,title,categories,publisher,scope,kind,status,reference_url,checked_on,review_due) values
  ('card-limit-change',1,'Changing your credit card limit',array['retail'],'DemoBank coursework project','Fictional demo guidance; not a live bank policy.','demo','published',null,null,null);
insert into kb.sections(article_id,version,anchor,heading,body,keywords) values
  ('card-limit-change',1,'main','Changing your credit card limit','You can ask DemoBank for a higher or lower credit card limit, either for a period (for example a trip) or permanently. In this demo you raise the request from My requests or from the assistant: give the card, the limit you want, whether it is temporary or permanent, the end date for a temporary change and the reason. The cards and credit team decides after its own checks, which can include asking for recent proof of income. The assistant cannot approve, reject or predict the outcome of a limit request, and a request does not change your limit until the bank confirms it. You get a request reference and can check its status under My requests. These are synthetic demo terms, not a live bank service standard.','credit limit card limit increase decrease higher lower raise trip temporary permanent limit change');

-- 8. Persona 1 demo data: Michelle now owns everyday banking needs as well as her compliance review.
insert into banking.credit_cards(card_id,customer_id,card_name,network,last4,credit_limit,outstanding_balance,minimum_payment_due,payment_due_date,status) values
  (10021,1002,'Signature Card','mastercard','7310',20000.00,1250.40,50.00,'2026-11-20','active')
on conflict(card_id) do nothing;
do $$
declare t support.tickets;
begin
  if exists(select 1 from banking.customer_profiles where customer_id=1002) then
    t:=support.open_ticket(1002,null,'statement_request','Copy of the June 2026 statement for my multi-currency account.',
      '{"account":"Multi-currency account","period":"June 2026","format":"e-statement"}','normal','seed');
    perform support.move_ticket(t.ticket_id,'in_review');
    t:=support.open_ticket(1002,null,'personal_details_update','Update my residential address after moving.',
      '{"change":"address","details":"New residential address in Singapore"}','normal','seed');
    perform support.move_ticket(t.ticket_id,'waiting_on_client','Please upload proof of your new address in your compliance review so the client records team can make the change.');
  end if;
end $$;

-- Row-change audit for the new tables, like every other business table (0006).
create trigger audit_row after insert or update or delete on support.tickets for each row execute function audit.row_change('ticket_id','category','urgency','status','source');
