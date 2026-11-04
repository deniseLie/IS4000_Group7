-- RM copilot phase 1 (docs/planning/rm-copilot-build-plan.md, 1b and 1c): what the meeting lifecycle side needs from
-- the client, consent and copilot side. Synthetic data only.
--   1. Topic codes and the slot check shared by requests, decisions and reschedules.
--   2. rm.meeting_requests: a client asks for a meeting with a purpose, topics and up to 3 preferred times.
--   3. Client actions on the ledger: meeting.request, meeting.withdraw, meeting.counter_reply (all need confirmation).
--   4. RM decisions: accept a preferred time, propose another (held for the client) or decline; reschedule.
--   5. Meeting events: every confirmed meeting, time change and cancellation queues its meeting.* event in the same
--      transaction (meetings.enqueue, 0105), with dedupe_key = the command or decision id.
--   6. Readers the meeting brief calls: rm_transaction_insights and rm_talking_points, gated by rm.consent_active and
--      audited with the category and grant id; rm_cash_events gets the same transaction_insights gate.
--   7. Client notices and the demo seed (Michelle's home loan meeting, Mei Ling's open request, Michelle's grant).

-- 1. Topics and slots -------------------------------------------------------------------------------------------------
create function rm.meeting_topics() returns text[] language sql immutable set search_path='' as
$$ select array['home_loan','investments','fixed_deposit','insurance','term_sheet','compliance_review','account_review','card_review','other'] $$;

create function rm.valid_topics(p text[]) returns boolean language sql immutable set search_path='' as $$
  select p is not null and cardinality(p)<=10 and (select count(distinct t) from unnest(p) t)=cardinality(p)
    and not exists(select 1 from unnest(p) t where t is null or not (t=any(rm.meeting_topics())))
$$;

alter table rm.appointments add column topics text[] not null default '{}' check (rm.valid_topics(topics));

-- One business day later (Friday, Saturday and Sunday move to Monday; public holidays are not counted).
create function rm.next_business_day(p timestamptz) returns timestamptz language sql stable set search_path='' as $$
  select p+make_interval(days=>case extract(isodow from p at time zone 'Asia/Singapore')::int when 5 then 3 when 6 then 2 else 1 end)
$$;

-- Why a 30-minute meeting cannot start at p_starts for this client with this RM, or null when it can. p_exclude is the
-- meeting being moved or confirmed (it does not clash with itself or count twice toward the weekly limit).
create function rm.slot_problem(p_advisor uuid,p_customer bigint,p_starts timestamptz,p_exclude uuid default null)
returns text language plpgsql stable security definer set search_path='' as $$
declare a rm.advisors; h rm.working_hours; ends timestamptz:=p_starts+interval '30 minutes'; ls timestamp; le timestamp;
begin
  select * into a from rm.advisors where advisor_id=p_advisor and is_active;
  if not found then return 'advisor_unavailable'; end if;
  if p_starts is null or not isfinite(p_starts) or p_starts<=clock_timestamp()+interval '1 hour' then return 'too_soon'; end if;
  if p_starts>clock_timestamp()+interval '60 days' then return 'too_far'; end if;
  ls:=p_starts at time zone a.timezone; le:=ends at time zone a.timezone;
  select * into h from rm.working_hours where advisor_id=p_advisor and weekday=extract(isodow from ls);
  if not found or ls::date<>le::date or ls::time<h.starts_at or le::time>h.ends_at
      or mod(extract(epoch from(ls::time-h.starts_at))::numeric,1800)<>0 then return 'outside_hours'; end if;
  if exists(select 1 from rm.advisor_blocks b where b.advisor_id=p_advisor
      and tstzrange(b.starts_at,b.ends_at,'[)') && tstzrange(p_starts,ends,'[)')) then return 'blocked'; end if;
  if exists(select 1 from rm.appointments ap where ap.advisor_id=p_advisor and ap.appointment_id is distinct from p_exclude
      and (ap.status='confirmed' or ap.status='held' and ap.hold_expires_at>clock_timestamp())
      and tstzrange(ap.starts_at,ap.ends_at,'[)') && tstzrange(p_starts,ends,'[)')) then return 'taken'; end if;
  if rm.client_week_meetings(p_customer,p_advisor,p_starts,p_exclude)>=rm.weekly_meeting_limit() then return 'weekly_limit'; end if;
  return null;
end $$;

create function rm.slot_message(p_code text) returns text language sql immutable set search_path='' as $$
  select case p_code when 'advisor_unavailable' then 'the relationship manager is not available'
    when 'too_soon' then 'it must be at least an hour from now' when 'too_far' then 'it must be within the next 60 days'
    when 'outside_hours' then 'it is outside the relationship manager''s working hours'
    when 'blocked' then 'the relationship manager is unavailable then' when 'taken' then 'that time is already booked'
    when 'weekly_limit' then 'the client already has the most meetings allowed that week' else p_code end
$$;

-- 2. Meeting requests -------------------------------------------------------------------------------------------------
create table rm.meeting_requests (
  request_id      uuid primary key default gen_random_uuid(),
  customer_id     bigint not null references banking.customer_profiles(customer_id) on delete cascade,
  advisor_id      uuid not null references rm.advisors(advisor_id),
  purpose         text not null check (length(btrim(purpose)) between 1 and 200),  -- client text: redacted, never in events
  topics          text[] not null check (cardinality(topics) between 1 and 5 and rm.valid_topics(topics)),
  format          text not null check (format in ('video','phone','in_person')),
  preferred_times timestamptz[] not null check (cardinality(preferred_times) between 1 and 3),
  status          text not null default 'requested' check (status in ('requested','countered','scheduled','declined','withdrawn')),
  respond_by      timestamptz not null,
  decline_reason  text check (decline_reason in ('answered_by_message','redirected','not_needed')),
  appointment_id  uuid unique references rm.appointments(appointment_id) on delete set null,  -- held while countered, then the meeting
  command_id      uuid unique references actions.commands(command_id),
  decision_id     uuid unique,  -- the RM's decision (idempotency)
  decided_at      timestamptz,
  created_at      timestamptz not null default clock_timestamp(),
  updated_at      timestamptz not null default clock_timestamp(),
  check (status<>'declined' or decline_reason is not null)
);
create index meeting_requests_customer_idx on rm.meeting_requests(customer_id,created_at desc);
create index meeting_requests_advisor_idx on rm.meeting_requests(advisor_id,status,respond_by);
alter table rm.meeting_requests enable row level security;
revoke all on rm.meeting_requests from anon,authenticated;
grant select on rm.meeting_requests to authenticated;
create policy own_rows on rm.meeting_requests for select to authenticated using (customer_id=(select banking.my_customer_id()));
create trigger audit_row after insert or update or delete on rm.meeting_requests for each row execute function audit.row_change('request_id','status');

-- 3. Client actions ---------------------------------------------------------------------------------------------------
create function actions.utc_text(p timestamptz) returns text language sql immutable set search_path='' as
$$ select to_char(p at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') $$;

create function actions.normalise_meeting_request(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
declare topics text[]; times timestamptz[];
begin
  if jsonb_typeof(p_target->'advisor_id') is distinct from 'string' or (p_target->>'advisor_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'purpose') is distinct from 'string' or length(btrim(p_target->>'purpose')) not between 1 and 200
      or jsonb_typeof(p_target->'topics') is distinct from 'array' or jsonb_array_length(p_target->'topics') not between 1 and 5
      or exists(select 1 from jsonb_array_elements(p_target->'topics') e where jsonb_typeof(e)<>'string')
      or jsonb_typeof(p_target->'format') is distinct from 'string' or p_target->>'format' not in ('video','phone','in_person')
      or jsonb_typeof(p_target->'preferred_times') is distinct from 'array' or jsonb_array_length(p_target->'preferred_times') not between 1 and 3
      or exists(select 1 from jsonb_array_elements(p_target->'preferred_times') e
        where jsonb_typeof(e)<>'string' or (e#>>'{}') !~ '^\d{4}-\d\d-\d\dT.*(Z|[+-]\d\d:\d\d)$') then
    raise exception 'a meeting request needs an advisor UUID, a purpose of 1-200 characters, 1-5 topics, a video, phone or in_person format and 1-3 preferred times with a time zone'
      using errcode='22023';
  end if;
  select array_agg(distinct t order by t) into topics from jsonb_array_elements_text(p_target->'topics') t;
  if not rm.valid_topics(topics) then raise exception 'meeting topics must come from the fixed list' using errcode='22023'; end if;
  begin
    select array_agg(distinct t::timestamptz order by t::timestamptz) into times from jsonb_array_elements_text(p_target->'preferred_times') t;
  exception when invalid_datetime_format or datetime_field_overflow then
    raise exception 'preferred times are not valid timestamps' using errcode='22023';
  end;
  return jsonb_build_object('advisor_id',(p_target->>'advisor_id')::uuid,'purpose',btrim(p_target->>'purpose'),'topics',to_jsonb(topics),
    'format',p_target->>'format','preferred_times',(select jsonb_agg(actions.utc_text(x) order by x) from unnest(times) x));
end $$;

create function actions.meeting_request_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_meeting_request(p_target); advisor uuid:=(target->>'advisor_id')::uuid; t text; problem text;
begin
  if not exists(select 1 from rm.assignments r join rm.advisors a using(advisor_id) where r.customer_id=p_customer_id and r.advisor_id=advisor
      and r.ended_on is null and r.started_on<=rm.today() and a.is_active) then
    raise exception 'a meeting request goes to the client''s current relationship manager' using errcode='42501';
  end if;
  perform conversation.require_redacted(target->>'purpose');
  for t in select jsonb_array_elements_text(target->'preferred_times') loop
    problem:=rm.slot_problem(advisor,p_customer_id,t::timestamptz);
    if problem is not null then raise exception 'preferred time % cannot be offered: %',t,rm.slot_message(problem) using errcode='22023'; end if;
  end loop;
  return target;
end $$;

create function actions.execute_meeting_request(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare r rm.meeting_requests; advisor uuid:=(p_command.target->>'advisor_id')::uuid;
begin
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(advisor::text));
  if not rm.in_book(advisor,p_command.customer_id) then raise exception 'the relationship manager assignment changed' using errcode='P1001'; end if;
  if (select count(*) from rm.meeting_requests where customer_id=p_command.customer_id and status in ('requested','countered'))>=3 then
    raise exception 'the client already has three open meeting requests' using errcode='P1001';
  end if;
  insert into rm.meeting_requests(customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by,command_id)
    values(p_command.customer_id,advisor,p_command.target->>'purpose',array(select jsonb_array_elements_text(p_command.target->'topics')),
      p_command.target->>'format',array(select t::timestamptz from jsonb_array_elements_text(p_command.target->'preferred_times') t),
      rm.next_business_day(clock_timestamp()),p_command.command_id) returning * into r;
  return jsonb_build_object('result',jsonb_build_object('request_id',r.request_id,'status',r.status,'respond_by',actions.utc_text(r.respond_by),
    'respond_by_text',rm.sgt(r.respond_by)),'event_type','meeting.requested');
end $$;

create function actions.normalise_meeting_ref(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'request_id') is distinct from 'string' or (p_target->>'request_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'a meeting request action needs the request UUID' using errcode='22023';
  end if;
  return jsonb_build_object('request_id',(p_target->>'request_id')::uuid);
end $$;

create function actions.normalise_counter_reply(p_target jsonb) returns jsonb language plpgsql immutable set search_path='' as $$
begin
  if jsonb_typeof(p_target->'reply') is distinct from 'string' or p_target->>'reply' not in ('accept','decline') then
    raise exception 'a reply to a proposed time is accept or decline' using errcode='22023';
  end if;
  return actions.normalise_meeting_ref(p_target)||jsonb_build_object('reply',p_target->>'reply');
end $$;

create function actions.meeting_withdraw_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_meeting_ref(p_target); r rm.meeting_requests;
begin
  select * into r from rm.meeting_requests where request_id=(target->>'request_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'meeting request is not available in this customer scope' using errcode='42501'; end if;
  if r.status not in ('requested','countered') then raise exception 'only an open meeting request can be withdrawn' using errcode='22023'; end if;
  return target||jsonb_build_object('advisor_id',r.advisor_id);
end $$;

create function actions.counter_reply_target(p_target jsonb,p_customer_id bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare target jsonb:=actions.normalise_counter_reply(p_target); r rm.meeting_requests;
begin
  select * into r from rm.meeting_requests where request_id=(target->>'request_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'meeting request is not available in this customer scope' using errcode='42501'; end if;
  if r.status<>'countered' then raise exception 'there is no proposed time to answer on this request' using errcode='22023'; end if;
  return target||jsonb_build_object('advisor_id',r.advisor_id);
end $$;

create function actions.release_hold(p_request rm.meeting_requests) returns void language sql volatile security definer set search_path='' as $$
  update rm.appointments set status='expired' where appointment_id=p_request.appointment_id and status='held'
$$;

create function actions.execute_meeting_withdraw(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare r rm.meeting_requests;
begin
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(p_command.target->>'advisor_id'));
  select * into r from rm.meeting_requests where request_id=(p_command.target->>'request_id')::uuid and customer_id=p_command.customer_id for update;
  if not found or r.status not in ('requested','countered') then raise exception 'the meeting request was answered before it could be withdrawn' using errcode='P1001'; end if;
  perform actions.release_hold(r);
  update rm.meeting_requests set status='withdrawn',updated_at=clock_timestamp() where request_id=r.request_id;
  return jsonb_build_object('result',jsonb_build_object('request_id',r.request_id,'status','withdrawn'),'event_type','meeting.withdrawn');
end $$;

-- The meeting.scheduled payload: ids, codes and dates only (never the purpose, which the client wrote).
create function rm.meeting_payload(a rm.appointments,p_request uuid,p_by text) returns jsonb language sql stable set search_path='' as $$
  select jsonb_build_object('starts_at',actions.utc_text(a.starts_at),'ends_at',actions.utc_text(a.ends_at),'format',a.format,
    'topics',to_jsonb(a.topics),'request_id',p_request,'by',p_by)
$$;

create function actions.execute_meeting_counter_reply(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare r rm.meeting_requests; a rm.appointments; accept boolean:=p_command.target->>'reply'='accept';
begin
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(p_command.target->>'advisor_id'));
  select * into r from rm.meeting_requests where request_id=(p_command.target->>'request_id')::uuid and customer_id=p_command.customer_id for update;
  select * into a from rm.appointments where appointment_id=r.appointment_id and customer_id=p_command.customer_id for update;
  if r.status is distinct from 'countered' or a.status is distinct from 'held' or a.hold_expires_at<=clock_timestamp() then
    raise exception 'the proposed time is no longer held' using errcode='P1001';
  end if;
  if not accept then
    perform actions.release_hold(r);
    update rm.meeting_requests set status='withdrawn',updated_at=clock_timestamp() where request_id=r.request_id;
    return jsonb_build_object('result',jsonb_build_object('request_id',r.request_id,'status','withdrawn','reply','decline'),'event_type','meeting.counter_declined');
  end if;
  if not rm.in_book(r.advisor_id,r.customer_id) then raise exception 'the relationship manager assignment changed' using errcode='P1001'; end if;
  update rm.appointments set status='confirmed',hold_expires_at=null where appointment_id=a.appointment_id returning * into a;
  update rm.meeting_requests set status='scheduled',updated_at=clock_timestamp() where request_id=r.request_id;
  perform meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,r.request_id,'client'),'command:'||p_command.command_id);
  return jsonb_build_object('result',jsonb_build_object('request_id',r.request_id,'status','scheduled','reply','accept','appointment_id',a.appointment_id,
    'starts_at',actions.utc_text(a.starts_at),'when',rm.sgt(a.starts_at)),'event_type','meeting.counter_accepted');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('meeting.request','actions.meeting_request_target(jsonb,bigint)'::regprocedure,'actions.execute_meeting_request(actions.commands)'::regprocedure,true,
    'actions.normalise_meeting_request(jsonb)'::regprocedure,array['meeting.requested']),
  ('meeting.withdraw','actions.meeting_withdraw_target(jsonb,bigint)'::regprocedure,'actions.execute_meeting_withdraw(actions.commands)'::regprocedure,true,
    'actions.normalise_meeting_ref(jsonb)'::regprocedure,array['meeting.withdrawn']),
  ('meeting.counter_reply','actions.counter_reply_target(jsonb,bigint)'::regprocedure,'actions.execute_meeting_counter_reply(actions.commands)'::regprocedure,true,
    'actions.normalise_counter_reply(jsonb)'::regprocedure,array['meeting.counter_accepted','meeting.counter_declined']);

-- 4. RM decisions -----------------------------------------------------------------------------------------------------
create function rm.notify_meeting(p_customer bigint,p_template text,p_vars jsonb,p_dedup text) returns void
language sql volatile security definer set search_path='' as $$
  insert into notify.inbox(customer_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
  select p_customer,tp.template_key,tp.version,'en','P3',tp.title,notify.render(tp.body,p_vars),true,p_dedup
  from notify.templates tp where tp.template_key=p_template and tp.channel='in_app' and tp.locale='en' order by tp.version desc limit 1
  on conflict(customer_id,dedup_key) where dedup_key is not null do nothing
$$;

create function rm.meeting_request_json(r rm.meeting_requests) returns jsonb language sql stable security definer set search_path='' as $$
  select jsonb_build_object('request_id',r.request_id,'customer_id',r.customer_id,'client',rm.display_name(p.full_name),'purpose',r.purpose,
    'topics',to_jsonb(r.topics),'format',r.format,'status',r.status,'overdue',r.status='requested' and r.respond_by<clock_timestamp(),
    'proposal_lapsed',r.status='countered' and not exists(select 1 from rm.appointments ap where ap.appointment_id=r.appointment_id
      and ap.status='held' and ap.hold_expires_at>clock_timestamp()),
    'respond_by',r.respond_by,'respond_by_text',rm.sgt(r.respond_by),'created_at',r.created_at,'created_text',rm.sgt(r.created_at),
    'preferred_times',(select coalesce(jsonb_agg(jsonb_build_object('starts_at',t,'when',rm.sgt(t),
        'problem',rm.slot_problem(r.advisor_id,r.customer_id,t,r.appointment_id)) order by t),'[]') from unnest(r.preferred_times) t),
    'decline_reason',r.decline_reason,'appointment_id',r.appointment_id,
    'meeting',(select jsonb_build_object('starts_at',ap.starts_at,'when',rm.sgt(ap.starts_at),'status',
        case when ap.status='held' and ap.hold_expires_at<=clock_timestamp() then 'expired' else ap.status end,
        'hold_expires',case when ap.status='held' then rm.sgt(ap.hold_expires_at) end) from rm.appointments ap where ap.appointment_id=r.appointment_id))
  from banking.customer_profiles p where p.customer_id=r.customer_id
$$;

-- Accept one of the client's preferred times, propose another (held for the client until she answers, at most two days)
-- or decline with a reason. The API calls this only after the RM approved the exact decision card.
create function public.rm_meeting_request_decide(p_decision_id uuid,p_request_id uuid,p_outcome text,p_starts_at timestamptz default null,
  p_reason text default null) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); r rm.meeting_requests; a rm.appointments; problem text;
begin
  if p_decision_id is null or p_request_id is null or p_outcome is null or p_outcome not in ('accept','counter','decline') then
    raise exception 'a decision needs a decision id, a request id and accept, counter or decline' using errcode='22023';
  end if;
  select * into r from rm.meeting_requests where request_id=p_request_id;
  if not found then raise exception 'meeting request not found' using errcode='P0002'; end if;
  if r.advisor_id<>advisor or not rm.in_book(advisor,r.customer_id) then return rm.deny(r.customer_id,'rm.meeting_request_decide'); end if;
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(advisor::text));
  select * into r from rm.meeting_requests where request_id=p_request_id for update;
  if r.decision_id=p_decision_id then
    return jsonb_build_object('denied',false,'replayed',true,'request',rm.meeting_request_json(r));
  end if;
  -- A proposed time the client did not answer before its hold ended is decided again, like a new request.
  if r.status='countered' and not exists(select 1 from rm.appointments ap where ap.appointment_id=r.appointment_id and ap.status='held'
      and ap.hold_expires_at>clock_timestamp()) then
    perform actions.release_hold(r);
  elsif r.status<>'requested' then raise exception 'this request is already %',r.status using errcode='P1001';
  end if;
  if p_outcome='decline' then
    if p_reason is null or p_reason not in ('answered_by_message','redirected','not_needed') then
      raise exception 'a decline needs a reason: answered_by_message, redirected or not_needed' using errcode='22023';
    end if;
    update rm.meeting_requests set status='declined',decline_reason=p_reason,decision_id=p_decision_id,decided_at=clock_timestamp(),
      updated_at=clock_timestamp() where request_id=r.request_id returning * into r;
    perform rm.notify_meeting(r.customer_id,'meeting.declined',jsonb_build_object('reason',case p_reason
      when 'answered_by_message' then 'They will answer it in a message instead. Check your Inbox.'
      when 'redirected' then 'They passed it to the team that handles it. Check My requests for updates.'
      else 'They do not think a meeting is needed for this. You can send a new request at any time.' end),'meeting_decision:'||p_decision_id);
  else
    if p_starts_at is null then raise exception 'accepting or proposing a time needs the start time' using errcode='22023'; end if;
    if p_outcome='accept' and not (p_starts_at=any(r.preferred_times)) then
      raise exception 'accept one of the client''s preferred times, or propose a different one' using errcode='22023';
    end if;
    if p_outcome='counter' and p_starts_at=any(r.preferred_times) then
      raise exception 'that is one of the client''s preferred times: accept it instead' using errcode='22023';
    end if;
    problem:=rm.slot_problem(advisor,r.customer_id,p_starts_at);
    if problem is not null then raise exception 'that time cannot be booked: %',rm.slot_message(problem) using errcode='P1001'; end if;
    update rm.appointments set status='expired' where advisor_id=advisor and status='held' and hold_expires_at<=clock_timestamp();
    begin
      insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,hold_expires_at,joining_details,purpose,topics)
        values(r.customer_id,advisor,p_starts_at,p_starts_at+interval '30 minutes',r.format,
          case when p_outcome='accept' then 'confirmed' else 'held' end,
          case when p_outcome='counter' then least(p_starts_at-interval '1 hour',rm.next_business_day(rm.next_business_day(clock_timestamp()))) end,
          (select o.joining_details from rm.meeting_options o where o.advisor_id=advisor and o.format=r.format),r.purpose,r.topics)
        returning * into a;
    exception when exclusion_violation then
      raise exception 'that time cannot be booked: %',rm.slot_message('taken') using errcode='P1001';
    end;
    update rm.meeting_requests set status=case when p_outcome='accept' then 'scheduled' else 'countered' end,appointment_id=a.appointment_id,
      decision_id=p_decision_id,decided_at=clock_timestamp(),updated_at=clock_timestamp() where request_id=r.request_id returning * into r;
    if p_outcome='accept' then
      perform meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,r.request_id,'rm'),'decision:'||p_decision_id);
      perform rm.notify_meeting(r.customer_id,'meeting.accepted',jsonb_build_object('when',rm.sgt(a.starts_at)),'meeting_decision:'||p_decision_id);
    else
      perform rm.notify_meeting(r.customer_id,'meeting.countered',jsonb_build_object('when',rm.sgt(a.starts_at),'until',rm.sgt(a.hold_expires_at)),
        'meeting_decision:'||p_decision_id);
    end if;
  end if;
  perform audit.record('action','action.rm_meeting_request_decided','success','rm.meeting_requests',r.request_id::text,
    jsonb_build_object('outcome',p_outcome,'decision_id',p_decision_id,'appointment_id',r.appointment_id,'reason',p_reason),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>r.customer_id);
  return jsonb_build_object('denied',false,'replayed',false,'request',rm.meeting_request_json(r));
end $$;

-- Move a confirmed meeting to another free time (the RM agreed it with the client first). Queues meeting.rescheduled.
create function public.rm_meeting_reschedule(p_change_id uuid,p_appointment_id uuid,p_starts_at timestamptz,p_reason text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); a rm.appointments; problem text; queued bigint;
begin
  if p_change_id is null or p_appointment_id is null or p_starts_at is null or p_reason is null
      or p_reason not in ('client_asked','rm_unavailable','other') then
    raise exception 'a reschedule needs a change id, the meeting, the new start and a reason: client_asked, rm_unavailable or other' using errcode='22023';
  end if;
  select * into a from rm.appointments where appointment_id=p_appointment_id;
  if not found then raise exception 'meeting not found' using errcode='P0002'; end if;
  if a.advisor_id<>advisor or not rm.in_book(advisor,a.customer_id) then return rm.deny(a.customer_id,'rm.meeting_reschedule'); end if;
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(advisor::text));
  select event_id into queued from meetings.events where dedupe_key='change:'||p_change_id;
  if found then return jsonb_build_object('denied',false,'replayed',true,'appointment_id',a.appointment_id,'when',rm.sgt(a.starts_at)); end if;
  select * into a from rm.appointments where appointment_id=p_appointment_id for update;
  if a.status<>'confirmed' or a.starts_at<=clock_timestamp() then raise exception 'only an upcoming confirmed meeting can be moved' using errcode='P1001'; end if;
  if a.starts_at=p_starts_at then raise exception 'the meeting is already at that time' using errcode='22023'; end if;
  problem:=rm.slot_problem(advisor,a.customer_id,p_starts_at,a.appointment_id);
  if problem is not null then raise exception 'that time cannot be booked: %',rm.slot_message(problem) using errcode='P1001'; end if;
  begin
    update rm.appointments set starts_at=p_starts_at,ends_at=p_starts_at+interval '30 minutes' where appointment_id=a.appointment_id returning * into a;
  exception when exclusion_violation then
    raise exception 'that time cannot be booked: %',rm.slot_message('taken') using errcode='P1001';
  end;
  perform meetings.enqueue('meeting.rescheduled',a.appointment_id,rm.meeting_payload(a,
    (select request_id from rm.meeting_requests where appointment_id=a.appointment_id),'rm')||jsonb_build_object('reason',p_reason),'change:'||p_change_id);
  perform rm.notify_meeting(a.customer_id,'meeting.rescheduled',jsonb_build_object('when',rm.sgt(a.starts_at)),'meeting_change:'||p_change_id);
  perform audit.record('action','action.rm_meeting_rescheduled','success','rm.appointments',a.appointment_id::text,
    jsonb_build_object('change_id',p_change_id,'reason',p_reason),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return jsonb_build_object('denied',false,'replayed',false,'appointment_id',a.appointment_id,'starts_at',a.starts_at,'when',rm.sgt(a.starts_at));
end $$;

-- 5. Meeting events from the client's own booking actions (same as 0034 plus the event) -------------------------------
create or replace function actions.execute_appointment_confirm(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare appointment rm.appointments;
begin
  perform actions.require_available_slot(p_command.target,p_command.customer_id);
  select * into appointment from rm.appointments where appointment_id=(p_command.target->>'appointment_id')::uuid
    and customer_id=p_command.customer_id for update;
  if not found or appointment.status<>'held' or appointment.hold_expires_at<=clock_timestamp()
      or actions.appointment_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the owned hold expired or changed before confirmation' using errcode='P1001';
  end if;
  update rm.appointments set status='confirmed' where appointment_id=appointment.appointment_id and customer_id=p_command.customer_id
    returning * into appointment;
  perform meetings.enqueue('meeting.scheduled',appointment.appointment_id,rm.meeting_payload(appointment,null,'client'),'command:'||p_command.command_id);
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','confirmed'),'event_type','booking.confirmed');
end $$;

create or replace function actions.execute_appointment_cancel(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare appointment rm.appointments;
begin
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(p_command.target->>'advisor_id'));
  select * into appointment from rm.appointments where appointment_id=(p_command.target->>'appointment_id')::uuid
    and customer_id=p_command.customer_id for update;
  if not found or appointment.status not in ('held','confirmed') or appointment.starts_at<=clock_timestamp()
      or appointment.status='held' and appointment.hold_expires_at<=clock_timestamp()
      or actions.appointment_target(p_command.target,p_command.customer_id)<>p_command.target then
    raise exception 'the owned appointment expired, started or changed before cancellation' using errcode='P1001';
  end if;
  update rm.appointments set status='cancelled' where appointment_id=appointment.appointment_id and customer_id=p_command.customer_id;
  if appointment.status='confirmed' then
    perform meetings.enqueue('meeting.cancelled',appointment.appointment_id,jsonb_build_object('by','client'),'command:'||p_command.command_id);
  end if;
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','cancelled'),'event_type','booking.cancelled');
end $$;

-- 6. Reads ------------------------------------------------------------------------------------------------------------
-- The RM's meeting requests: open (default), scheduled or closed (declined, withdrawn). Purposes are the client's words.
create function public.rm_meeting_requests(p_status text default 'open') returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); wanted text[];
begin
  wanted:=case coalesce(p_status,'open') when 'open' then array['requested','countered'] when 'scheduled' then array['scheduled']
    when 'closed' then array['declined','withdrawn'] end;
  if wanted is null then raise exception 'status must be open, scheduled or closed' using errcode='22023'; end if;
  return rm.result(null,'rm.meeting_requests','rm.meeting_requests',(select coalesce(jsonb_agg(rm.meeting_request_json(r)
      order by (r.status='requested' and r.respond_by<clock_timestamp()) desc,r.respond_by),'[]')
    from (select * from rm.meeting_requests r where r.advisor_id=advisor and rm.in_book(advisor,r.customer_id) and r.status=any(wanted)
      order by r.respond_by limit 50) r));
end $$;

create function public.customer_read_meeting_requests(p_customer_id bigint) returns jsonb
language plpgsql volatile security invoker set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(jsonb_build_object('request_id',r.request_id,'purpose',r.purpose,'topics',to_jsonb(r.topics),'format',r.format,
      'status',r.status,'respond_by',r.respond_by,'preferred_times',to_jsonb(r.preferred_times),'decline_reason',r.decline_reason,
      'appointment_id',r.appointment_id,'created_at',r.created_at) order by r.created_at desc),'[]') into data
    from (select * from rm.meeting_requests r where r.customer_id=cid order by r.created_at desc limit 50) r;
  return banking.customer_result(cid,'meeting_requests.list','rm.meeting_requests',data);
end $$;

-- Consent-gated reads (the meeting brief, the client overview). Shared: the data plus the category and grant id, audited
-- as a data.read with both. Not shared: {"shared": false} and no data, audited as a consent gap. Never filtered rows.
create function rm.grant_for(p_customer bigint,p_category text,p_advisor uuid) returns uuid language sql stable security definer set search_path='' as $$
  select g.grant_id from rm.active_consent(p_advisor,p_customer) g
  where rm.consent_active(p_customer,p_category,p_advisor) and p_category=any(g.categories) order by g.granted_at desc limit 1
$$;

create function rm.gated(p_customer bigint,p_tool text,p_resource text,p_category text,p_grant uuid,p_purpose text,p_data jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare call_id uuid:=gen_random_uuid(); n int;
begin
  if p_grant is null then
    perform audit.record('policy','policy.rm_consent_gap','denied',p_resource,call_id::text,
      jsonb_build_object('tool',p_tool,'category',p_category,'purpose',p_purpose,'reason','no_active_consent'),
      p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer);
    return jsonb_build_object('denied',false,'shared',false,'category',p_category,'customer_id',p_customer,'call_id',call_id,
      'as_of',clock_timestamp(),'data',null);
  end if;
  n:=case when p_data is null or p_data='null' then 0 when jsonb_typeof(p_data)='array' then jsonb_array_length(p_data) else 1 end;
  perform audit.record('data','data.read','success',p_resource,call_id::text,
    jsonb_build_object('tool',p_tool,'result_count',n,'category',p_category,'grant_id',p_grant,'purpose',p_purpose),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>p_customer);
  return jsonb_build_object('denied',false,'shared',true,'category',p_category,'grant_id',p_grant,'customer_id',p_customer,'call_id',call_id,
    'as_of',clock_timestamp(),'data',p_data);
end $$;

-- What the client's accounts and cards show over the last p_days (30-180): monthly money in and out, card spending,
-- overseas spending, movements of 10,000 or more, and recurring counterparties (3 or more times). Computed here; no
-- transaction descriptions and no list of ordinary transactions.
create function public.rm_transaction_insights(p_customer_id bigint,p_days int default 90) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid; since timestamptz; days int:=coalesce(p_days,90);
begin
  if days not between 30 and 180 then raise exception 'choose 30 to 180 days' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.transaction_insights'); end if;
  g:=rm.grant_for(p_customer_id,'transaction_insights',advisor);
  if g is null then return rm.gated(p_customer_id,'rm.transaction_insights','banking.transactions','transaction_insights',null,'client_view',null); end if;
  since:=clock_timestamp()-make_interval(days=>days);
  return rm.gated(p_customer_id,'rm.transaction_insights','banking.transactions','transaction_insights',g,'client_view',(
    with t as (select * from banking.transactions where customer_id=p_customer_id and txn_date>=since and status in ('posted','pending'))
    select jsonb_build_object('days',days,'from',(since at time zone 'Asia/Singapore')::date,'to',rm.today(),'transactions',(select count(*) from t),
      'monthly',(select coalesce(jsonb_agg(jsonb_build_object('month',m,'currency',c,'money_in',i,'money_out',o,'count',n) order by m,c),'[]') from (
          select to_char(txn_date at time zone 'Asia/Singapore','YYYY-MM') m,currency c,coalesce(sum(amount) filter (where amount>0),0) i,
            coalesce(abs(sum(amount) filter (where amount<0)),0) o,count(*) n from t group by 1,2) x),
      'card_spending',(select coalesce(jsonb_agg(jsonb_build_object('currency',c,'amount',s,'count',n) order by c),'[]') from (
          select currency c,abs(sum(amount)) s,count(*) n from t where card_id is not null and amount<0 group by 1) x),
      'overseas',jsonb_build_object('count',(select count(*) from t where is_overseas and amount<0),
        'amounts',(select coalesce(jsonb_object_agg(c,s),'{}') from (select currency c,abs(sum(amount)) s from t where is_overseas and amount<0 group by 1) x)),
      'large_movements',(select coalesce(jsonb_agg(jsonb_build_object('date',(txn_date at time zone 'Asia/Singapore')::date,
          'date_text',rm.day((txn_date at time zone 'Asia/Singapore')::date),'direction',case when amount>0 then 'incoming' else 'outgoing' end,
          'amount',abs(amount),'currency',currency,'counterparty',merchant,'status',status) order by txn_date desc),'[]') from t where abs(amount)>=10000),
      'recurring',(select coalesce(jsonb_agg(jsonb_build_object('counterparty',merchant,'direction',d,'count',n,'typical_amount',a,'currency',c,
          'last_date',l) order by n desc,merchant),'[]') from (
          select merchant,case when amount>0 then 'incoming' else 'outgoing' end d,currency c,count(*) n,round(avg(abs(amount)),2) a,
            max((txn_date at time zone 'Asia/Singapore')::date) l from t group by 1,2,3 having count(*)>=3) x))));
end $$;

-- Talking points from the client's chats (build plan phase 4 fills them). The signature and gate are final now:
-- p_purpose is meeting_prep (the brief) or client_view (the client page); points carry list values, dates and ids only.
create function public.rm_talking_points(p_customer_id bigint,p_purpose text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid;
begin
  if p_purpose is null or p_purpose not in ('meeting_prep','client_view') then raise exception 'purpose must be meeting_prep or client_view' using errcode='22023'; end if;
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.talking_points'); end if;
  g:=rm.grant_for(p_customer_id,'chat_topics',advisor);
  return rm.gated(p_customer_id,'rm.talking_points','rm.talking_points','chat_topics',g,p_purpose,case when g is not null then '[]'::jsonb end);
end $$;

-- Same as 0095, gated by transaction_insights like rm_transaction_insights.
create or replace function public.rm_cash_events(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); g uuid;
begin
  if not rm.in_book(advisor,p_customer_id) then return rm.deny(p_customer_id,'rm.cash_events'); end if;
  g:=rm.grant_for(p_customer_id,'transaction_insights',advisor);
  return rm.gated(p_customer_id,'rm.cash_events','banking.transactions','transaction_insights',g,'client_view',case when g is not null then (
    select coalesce(jsonb_agg(jsonb_build_object(
      'date',(t.txn_date at time zone 'Asia/Singapore')::date,'date_text',rm.day((t.txn_date at time zone 'Asia/Singapore')::date),
      'direction',case when t.amount>0 then 'incoming' else 'outgoing' end,'amount',abs(t.amount),'currency',t.currency,
      'description',t.description,'counterparty',t.merchant,'overseas',t.is_overseas,'status',t.status,
      'recurring',(select count(*)>=3 from banking.transactions r where r.customer_id=t.customer_id and r.merchant=t.merchant
         and r.txn_date>=clock_timestamp()-interval '180 days')) order by t.txn_date desc),'[]')
    from banking.transactions t where t.customer_id=p_customer_id and abs(t.amount)>=10000
      and t.txn_date>=clock_timestamp()-interval '180 days' and t.status in ('posted','pending')) end);
end $$;

-- 7. Client notices ---------------------------------------------------------------------------------------------------
insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
  ('meeting.requested',1,'en','in_app','Meeting request sent','Your request to meet your relationship manager was sent. They will answer by {respond_by}. See My requests, Meetings.',true),
  ('meeting.withdrawn',1,'en','in_app','Meeting request withdrawn','You withdrew your meeting request. No meeting was booked.',true),
  ('meeting.counter_accepted',1,'en','in_app','Meeting confirmed','Your meeting with your relationship manager on {when} is confirmed. See My requests, Meetings for the joining details.',true),
  ('meeting.counter_declined',1,'en','in_app','Proposed time declined','You declined the time your relationship manager proposed. You can send a new meeting request at any time.',true),
  ('meeting.accepted',1,'en','in_app','Meeting confirmed','Your relationship manager accepted your meeting request for {when}. See My requests, Meetings for the joining details.',true),
  ('meeting.countered',1,'en','in_app','New meeting time proposed','Your relationship manager proposed {when} for your meeting. Accept or decline it in My requests, Meetings by {until}.',true),
  ('meeting.declined',1,'en','in_app','Your meeting request was answered','Your relationship manager answered your meeting request without booking a meeting. {reason}',true),
  ('meeting.rescheduled',1,'en','in_app','Meeting moved','Your meeting with your relationship manager moved to {when}. See My requests, Meetings.',true)
on conflict do nothing;

alter function notify.deliver(events.outbox) rename to deliver_before_meeting_requests;
revoke all on function notify.deliver_before_meeting_requests(events.outbox) from public,anon,authenticated;
create function notify.deliver(p_event events.outbox) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=p_event.customer_id; command actions.commands; receipt actions.receipts; template notify.templates; vars jsonb:='{}';
  notification uuid;
begin
  if p_event.event_type not in ('meeting.requested','meeting.withdrawn','meeting.counter_accepted','meeting.counter_declined') then
    return notify.deliver_before_meeting_requests(p_event);
  end if;
  if cid is null then raise exception 'customer required' using errcode='P2002'; end if;
  select * into command from actions.commands where command_id=p_event.command_id and customer_id=cid;
  select * into receipt from actions.receipts where command_id=p_event.command_id and customer_id=cid;
  if command.type is distinct from (case when p_event.event_type='meeting.requested' then 'meeting.request'
        when p_event.event_type='meeting.withdrawn' then 'meeting.withdraw' else 'meeting.counter_reply' end)
      or receipt.outcome is distinct from 'succeeded' or p_event.payload->>'receipt_id' is distinct from receipt.receipt_id
      or p_event.payload->>'command_id' is distinct from command.command_id::text then
    raise exception 'meeting event disagrees with its receipt' using errcode='P2002';
  end if;
  if p_event.event_type='meeting.requested' then vars:=jsonb_build_object('respond_by',receipt.result->>'respond_by_text');
  elsif p_event.event_type='meeting.counter_accepted' then vars:=jsonb_build_object('when',receipt.result->>'when'); end if;
  select * into template from notify.templates where template_key=p_event.event_type and channel='in_app' and locale='en' order by version desc limit 1;
  insert into notify.inbox(customer_id,event_id,template_key,template_version,locale,priority,title,body,is_service,dedup_key)
  values(cid,p_event.event_id,template.template_key,template.version,'en','P3',template.title,notify.render(template.body,vars),true,'event:'||p_event.event_id)
  on conflict(customer_id,dedup_key) where dedup_key is not null do nothing returning notification_id into notification;
  if notification is null then return jsonb_build_object('disposition','duplicate','notification_id',null,'available_at',null); end if;
  perform audit.record('notification','notification.sent','success','notify.inbox',notification::text,
    jsonb_build_object('event_id',p_event.event_id,'template',template.template_key,'version',template.version,'priority','P3'),
    p_customer_id=>cid,p_actor_type=>'system',p_actor_id=>'notification_worker');
  return jsonb_build_object('disposition','delivered','notification_id',notification,'available_at',null);
end $$;
revoke all on function notify.deliver(events.outbox) from public,anon,authenticated;

-- 8. Demo seed --------------------------------------------------------------------------------------------------------
-- * Michelle shares transaction insights, chat topics and meeting recording with Sophie (consent v2 will let clients
--   grant these themselves; until then this grant is the demo's only one).
-- * Michelle's request "Home loan options", accepted by Sophie: a confirmed video meeting at 15:00 on the next Thursday
--   that is free and in a week with no other meeting for her, with its meeting.scheduled event queued for the runner.
-- * Mei Ling's open request about her fixed deposit, waiting for Sophie (she has no category grant, so her gated reads
--   show the gap).
do $seed$
declare
  sophie uuid:='00000000-0000-4000-8000-100200000002'; today date:=rm.today(); at timestamptz; a rm.appointments; r uuid; i int;
begin
  if exists(select 1 from rm.meeting_requests) then return; end if;  -- safe to re-run
  insert into consent.grants(grant_id,customer_id,purpose,recipient,categories,granted_at,expires_at) values
    ('00000000-0000-4000-8000-100200000041',1002,'rm_brief',sophie::text,array['transaction_insights','chat_topics','meeting_recording'],
     clock_timestamp()-interval '1 day',clock_timestamp()+interval '180 days') on conflict (grant_id) do nothing;
  for i in 0..4 loop
    at:=((today+((4-extract(isodow from today)::int+6)%7)+1+7*i)+time '15:00') at time zone 'Asia/Singapore';
    exit when rm.slot_problem(sophie,1002,at) is null and rm.client_week_meetings(1002,sophie,at)=0;  -- a week she can still book in
  end loop;
  insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,joining_details,purpose,topics)
    values(1002,sophie,at,at+interval '30 minutes','video','confirmed',
      (select o.joining_details from rm.meeting_options o where o.advisor_id=sophie and o.format='video'),'Home loan options',
      array['home_loan','term_sheet','compliance_review']) returning * into a;
  insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,status,respond_by,appointment_id,
      decision_id,decided_at,created_at,updated_at)
    values('00000000-0000-4000-8000-100200000071',1002,sophie,'Home loan options',array['home_loan','term_sheet','compliance_review'],'video',
      array[at,at+interval '1 day'],'scheduled',rm.next_business_day(clock_timestamp()-interval '1 day'),a.appointment_id,
      '00000000-0000-4000-8000-100200000072',clock_timestamp()-interval '20 hours',clock_timestamp()-interval '1 day',clock_timestamp()-interval '20 hours');
  perform meetings.enqueue('meeting.scheduled',a.appointment_id,rm.meeting_payload(a,'00000000-0000-4000-8000-100200000071'::uuid,'rm'),
    'decision:00000000-0000-4000-8000-100200000072');
  if exists(select 1 from rm.assignments where customer_id=1007 and advisor_id=sophie and ended_on is null) then
    insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,respond_by)
      values('00000000-0000-4000-8000-100700000071',1007,sophie,'Renewing my fixed deposit before it matures in December',array['fixed_deposit'],'video',
        array[((today+((1-extract(isodow from today)::int+6)%7)+1)+time '16:30') at time zone 'Asia/Singapore',
              ((today+((2-extract(isodow from today)::int+6)%7)+1)+time '16:30') at time zone 'Asia/Singapore'],
        rm.next_business_day(clock_timestamp()));
  end if;
end $seed$;

-- 9. Grants -----------------------------------------------------------------------------------------------------------
do $$
declare f text;
begin
  foreach f in array array['rm_meeting_request_decide(uuid,uuid,text,timestamptz,text)','rm_meeting_reschedule(uuid,uuid,timestamptz,text)',
    'rm_meeting_requests(text)','rm_transaction_insights(bigint,int)','rm_talking_points(bigint,text)','customer_read_meeting_requests(bigint)'] loop
    execute format('revoke all on function public.%s from public,anon',f);
    execute format('grant execute on function public.%s to authenticated',f);
  end loop;
end $$;
-- Named one by one: a schema-wide revoke would also remove rm.is_my_advisor, which customer RLS policies need (0094).
revoke all on function rm.meeting_topics(),rm.valid_topics(text[]),rm.next_business_day(timestamptz),rm.slot_problem(uuid,bigint,timestamptz,uuid),
  rm.slot_message(text),rm.meeting_payload(rm.appointments,uuid,text),rm.notify_meeting(bigint,text,jsonb,text),
  rm.meeting_request_json(rm.meeting_requests),rm.grant_for(bigint,text,uuid),rm.gated(bigint,text,text,text,uuid,text,jsonb)
  from public,anon,authenticated;
revoke all on function actions.utc_text(timestamptz),actions.normalise_meeting_request(jsonb),actions.meeting_request_target(jsonb,bigint),
  actions.execute_meeting_request(actions.commands),actions.normalise_meeting_ref(jsonb),actions.normalise_counter_reply(jsonb),
  actions.meeting_withdraw_target(jsonb,bigint),actions.counter_reply_target(jsonb,bigint),actions.release_hold(rm.meeting_requests),
  actions.execute_meeting_withdraw(actions.commands),actions.execute_meeting_counter_reply(actions.commands) from public,anon,authenticated;
