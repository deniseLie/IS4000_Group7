-- Add the already-authorised appointment agenda to the current briefing sources.
alter function public.meeting_preparation_inputs(uuid) set schema meetings;
alter function meetings.meeting_preparation_inputs(uuid) rename to preparation_inputs_before_agenda;
revoke all on function meetings.preparation_inputs_before_agenda(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare frame jsonb;agenda jsonb;purpose text;topics text;
begin
 frame:=meetings.preparation_inputs_before_agenda(p_meeting_id);
 purpose:=nullif(btrim(frame->>'purpose'),'');
 select string_agg(replace(topic,'_',' '),', ' order by position) into topics
 from jsonb_array_elements_text(frame->'topics') with ordinality as t(topic,position);
 if purpose is not null or topics is not null then
  agenda:=meetings.brief_source(p_meeting_id,'meeting_agenda','appointment',p_meeting_id::text,
   concat_ws(' ',purpose,case when topics is not null then 'Agreed topics: '||topics||'.' end),null,null);
  frame:=frame||jsonb_build_object('sources',(frame->'sources')||jsonb_build_array(agenda));
 end if;
 -- Agenda and collateral placement change the displayed brief. Earlier approvals require a refresh.
 return frame||jsonb_build_object('source_hash',meetings.hash(jsonb_build_array(frame->>'source_hash','brief-agenda-v1',agenda)));
end $$;
revoke all on function public.meeting_preparation_inputs(uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid) to authenticated;
