-- Link a reviewed support handoff to owned compliance records; never include uploaded contents.
create table compliance.support_context(
 action_case_id uuid primary key references conversation.cases(case_id),customer_id bigint not null references banking.customer_profiles,
 review_case_id uuid not null references compliance.cases(case_id),item_id uuid references compliance.requested_items(item_id),
 request_id uuid references compliance.info_requests(request_id),document_id uuid references compliance.documents(document_id),
 check_id uuid references compliance.prechecks(check_id),summary text not null,created_at timestamptz not null default clock_timestamp()
);
alter table compliance.support_context enable row level security;
revoke all on compliance.support_context from public,anon,authenticated;
grant select on compliance.support_context to authenticated;
create policy own_support_context on compliance.support_context for select to authenticated using(customer_id=banking.my_customer_id());
create function public.prepare_compliance_support(p_session_id uuid,p_action_case_id uuid,p_review_case_id uuid,p_item_id uuid default null,p_request_id uuid default null)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.my_customer_id(); review compliance.cases; item compliance.requested_items; request compliance.info_requests;
 check_row compliance.prechecks; previous compliance.support_context; message text;
begin
 if cid is null or auth.uid() is null then raise exception 'linked customer required' using errcode='42501'; end if;
 select * into review from compliance.cases where case_id=p_review_case_id and customer_id=cid for share;
 if not found then raise exception 'owned review required' using errcode='42501'; end if;
 if p_item_id is not null then
  select * into item from compliance.requested_items where item_id=p_item_id and case_id=review.case_id and customer_id=cid for share;
  if not found then raise exception 'owned item required' using errcode='42501'; end if;
  select * into check_row from compliance.prechecks where document_id=item.current_document_id and customer_id=cid
   and requirements_hash=compliance.notice_digest(review.case_id,item.item_id) order by created_at desc limit 1;
 end if;
 if p_request_id is not null then
  select * into request from compliance.info_requests where request_id=p_request_id and case_id=review.case_id and customer_id=cid;
  if not found then raise exception 'owned information request required' using errcode='42501'; end if;
 end if;
 perform public.action_create_case(p_session_id,p_action_case_id);
 message:='Compliance review support. Recorded case status: '||review.status||'.';
 if item.item_id is not null then message:=message||' Requested item status: '||item.submission_status||'.'; end if;
 if check_row.check_id is not null then
  message:=message||' Preliminary outcome: '||check_row.status||'. Findings: '||coalesce((select string_agg(distinct f->>'code',', ') from jsonb_array_elements(check_row.report->'findings') f),'none')||'.';
 end if;
 if request.request_id is not null then message:=message||' Selected information request status: '||request.status||'.'; end if;
 select * into previous from compliance.support_context where action_case_id=p_action_case_id;
 if found and (previous.customer_id<>cid or previous.review_case_id<>p_review_case_id or previous.item_id is distinct from p_item_id or previous.request_id is distinct from p_request_id) then
  raise exception 'support selection changed' using errcode='22023';
 end if;
 insert into compliance.support_context(action_case_id,customer_id,review_case_id,item_id,request_id,document_id,check_id,summary)
 values(p_action_case_id,cid,review.case_id,item.item_id,request.request_id,item.current_document_id,check_row.check_id,message)
 on conflict(action_case_id) do nothing;
 select * into previous from compliance.support_context where action_case_id=p_action_case_id and customer_id=cid;
 return banking.customer_result(cid,'compliance.support','compliance.support_context',jsonb_build_object('case_id',p_action_case_id,'summary',previous.summary));
end $$;
revoke all on function public.prepare_compliance_support(uuid,uuid,uuid,uuid,uuid) from public,anon;
grant execute on function public.prepare_compliance_support(uuid,uuid,uuid,uuid,uuid) to authenticated;

-- Staff can inspect the selected recorded context only after claiming its handoff under MFA.
create function public.staff_compliance_context(p_handoff_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare staff uuid:=support.require_staff(); h support.handoffs; context compliance.support_context; result jsonb;
begin
 select * into h from support.handoffs where handoff_id=p_handoff_id and status='claimed' and claimed_by=staff::text for share;
 if not found then raise exception 'claim this handoff first' using errcode='42501'; end if;
 select * into context from compliance.support_context where action_case_id=h.case_id and customer_id=h.customer_id;
 if not found then return null; end if;
 select jsonb_build_object('review',jsonb_build_object('case_id',c.case_id,'notice',c.notice_text,'status',c.status,'updated_at',c.status_updated_at,'due_date',c.due_date),
 'item',(select jsonb_build_object('item_id',i.item_id,'label',i.label,'instructions',i.instructions,'submission_status',i.submission_status) from compliance.requested_items i where i.item_id=context.item_id and i.customer_id=h.customer_id),
 'request',(select jsonb_build_object('request_id',r.request_id,'question',r.question,'instructions',r.instructions,'status',r.status,'due_date',r.due_date) from compliance.info_requests r where r.request_id=context.request_id and r.customer_id=h.customer_id),
 'precheck',(select p.report from compliance.prechecks p where p.check_id=context.check_id and p.customer_id=h.customer_id),
 'snapshot_at',context.created_at)
 into result from compliance.cases c where c.case_id=context.review_case_id and c.customer_id=h.customer_id;
 perform audit.record('data','data.read','success','compliance.support_context',context.action_case_id::text,jsonb_build_object('tool','staff.compliance_context'),h.case_id,
 p_customer_id=>h.customer_id,p_actor_type=>'staff',p_actor_id=>staff::text);
 return result;
end $$;
revoke all on function public.staff_compliance_context(uuid) from public,anon;
grant execute on function public.staff_compliance_context(uuid) to authenticated;

alter function actions.execute_handoff(actions.commands) rename to execute_handoff_before_compliance_context;
revoke all on function actions.execute_handoff_before_compliance_context(actions.commands) from public,anon,authenticated;
create function actions.execute_handoff(p_command actions.commands) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare context compliance.support_context;
begin
 select * into context from compliance.support_context where action_case_id=p_command.case_id and customer_id=p_command.customer_id;
 if found and p_command.target->>'summary' is distinct from context.summary then
  raise exception 'reviewed compliance summary changed' using errcode='P1001';
 end if;
 return actions.execute_handoff_before_compliance_context(p_command);
end $$;
revoke all on function actions.execute_handoff(actions.commands) from public,anon,authenticated;
update actions.executors set executor='actions.execute_handoff(actions.commands)'::regprocedure where type='handoff.create';
