-- Singleton UUID ranges use PostgreSQL's native range GiST support for advisor equality.
create type rm.advisor_range as range(subtype=uuid);
update rm.appointments set status='expired' where status='held' and hold_expires_at<=clock_timestamp();
alter table rm.appointments add constraint appointments_no_overlap exclude using gist
  (rm.advisor_range(advisor_id,advisor_id,'[]') with &&,tstzrange(starts_at,ends_at,'[)') with &&)
  where(status in ('held','confirmed'));

create function actions.normalise_slot(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
declare start_time timestamptz; end_time timestamptz;
begin
  if jsonb_typeof(p_target->'advisor_id') is distinct from 'string'
      or (p_target->>'advisor_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
      or jsonb_typeof(p_target->'starts_at') is distinct from 'string'
      or jsonb_typeof(p_target->'ends_at') is distinct from 'string'
      or (p_target->>'starts_at') !~ '^\d{4}-\d\d-\d\dT.*(Z|[+-]\d\d:\d\d)$'
      or (p_target->>'ends_at') !~ '^\d{4}-\d\d-\d\dT.*(Z|[+-]\d\d:\d\d)$'
      or jsonb_typeof(p_target->'format') is distinct from 'string'
      or p_target->>'format' not in ('video','phone','in_person') then
    raise exception 'appointment hold requires an advisor UUID, timezone-aware start/end and video, phone or in_person format' using errcode='22023';
  end if;
  begin
    start_time:=(p_target->>'starts_at')::timestamptz; end_time:=(p_target->>'ends_at')::timestamptz;
  exception when invalid_datetime_format or datetime_field_overflow then
    raise exception 'appointment start/end are not valid timestamps' using errcode='22023';
  end;
  if not isfinite(start_time) or not isfinite(end_time) or end_time-start_time<>interval '30 minutes' then
    raise exception 'appointment slots must last exactly thirty minutes' using errcode='22023';
  end if;
  return jsonb_build_object('advisor_id',(p_target->>'advisor_id')::uuid,'starts_at',start_time,'ends_at',end_time,'format',p_target->>'format');
end $$;

create function actions.normalise_appointment(p_target jsonb)
returns jsonb language plpgsql immutable set search_path=''
as $$
begin
  if jsonb_typeof(p_target->'appointment_id') is distinct from 'string'
      or (p_target->>'appointment_id') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$' then
    raise exception 'appointment action requires an appointment UUID' using errcode='22023';
  end if;
  return jsonb_build_object('appointment_id',(p_target->>'appointment_id')::uuid);
end $$;

create function actions.require_available_slot(p_target jsonb,p_customer_id bigint)
returns void language plpgsql volatile security definer set search_path=''
as $$
declare advisor rm.advisors; hours rm.working_hours; start_time timestamptz:=(p_target->>'starts_at')::timestamptz;
  end_time timestamptz:=(p_target->>'ends_at')::timestamptz; local_start timestamp; local_end timestamp;
begin
  -- Every hold/confirm/cancel for this advisor takes the same lock, before locking an appointment.
  perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(p_target->>'advisor_id'));
  select a.* into advisor from rm.advisors a join rm.assignments r on r.advisor_id=a.advisor_id
    where a.advisor_id=(p_target->>'advisor_id')::uuid and r.customer_id=p_customer_id and r.ended_on is null
      and r.started_on<=(clock_timestamp() at time zone 'Asia/Singapore')::date and a.is_active for share of a,r;
  if not found then raise exception 'the active relationship manager assignment changed' using errcode='P1001'; end if;
  if start_time<=clock_timestamp() or start_time>clock_timestamp()+interval '366 days' then
    raise exception 'appointment must start in the future within the next 366 days' using errcode='P1001';
  end if;
  local_start:=start_time at time zone advisor.timezone; local_end:=end_time at time zone advisor.timezone;
  select * into hours from rm.working_hours where advisor_id=advisor.advisor_id and weekday=extract(isodow from local_start) for share;
  if not found or local_start::date<>local_end::date or local_start::time<hours.starts_at or local_end::time>hours.ends_at
      or mod(extract(epoch from(local_start::time-hours.starts_at))::numeric,1800)<>0 then
    raise exception 'appointment does not match the advisor working-hours grid' using errcode='P1001';
  end if;
  if exists(select 1 from rm.advisor_blocks b where b.advisor_id=advisor.advisor_id
      and tstzrange(b.starts_at,b.ends_at,'[)') && tstzrange(start_time,end_time,'[)')) then
    raise exception 'advisor is unavailable at this time' using errcode='P1001';
  end if;
end $$;

create function actions.slot_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_slot(p_target);
begin
  if not exists(select 1 from rm.assignments where customer_id=p_customer_id and advisor_id=(target->>'advisor_id')::uuid and ended_on is null) then
    raise exception 'slot does not belong to the assigned relationship manager' using errcode='42501';
  end if;
  return target;
end $$;

create function actions.appointment_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare target jsonb:=actions.normalise_appointment(p_target); appointment rm.appointments;
begin
  select * into appointment from rm.appointments where appointment_id=(target->>'appointment_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'appointment is not available in this customer scope' using errcode='42501'; end if;
  return jsonb_build_object('appointment_id',appointment.appointment_id,'advisor_id',appointment.advisor_id,'starts_at',appointment.starts_at,
    'ends_at',appointment.ends_at,'format',appointment.format,'hold_expires_at',appointment.hold_expires_at);
end $$;

create function actions.execute_appointment_hold(p_command actions.commands)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare appointment rm.appointments;
begin
  perform actions.require_available_slot(p_command.target,p_command.customer_id);
  -- Expired holds stop blocking the constraint before a new hold is inserted, under the advisor lock.
  update rm.appointments set status='expired' where advisor_id=(p_command.target->>'advisor_id')::uuid
    and status='held' and hold_expires_at<=clock_timestamp();
  begin
    insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,hold_expires_at)
      values(p_command.customer_id,(p_command.target->>'advisor_id')::uuid,(p_command.target->>'starts_at')::timestamptz,
        (p_command.target->>'ends_at')::timestamptz,p_command.target->>'format','held',clock_timestamp()+interval '10 minutes') returning * into appointment;
  exception when exclusion_violation then
    raise exception 'this appointment slot is already reserved' using errcode='P1001';
  end;
  return jsonb_build_object('result',actions.appointment_target(jsonb_build_object('appointment_id',appointment.appointment_id),p_command.customer_id)
    ||jsonb_build_object('status','held'),'event_type','booking.held');
end $$;

create function actions.execute_appointment_confirm(p_command actions.commands)
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
  update rm.appointments set status='confirmed' where appointment_id=appointment.appointment_id and customer_id=p_command.customer_id;
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','confirmed'),'event_type','booking.confirmed');
end $$;

create function actions.execute_appointment_cancel(p_command actions.commands)
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
  return jsonb_build_object('result',p_command.target||jsonb_build_object('status','cancelled'),'event_type','booking.cancelled');
end $$;

insert into actions.executors(type,validator,executor,confirmation_required,normaliser,event_types) values
  ('appointment.hold','actions.slot_target(jsonb,bigint)'::regprocedure,'actions.execute_appointment_hold(actions.commands)'::regprocedure,false,
    'actions.normalise_slot(jsonb)'::regprocedure,array['booking.held']),
  ('appointment.confirm','actions.appointment_target(jsonb,bigint)'::regprocedure,'actions.execute_appointment_confirm(actions.commands)'::regprocedure,true,
    'actions.normalise_appointment(jsonb)'::regprocedure,array['booking.confirmed']),
  ('appointment.cancel','actions.appointment_target(jsonb,bigint)'::regprocedure,'actions.execute_appointment_cancel(actions.commands)'::regprocedure,true,
    'actions.normalise_appointment(jsonb)'::regprocedure,array['booking.cancelled']);

-- A direct booking form has a real owned case, without fabricating a chat message or AI turn.
create function public.appointment_hold(p_command_id uuid,p_session_id uuid,p_case_id uuid,p_target jsonb)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id();
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  if p_command_id is null or p_session_id is null or p_case_id is null then
    raise exception 'appointment hold requires stable command, session and case UUIDs' using errcode='22023';
  end if;
  perform public.conversation_create(p_session_id,'personal','English',null);
  insert into conversation.cases(case_id,session_id,customer_id,topic,priority)
    values(p_case_id,p_session_id,cid,'appointment','P3') on conflict do nothing;
  if not exists(select 1 from conversation.cases where case_id=p_case_id and customer_id=cid and session_id=p_session_id and topic='appointment') then
    raise exception 'appointment case belongs to another request' using errcode='42501';
  end if;
  return public.action_propose(p_command_id,p_case_id,'appointment.hold',p_target,p_case_id::text||':hold:appointment.hold');
end $$;

create function public.customer_read_appointments(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb; checked_at timestamptz:=clock_timestamp();
begin
  select coalesce(jsonb_agg(to_jsonb(a) order by a.starts_at desc,a.appointment_id),'[]') into data from(
    select a.appointment_id,a.advisor_id,a.starts_at,a.ends_at,a.format,a.hold_expires_at,
      case when a.status='held' and a.hold_expires_at<=checked_at then 'expired' else a.status end status,
      (select c.case_id from actions.commands c join actions.receipts r using(command_id) where c.customer_id=cid and r.customer_id=cid
        and c.type='appointment.hold' and c.state='succeeded' and r.result->>'appointment_id'=a.appointment_id::text limit 1) case_id
    from rm.appointments a where a.customer_id=cid order by a.starts_at desc,a.appointment_id limit 200)a;
  return banking.customer_result(cid,'appointments.list','rm.appointments',data);
end $$;

revoke all on function actions.normalise_slot(jsonb),actions.normalise_appointment(jsonb),actions.require_available_slot(jsonb,bigint),
  actions.slot_target(jsonb,bigint),actions.appointment_target(jsonb,bigint),actions.execute_appointment_hold(actions.commands),
  actions.execute_appointment_confirm(actions.commands),actions.execute_appointment_cancel(actions.commands) from public,anon,authenticated;
revoke all on function public.appointment_hold(uuid,uuid,uuid,jsonb),public.customer_read_appointments(bigint) from public,anon;
grant execute on function public.appointment_hold(uuid,uuid,uuid,jsonb),public.customer_read_appointments(bigint) to authenticated;
