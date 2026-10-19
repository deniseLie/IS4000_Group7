-- One statement builds each case/assignment snapshot from client-visible owned records only.
create function public.customer_read_compliance(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select jsonb_build_object('case_id',c.case_id,'case_type',c.case_type,'title',c.title,'notice_text',c.notice_text,
    'notice_date',c.notice_date,'due_date',c.due_date,'status',c.status,'status_updated_at',c.status_updated_at,
    'requested_items',coalesce((select jsonb_agg(to_jsonb(i) order by i.sort_order,i.item_id) from(
      select item_id,label,instructions,submission_status,client_mark,client_marked_at,sort_order,updated_at
      from compliance.requested_items where customer_id=cid and case_id=c.case_id order by sort_order,item_id limit 200)i),'[]'),
    'status_history',coalesce((select jsonb_agg(to_jsonb(h) order by h.changed_at desc) from(
      select status,note,changed_at from compliance.status_history where customer_id=cid and case_id=c.case_id order by changed_at desc,id desc limit 200)h),'[]'),
    'info_requests',coalesce((select jsonb_agg(to_jsonb(r) order by r.created_at desc,r.request_id) from(
      select request_id,question,instructions,due_date,status,responded_at,created_at
      from compliance.info_requests where customer_id=cid and case_id=c.case_id order by created_at desc,request_id limit 200)r),'[]'))
    into data from compliance.cases c where c.customer_id=cid order by c.notice_date desc,c.case_id limit 1;
  return banking.customer_result(cid,'compliance.get_case','compliance.cases',data);
end $$;

create function public.customer_read_assignment(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); current_assignment jsonb; previous_assignment jsonb; data jsonb;
begin
  if (select count(*) from rm.assignments where customer_id=cid and ended_on is null)>1 then
    raise exception 'more than one current advisor assignment was returned' using errcode='23514';
  end if;
  if exists(select 1 from rm.assignments a where a.customer_id=cid and not exists(
      select 1 from rm.advisors r where r.advisor_id=a.advisor_id)) then
    raise exception 'an assigned advisor was not returned under the customer scope' using errcode='23514';
  end if;
  with assignments as(
    select a.ended_on,a.started_on,a.assignment_id,jsonb_build_object('rm_id',a.advisor_id,'name',r.full_name,'role',r.role,'team',r.team_name,
      'contact',jsonb_build_object('email',r.email,'phone',r.phone),'timezone',r.timezone,'started_on',a.started_on,'ended_on',a.ended_on,
      'transition_reason_code',a.transition_reason_code) data from rm.assignments a join rm.advisors r on r.advisor_id=a.advisor_id
    where a.customer_id=cid)
  select (select a.data from assignments a where a.ended_on is null order by a.started_on desc,a.assignment_id limit 1),
    (select a.data from assignments a where a.ended_on is not null order by a.ended_on desc,a.started_on desc,a.assignment_id limit 1)
    into current_assignment,previous_assignment;
  if current_assignment is not null or previous_assignment is not null then
    data:=jsonb_build_object('current',current_assignment,'previous',previous_assignment);
  end if;
  return banking.customer_result(cid,'rm.get_assignment','rm.assignments',data);
end $$;

create function public.customer_read_requests(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.created_at desc,r.request_id),'[]') into data from(
    select customer_id,request_id,advisor_id,who,what,urgency,deadline,country,currency,status,created_at,updated_at
    from rm.requests where customer_id=cid order by created_at desc,request_id limit 200)r;
  return banking.customer_result(cid,'requests.list','rm.requests',data);
end $$;

create function public.customer_read_slots(p_customer_id bigint,p_from date,p_to date,p_limit int)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(s) order by s.starts_at,s.advisor_id),'[]') into data
    from public.appointments_find_slots(cid,p_from,p_to,p_limit) s;
  return banking.customer_result(cid,'appointments.find_slots','rm.appointments',data);
end $$;
revoke all on function public.customer_read_compliance(bigint),public.customer_read_assignment(bigint),
  public.customer_read_requests(bigint),public.customer_read_slots(bigint,date,date,int) from public,anon;
grant execute on function public.customer_read_compliance(bigint),public.customer_read_assignment(bigint),
  public.customer_read_requests(bigint),public.customer_read_slots(bigint,date,date,int) to authenticated;
