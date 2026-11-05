-- Consent v2, part 5 (RM copilot build plan phase 6d): the RM asks, the client decides; "Delete all my data" is a request.
--   1. Request categories on the 0071 tickets pattern: rm_consent_request (raised only by the RM's approved ask; lapses
--      after 14 days) and data_deletion (Delete all my data: raised by the client, a 10-business-day target).
--   2. consent.asks: one open ask per client, RM and category. public.rm_ask_consent (the RM's approved card through
--      POST /rm/consent-requests; the copilot only proposes it) opens a ticket waiting on the client and an Inbox notice.
--      The reason is a code with fixed wording (meeting_prep with her meeting, review, advice), never RM free text.
--   3. Her answer: switching the category on (any v2 consent.grant to that RM) marks the ask shared and resolves the
--      ticket; consent.decline_ask (automatic, receipt) closes it; an ask she does not answer lapses after 14 days.
--      Nothing is shared by an ask itself.
--   4. customer_read_consent_asks (her JWT) for Data sharing; the demo reset clears asks.
-- Audit: consent.rm_asked (staff), consent.ask_answered (shared or declined), consent.ask_lapsed (system); ids and codes.

-- 1. Request categories ------------------------------------------------------------------------------------------------
insert into support.request_categories(category,label,description,owner_team,decided_by,target_hours,fields,documents,kb_article,customer_raisable) values
 ('rm_consent_request','Your relationship manager asks you to share','Your relationship manager asked to see something you have not shared. '
  'Answer in Data sharing: switch it on, or choose not to share. Nothing is shared unless you switch it on.','Your relationship manager',
  'You decide. Your relationship manager cannot switch sharing on for you.',336,'[]','{}',null,false),
 ('data_deletion','Delete all my data','Ask DemoBank to delete the personal data it holds about you, apart from what the law requires the bank to keep.',
  'Data protection team','The data protection team decides what the law requires the bank to keep and replies within 10 business days.',240,'[{"key":"note","label":"Anything the team should know (optional)","kind":"text","required":false}]',
  '{}',null,true);

-- 10 business days (Monday to Friday; public holidays are not counted), by the end of the day in Singapore.
create function support.business_days_after(p_from timestamptz,p_days int) returns timestamptz language sql stable set search_path='' as $$
  select ((select d::date from generate_series((p_from at time zone 'Asia/Singapore')::date+1,(p_from at time zone 'Asia/Singapore')::date+p_days*2+7,
      interval '1 day') g(d) where extract(isodow from d)<6 order by d offset p_days-1 limit 1)+time '18:00') at time zone 'Asia/Singapore'
$$;
create function support.data_deletion_target() returns trigger language plpgsql set search_path='' as $$
begin
  new.target_at:=support.business_days_after(coalesce(new.created_at,clock_timestamp()),10);
  return new;
end $$;
create trigger data_deletion_target before insert on support.tickets for each row when (new.category='data_deletion')
  execute function support.data_deletion_target();

-- 2. Asks -------------------------------------------------------------------------------------------------------------
create table consent.asks (
  ask_id      uuid primary key default gen_random_uuid(),
  request_key uuid not null unique,  -- the RM's card id: a retry records once
  customer_id bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id  uuid not null references rm.advisors(advisor_id),
  category    text not null references consent.categories(code) check (category in ('transaction_insights','chat_topics')),
  reason      text not null check (reason in ('meeting_prep','review','advice')),
  meeting_id  uuid references rm.appointments(appointment_id) on delete set null,
  ticket_id   uuid unique references support.tickets(ticket_id) on delete set null,
  status      text not null default 'open' check (status in ('open','shared','declined','lapsed')),
  asked_at    timestamptz not null default clock_timestamp(),
  lapses_at   timestamptz not null,
  closed_at   timestamptz,
  grant_id    uuid references consent.grants(grant_id) on delete set null,
  check ((status='open')=(closed_at is null)),
  check (lapses_at>asked_at)
);
create unique index asks_one_open_idx on consent.asks(customer_id,advisor_id,category) where status='open';
create index asks_customer_idx on consent.asks(customer_id,asked_at desc);
alter table consent.asks enable row level security;
revoke all on consent.asks from public,anon,authenticated;
grant select on consent.asks to authenticated;
create policy own_asks on consent.asks for select to authenticated using (customer_id=banking.my_customer_id());

insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('consent.rm_asked',1,'en','in_app','{rm} asked you to share {category}',
  '{rm} would like to see your {category} {reason}. Nothing is shared unless you switch it on in Data sharing. You can also choose not to share. '
  'The request lapses on {date}.',true);

create function consent.ask_reason_text(p_reason text,p_meeting_at timestamptz) returns text language sql stable set search_path='' as $$
  select case p_reason when 'meeting_prep' then 'to prepare for your meeting on '||to_char(p_meeting_at at time zone 'Asia/Singapore','FMDy FMDD Mon')
    when 'review' then 'for your relationship review' else 'to suggest options that fit your plans' end
$$;

-- Asks she did not answer close after 14 days (the ticket says so). The worker runs it every minute (enqueue_due_reminders
-- below); rm_ask_consent and her read run it for her first so an old ask never blocks or shows as open.
create function consent.lapse_asks(p_customer bigint default null) returns int language plpgsql volatile security definer set search_path='' as $$
declare a consent.asks; n int:=0;
begin
  for a in update consent.asks set status='lapsed',closed_at=clock_timestamp()
      where status='open' and lapses_at<=clock_timestamp() and (p_customer is null or customer_id=p_customer) returning * loop
    if a.ticket_id is not null then perform support.move_ticket(a.ticket_id,'closed','This request lapsed after 14 days. Nothing was shared.'); end if;
    perform audit.record('consent','consent.ask_lapsed','success','consent.asks',a.ask_id::text,jsonb_build_object('category',a.category),
      p_customer_id=>a.customer_id,p_actor_type=>'system',p_actor_id=>'consent_asks');
    n:=n+1;
  end loop;
  return n;
end $$;

create function consent.ask_json(a consent.asks) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('ask_id',a.ask_id,'category',a.category,'reason',a.reason,'meeting_id',a.meeting_id,'status',a.status,
    'asked_at',a.asked_at,'lapses_at',a.lapses_at,'lapses_sgt',rm.sgt(a.lapses_at),'reference',(select t.reference from support.tickets t where t.ticket_id=a.ticket_id))
$$;

create function public.rm_ask_consent(p_request_key uuid,p_customer_id bigint,p_category text,p_reason text,p_meeting_id uuid default null)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); a consent.asks; cat consent.categories; m rm.appointments; t support.tickets; rm_name text; why text;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.ask_consent'); end if;
  if p_request_key is null then raise exception 'a request key is required' using errcode='22023'; end if;
  select * into a from consent.asks where request_key=p_request_key;
  if found then
    if a.customer_id<>p_customer_id or a.advisor_id<>advisor or a.category is distinct from p_category then
      raise exception 'request key already used' using errcode='23505';
    end if;
    return jsonb_build_object('denied',false,'created',true,'replayed',true,'ask',consent.ask_json(a));
  end if;
  select * into cat from consent.categories where code=p_category and withdrawable;
  if not found then raise exception 'ask for transaction_insights or chat_topics' using errcode='22023'; end if;
  if p_reason is null or p_reason not in ('meeting_prep','review','advice') then
    raise exception 'reason must be meeting_prep, review or advice' using errcode='22023';
  end if;
  if p_meeting_id is not null then
    select * into m from rm.appointments where appointment_id=p_meeting_id and customer_id=p_customer_id and advisor_id=advisor
      and status='confirmed' and starts_at>clock_timestamp();
    if not found then raise exception 'the meeting is not an upcoming confirmed meeting of this client with you' using errcode='42501'; end if;
  elsif p_reason='meeting_prep' then raise exception 'choose the meeting to prepare for' using errcode='22023';
  end if;
  if rm.consent_active(p_customer_id,p_category,advisor) then
    return jsonb_build_object('denied',false,'created',false,'already_shared',true,'category',p_category);
  end if;
  perform consent.lapse_asks(p_customer_id);
  select * into a from consent.asks where customer_id=p_customer_id and advisor_id=advisor and category=p_category and status='open';
  if found then return jsonb_build_object('denied',false,'created',false,'duplicate',consent.ask_json(a)); end if;
  select regexp_replace(full_name,'\s*\(DEMO\)\s*$','') into rm_name from rm.advisors where advisor_id=advisor;
  why:=consent.ask_reason_text(p_reason,m.starts_at);
  t:=support.open_ticket(p_customer_id,null,'rm_consent_request',format('%s asked you to share %s %s.',rm_name,lower(cat.label),why),
    jsonb_build_object('category',p_category,'reason',p_reason),'normal','rm');
  perform support.move_ticket(t.ticket_id,'waiting_on_client','Waiting for your answer in Data sharing. Nothing is shared unless you switch it on.');
  insert into consent.asks(request_key,customer_id,advisor_id,category,reason,meeting_id,ticket_id,lapses_at)
    values(p_request_key,p_customer_id,advisor,p_category,p_reason,p_meeting_id,t.ticket_id,t.target_at) returning * into a;
  insert into notify.inbox(customer_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
    select p_customer_id,tp.template_key,tp.version,'en','P3',notify.render(tp.title,jsonb_build_object('rm',rm_name,'category',lower(cat.label))),
      notify.render(tp.body,jsonb_build_object('rm',rm_name,'category',lower(cat.label),'reason',why,
        'date',to_char(a.lapses_at at time zone 'Asia/Singapore','FMDD Mon YYYY'))),true,'consent_ask:'||a.ask_id
    from notify.templates tp where tp.template_key='consent.rm_asked' and tp.channel='in_app' and tp.locale='en' order by tp.version desc limit 1;
  perform audit.record('consent','consent.rm_asked','success','consent.asks',a.ask_id::text,
    jsonb_build_object('category',p_category,'reason',p_reason,'meeting_id',p_meeting_id,'reference',t.reference),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer_id);
  return jsonb_build_object('denied',false,'created',true,'replayed',false,'ask',consent.ask_json(a));
end $$;

-- 3. Her answer -------------------------------------------------------------------------------------------------------
-- Any active grant to that RM covering the category answers the ask (her switch on Data sharing, or a chat offer).
create function consent.answer_asks() returns trigger language plpgsql security definer set search_path='' as $$
declare a consent.asks;
begin
  if new.revoked_at is not null or new.basis<>'consent' or new.purpose<>'rm_brief' then return null; end if;
  for a in update consent.asks set status='shared',closed_at=clock_timestamp(),grant_id=new.grant_id
      where customer_id=new.customer_id and advisor_id::text=new.recipient and category=any(new.categories) and status='open' returning * loop
    if a.ticket_id is not null then perform support.move_ticket(a.ticket_id,'resolved','You switched it on in Data sharing.'); end if;
    perform audit.record('consent','consent.ask_answered','success','consent.asks',a.ask_id::text,
      jsonb_build_object('category',a.category,'answer','shared','grant_id',new.grant_id),p_customer_id=>a.customer_id);
  end loop;
  return null;
end $$;
create trigger answer_asks after insert on consent.grants for each row execute function consent.answer_asks();

create function actions.normalise_ask(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'ask_id') is distinct from 'string' or (p_target->>'ask_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'a consent ask answer needs the ask UUID' using errcode='22023';
  end if;
  return jsonb_build_object('ask_id',(p_target->>'ask_id')::uuid);
end $$;

create function actions.decline_ask_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_ask(p_target); a consent.asks;
begin
  perform consent.lapse_asks(p_customer_id);
  select * into a from consent.asks where ask_id=(target->>'ask_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'the ask is not in the signed-in customer scope' using errcode='42501'; end if;
  if a.status<>'open' then raise exception 'this ask is already answered or has lapsed' using errcode='22023'; end if;
  return target||jsonb_build_object('category',a.category,'recipient',a.advisor_id::text);
end $$;

create function actions.execute_decline_ask(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a consent.asks;
begin
  if actions.decline_ask_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the ask changed before it was declined' using errcode='P1001';
  end if;
  update consent.asks set status='declined',closed_at=clock_timestamp() where ask_id=(p_command.target->>'ask_id')::uuid and status='open' returning * into a;
  if a.ticket_id is not null then perform support.move_ticket(a.ticket_id,'closed','You chose not to share it. Nothing was shared.'); end if;
  perform audit.record('consent','consent.ask_answered','success','consent.asks',a.ask_id::text,
    jsonb_build_object('category',a.category,'answer','declined'),p_command.case_id,p_customer_id=>a.customer_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','declined'),'event_type','consent.ask_declined');
end $$;
insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('consent.decline_ask','actions.decline_ask_target(jsonb,bigint)'::regprocedure,'actions.execute_decline_ask(actions.commands)'::regprocedure,false,
    'actions.normalise_ask(jsonb)'::regprocedure,array['consent.ask_declined']);

-- Her receipt is the answer; the ticket timeline tells the RM. No Inbox notice for her own decline.
alter function notify.deliver(events.outbox) rename to deliver_before_consent_asks;
revoke all on function notify.deliver_before_consent_asks(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  if p_event.event_type='consent.ask_declined' then return jsonb_build_object('disposition','suppressed','notification_id',null,'available_at',null); end if;
  return notify.deliver_before_consent_asks(p_event);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- The worker's minute: asks lapse first; the reminder count is unchanged.
alter function events.enqueue_due_reminders() rename to enqueue_reminders_before_asks;
revoke all on function events.enqueue_reminders_before_asks() from public,anon,authenticated,demobank_worker;
create function events.enqueue_due_reminders() returns int language plpgsql volatile security definer set search_path='' as $$
begin
  perform consent.lapse_asks();
  return events.enqueue_reminders_before_asks();
end $$;
revoke all on function events.enqueue_due_reminders() from public,anon,authenticated;
grant execute on function events.enqueue_due_reminders() to demobank_worker;

-- 4. Her read and the demo reset ---------------------------------------------------------------------------------------
create function public.customer_read_consent_asks(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb; checked timestamptz:=clock_timestamp();
begin
  select coalesce(jsonb_agg(jsonb_build_object('ask_id',a.ask_id,'category',a.category,'category_label',c.label,'reason',a.reason,
      'advisor_id',a.advisor_id,'rm_name',regexp_replace(v.full_name,'\s*\(DEMO\)\s*$',''),'meeting_id',a.meeting_id,'meeting_starts_at',m.starts_at,
      'status',case when a.status='open' and a.lapses_at<=checked then 'lapsed' else a.status end,'asked_at',a.asked_at,'lapses_at',a.lapses_at,
      'closed_at',a.closed_at,'reference',t.reference) order by a.asked_at desc),'[]') into data
  from (select * from consent.asks where customer_id=cid order by asked_at desc limit 50) a
  join consent.categories c on c.code=a.category
  left join rm.advisors v on v.advisor_id=a.advisor_id
  left join rm.appointments m on m.appointment_id=a.meeting_id and m.customer_id=cid
  left join support.tickets t on t.ticket_id=a.ticket_id and t.customer_id=cid;
  return banking.customer_result(cid,'consent.asks','consent.asks',data);
end $$;

alter function rm.reset_meeting_demo(bigint[]) rename to reset_meeting_demo_before_asks;
revoke all on function rm.reset_meeting_demo_before_asks(bigint[]) from public,anon,authenticated;
create function rm.reset_meeting_demo(p_customers bigint[] default array[1002,1007]::bigint[]) returns jsonb
language plpgsql volatile set search_path='' as $$
declare asks int;
begin
  if p_customers is null or cardinality(p_customers)=0 or not p_customers<@array[1002,1007]::bigint[] then
    raise exception 'only the demo clients 1002 and 1007' using errcode='22023';
  end if;
  with gone as (delete from consent.asks where customer_id=any(p_customers) returning ticket_id) select count(*) into asks from gone;
  delete from support.tickets where customer_id=any(p_customers) and category='rm_consent_request';
  delete from notify.inbox where customer_id=any(p_customers) and template_key='consent.rm_asked';
  return rm.reset_meeting_demo_before_asks(p_customers)||jsonb_build_object('removed_asks',asks);
end $$;

-- Grants --------------------------------------------------------------------------------------------------------------
revoke all on function support.business_days_after(timestamptz,int),support.data_deletion_target(),consent.ask_reason_text(text,timestamptz),
  consent.lapse_asks(bigint),consent.ask_json(consent.asks),consent.answer_asks(),actions.normalise_ask(jsonb),actions.decline_ask_target(jsonb,bigint),
  actions.execute_decline_ask(actions.commands),rm.reset_meeting_demo(bigint[]) from public,anon,authenticated;
revoke all on function public.rm_ask_consent(uuid,bigint,text,text,uuid),public.customer_read_consent_asks(bigint) from public,anon;
grant execute on function public.rm_ask_consent(uuid,bigint,text,text,uuid),public.customer_read_consent_asks(bigint) to authenticated;
