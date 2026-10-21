-- Receipt snapshots retain canonical proposal timestamps regardless of the caller's SQL timezone.
alter function actions.appointment_target(jsonb,bigint) set timezone='UTC';

create or replace function actions.execute_appointment_hold(p_command actions.commands)
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
  return jsonb_build_object('result',p_command.target||jsonb_build_object('appointment_id',appointment.appointment_id,'hold_expires_at',appointment.hold_expires_at,'status','held'),'event_type','booking.held');
end $$;
