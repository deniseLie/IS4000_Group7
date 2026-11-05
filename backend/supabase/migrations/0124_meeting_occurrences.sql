-- Fenced lifecycle projections: a changed schedule invalidates prior intake/approval without rewriting approved minutes.
create function meetings.apply_lifecycle(p_event_id bigint,p_token uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;a rm.appointments;w meetings.workflows;
begin
 e:=meetings.require_claim(p_event_id,p_token);
 if e.event_type not in ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','meeting.started','meeting.ended')
 then raise exception 'unsupported lifecycle projection' using errcode='22023';end if;
 select * into a from rm.appointments where appointment_id=e.meeting_id;
 select * into w from meetings.workflows where meeting_id=e.meeting_id for update;
 if not found then return;end if;
 if a.status='cancelled' then
  update meetings.workflows set state='cancelled',consent_epoch=consent_epoch+1 where meeting_id=e.meeting_id and state<>'cancelled';
 elsif a.status='confirmed' and w.schedule_hash is distinct from meetings.schedule_hash(e.meeting_id) then
  update meetings.workflows set revision=revision+1,consent_epoch=consent_epoch+1,schedule_hash=meetings.schedule_hash(e.meeting_id),
   state='scheduled',occurrence_id=null,current_source=null,current_draft=null,started_at=null,ended_at=null where meeting_id=e.meeting_id;
 end if;
end $$;
revoke all on function meetings.apply_lifecycle(bigint,uuid) from public,anon,authenticated;
grant execute on function meetings.apply_lifecycle(bigint,uuid) to demobank_meetings;
