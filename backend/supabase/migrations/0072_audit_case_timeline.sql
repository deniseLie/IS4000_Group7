-- Audit trail walkthrough (F24): one conversation case traced end to end, for an administrator or compliance
-- reviewer (owner role, SQL editor or scripts/audit_walkthrough.py) and, for their own case only, the customer.
-- Rows hold ids, enums and counts only (audit.is_safe), so the timeline never contains message text or records.
create function audit.case_timeline(p_case_id uuid)
returns table(seq int,occurred_at timestamptz,source text,actor text,action text,outcome text,resource text,details jsonb,request_id uuid)
language sql stable security definer set search_path='' as $$
  with steps as (
    select e.occurred_at,'audit' source,e.actor_type||':'||e.actor_id actor,e.action,e.outcome,
      coalesce(e.resource_type||'/'||e.resource_id,'') resource,e.details,e.request_id,e.event_id ord
    from audit.events e where e.case_id=p_case_id
    union all
    select r.created_at,'receipt','system:action_service',c.type,r.outcome,'actions.receipts/'||r.receipt_id,
      jsonb_build_object('state',c.state,'confirmed',c.confirmed_at is not null),null,r.audit_event_id
    from actions.commands c join actions.receipts r using(command_id) where c.case_id=p_case_id
    union all
    select t.created_at,'ticket','team:'||t.owner_team,'ticket.'||t.status,'success','support.tickets/'||t.reference,
      jsonb_build_object('category',t.category,'urgency',t.urgency,'source',t.source),null,null
    from support.tickets t where t.case_id=p_case_id)
  select (row_number() over (order by occurred_at,ord nulls last))::int,occurred_at,source,actor,action,outcome,resource,details,request_id
  from steps order by 1 $$;
revoke all on function audit.case_timeline(uuid) from public,anon,authenticated;

create function public.my_case_timeline(p_case_id uuid)
returns jsonb language plpgsql stable security definer set search_path='' as $$
declare cid bigint:=banking.my_customer_id();
begin
  if auth.uid() is null or cid is null or not exists(select 1 from conversation.cases where case_id=p_case_id and customer_id=cid) then
    raise exception 'the case is not in the signed-in customer scope' using errcode='42501';
  end if;
  return (select coalesce(jsonb_agg(to_jsonb(t) order by t.seq),'[]') from audit.case_timeline(p_case_id) t);
end $$;
revoke all on function public.my_case_timeline(uuid) from public,anon;
grant execute on function public.my_case_timeline(uuid) to authenticated;
