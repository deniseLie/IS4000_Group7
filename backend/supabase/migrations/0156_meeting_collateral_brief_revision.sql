-- Briefs affected by the new record presentation must be reviewed again.
alter function public.meeting_preparation_inputs(uuid) set schema meetings;
alter function meetings.meeting_preparation_inputs(uuid) rename to preparation_inputs_before_collateral_status;
revoke all on function meetings.preparation_inputs_before_collateral_status(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare frame jsonb;
begin
 frame:=meetings.preparation_inputs_before_collateral_status(p_meeting_id);
 if exists(select 1 from jsonb_array_elements(frame->'sources') source where source->>'reference' like 'rm_collateral_alert:%')
  or (frame->'topics') ? 'fixed_deposit' then
  return frame||jsonb_build_object('source_hash',meetings.hash(jsonb_build_array(frame->>'source_hash','brief-record-presentation-v1')));
 end if;
 return frame;
end $$;
revoke all on function public.meeting_preparation_inputs(uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid) to authenticated;
