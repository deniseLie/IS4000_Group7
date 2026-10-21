-- A queue read is an explicit staff policy decision as well as a data-read audit.
create or replace function public.staff_handoff_queue(p_limit int)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare staff uuid:=support.require_staff(); result jsonb;
begin
  if p_limit is null or p_limit not between 1 and 100 then raise exception 'staff queue limit must be 1-100' using errcode='22023'; end if;
  select coalesce(jsonb_agg(to_jsonb(h) order by h.priority,h.created_at,h.handoff_id),'[]') into result from(
    select handoff_id,customer_id,case_id,reason,priority,summary,status,claimed_by,created_at,updated_at
    from support.handoffs where status in ('open','claimed') order by priority,created_at,handoff_id limit p_limit)h;
  perform audit.record('policy','policy.decided','success','support.handoffs',null,
    jsonb_build_object('tool','handoff.queue','reasons',jsonb_build_array('support_role','aal2'),'version','staff_v1'),
    p_actor_type=>'staff',p_actor_id=>staff::text);
  perform audit.record('data','data.read','success','support.handoffs',null,
    jsonb_build_object('tool','handoff.queue','result_count',jsonb_array_length(result)),p_actor_type=>'staff',p_actor_id=>staff::text);
  return result;
end $$;
