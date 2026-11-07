-- Add approved outstanding commitments to the same live, consent-checked brief inputs used by both RM entry points.
alter function public.meeting_preparation_inputs(uuid) set schema meetings;
alter function meetings.meeting_preparation_inputs(uuid) rename to base_preparation_inputs;
revoke all on function meetings.base_preparation_inputs(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function meetings.open_commitments(p_meeting_id uuid)
returns table(record_id text,recorded_at timestamptz,owner_role text,action_text text,due_on date)
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;
begin
 perform meetings.require_rm(p_meeting_id);select * into a from rm.appointments where appointment_id=p_meeting_id;
 return query
 select 'followup:'||x.followup_id,prior.starts_at,x.owner_role,x.action->>'text',(x.action->>'due_on')::date
 from meetings.followups x join rm.appointments prior on prior.appointment_id=x.meeting_id left join rm.tasks task on task.task_id=x.task_id
 where prior.customer_id=a.customer_id and prior.advisor_id=a.advisor_id and prior.starts_at<a.starts_at
  and x.meeting_id<>p_meeting_id and x.status='open' and (x.task_id is null or task.status='open')
 union all
 select 'task:'||task.task_id,task.created_at,'rm',task.title,task.due_on from rm.tasks task
 where task.customer_id=a.customer_id and task.advisor_id=a.advisor_id and task.status='open' and task.created_at<a.starts_at
  and not exists(select 1 from meetings.followups x where x.task_id=task.task_id);
end $$;
revoke all on function meetings.open_commitments(uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function public.meeting_preparation_inputs(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare frame jsonb;a rm.appointments;f record;extra jsonb:='[]';total int;
begin
 frame:=meetings.base_preparation_inputs(p_meeting_id);
 select * into a from rm.appointments where appointment_id=p_meeting_id;
 perform meetings.read_audit(a.customer_id,'meetings.followups',p_meeting_id::text,'meeting.outstanding_followups');
 perform meetings.read_audit(a.customer_id,'rm.tasks',p_meeting_id::text,'meeting.outstanding_tasks');
 select count(*) into total from meetings.open_commitments(p_meeting_id);
 for f in select * from meetings.open_commitments(p_meeting_id) order by due_on nulls last,recorded_at,record_id limit 25
 loop
  extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'meeting_followups',f.record_id,f.record_id,
   format('Outstanding %s from %s. Owner: %s. Action: %s. Due: %s. Current status: open.',
    case when f.record_id like 'task:%' then 'RM task' else 'agreed follow-up' end,
    (f.recorded_at at time zone 'Asia/Singapore')::date,f.owner_role,f.action_text,coalesce(f.due_on::text,'not agreed')),null,null));
 end loop;
 if total>25 then
  extra:=extra||jsonb_build_array(meetings.brief_source(p_meeting_id,'meeting_followups','remaining',p_meeting_id::text,
   format('There are %s outstanding follow-ups and RM tasks. This brief includes the first 25 by due date; review the remaining %s in Follow-ups and the earlier meeting workspaces.',total,total-25),null,null));
 end if;
 if jsonb_array_length(frame->'sources')+jsonb_array_length(extra)>128 then
  raise exception 'preparation sources exceed 128 items after adding outstanding follow-ups; narrow the source-reader window before generating a brief' using errcode='22023';
 end if;
 return frame||jsonb_build_object('sources',(frame->'sources')||extra,
  'source_hash',meetings.hash(jsonb_build_array(frame->>'source_hash',
   (select coalesce(jsonb_agg(value-'reference'),'[]') from jsonb_array_elements(extra)))));
end $$;
revoke all on function public.meeting_preparation_inputs(uuid) from public,anon;
grant execute on function public.meeting_preparation_inputs(uuid) to authenticated;
