-- Bind agent proposals to an owned durable turn. A resume lease replaces its run id so late workers cannot write.
create unique index turns_case_unique on conversation.turns(case_id);
create table conversation.agent_workflows (
  turn_id uuid primary key references conversation.turns on delete cascade,
  session_id uuid not null references conversation.sessions on delete cascade,
  case_id uuid not null unique references conversation.cases,
  customer_id bigint not null references banking.customer_profiles(customer_id),
  version text not null check(version='v2-actions-v1'),
  state text not null check(state in ('awaiting_confirmation','running','failed','completed','cancelled')),
  checkpoint_id text,
  ready boolean not null default false,
  resume_owner uuid,
  lease_until timestamptz,
  error_code text check(error_code in ('resume_failed','cancelled','assistant_unavailable','interrupted'))
);
create table actions.workflow_commands (
  command_id uuid primary key references actions.commands on delete cascade,
  turn_id uuid not null references conversation.agent_workflows on delete cascade,
  customer_id bigint not null references banking.customer_profiles(customer_id),
  task_id text not null check(task_id ~ '^[A-Za-z0-9_-]{1,40}$'),
  unique(turn_id,task_id)
);
alter table conversation.agent_workflows enable row level security;
alter table actions.workflow_commands enable row level security;
revoke all on conversation.agent_workflows,actions.workflow_commands from public,anon,authenticated;
grant select on conversation.agent_workflows,actions.workflow_commands to authenticated;
create policy own_rows on conversation.agent_workflows for select to authenticated using(customer_id=banking.my_customer_id());
create policy own_rows on actions.workflow_commands for select to authenticated using(customer_id=banking.my_customer_id());

create function conversation.workflow_status(p_turn_id uuid,p_customer_id bigint)
returns jsonb language sql stable security invoker set search_path=''
as $$
  select jsonb_build_object('version',w.version,'turn_id',w.turn_id,'session_id',w.session_id,'case_id',w.case_id,'customer_id',w.customer_id,
    'state',w.state,'ready',w.ready,'pending_commands',p.ids,'lease_expires_at',w.lease_until,'error_code',w.error_code,
    'can_resume',w.ready and t.state='processing' and jsonb_array_length(p.ids)=0
      and (w.state in ('awaiting_confirmation','failed') or (w.state='running' and w.lease_until<=clock_timestamp())))
  from conversation.agent_workflows w join conversation.turns t using(turn_id),lateral(
    select coalesce(jsonb_agg(c.command_id order by c.proposed_at,c.command_id),'[]'::jsonb) ids
    from actions.workflow_commands b join actions.commands c using(command_id)
    where b.turn_id=w.turn_id and b.customer_id=p_customer_id and c.customer_id=p_customer_id and c.state='proposed')p
  where w.turn_id=p_turn_id and w.customer_id=p_customer_id and t.customer_id=p_customer_id
$$;
revoke all on function conversation.workflow_status(uuid,bigint) from public,anon;
grant execute on function conversation.workflow_status(uuid,bigint) to authenticated;

create or replace function actions.outcome(p_command_id uuid,p_customer_id bigint)
returns jsonb language sql stable security invoker set search_path=''
as $$
  select jsonb_build_object('command',to_jsonb(c),'receipt',(select to_jsonb(r) from actions.receipts r
    where r.command_id=c.command_id and r.customer_id=p_customer_id),
    'workflow',(select conversation.workflow_status(b.turn_id,p_customer_id) from actions.workflow_commands b
      where b.command_id=c.command_id and b.customer_id=p_customer_id))
  from actions.commands c where c.command_id=p_command_id and c.customer_id=p_customer_id
$$;

create function public.workflow_propose_action(p_session_id uuid,p_turn_id uuid,p_run_id uuid,p_task_id text,
  p_command_id uuid,p_type text,p_target jsonb,p_idempotency_key text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); t conversation.turns; result jsonb; actual_id uuid; binding actions.workflow_commands;
begin
  if auth.uid() is null or cid is null then raise exception 'a linked customer session is required' using errcode='42501'; end if;
  select * into t from conversation.turns where turn_id=p_turn_id and session_id=p_session_id and customer_id=cid;
  if not found then raise exception 'owned workflow turn not found' using errcode='P0002'; end if;
  perform conversation.require_checkpoint_run(p_session_id,t.case_id,p_run_id,null);
  if p_task_id is null or p_task_id !~ '^[A-Za-z0-9_-]{1,40}$'
      or p_idempotency_key is distinct from t.case_id::text||':'||p_task_id||':'||p_type then
    raise exception 'workflow proposal must use the owned case and task id' using errcode='22023';
  end if;
  insert into conversation.agent_workflows(turn_id,session_id,case_id,customer_id,version,state)
    values(t.turn_id,t.session_id,t.case_id,cid,'v2-actions-v1','awaiting_confirmation') on conflict(turn_id) do nothing;
  result:=public.action_propose(p_command_id,t.case_id,p_type,p_target,p_idempotency_key);
  actual_id:=(result#>>'{command,command_id}')::uuid;
  select * into binding from actions.workflow_commands where command_id=actual_id;
  if found and (binding.turn_id<>p_turn_id or binding.task_id<>p_task_id or binding.customer_id<>cid) then
    raise exception 'command is already bound to another workflow task' using errcode='23505';
  end if;
  insert into actions.workflow_commands(command_id,turn_id,customer_id,task_id)
    values(actual_id,p_turn_id,cid,p_task_id) on conflict(command_id) do nothing;
  if (select count(*) from actions.workflow_commands b join actions.commands c using(command_id)
      where b.turn_id=p_turn_id and c.state='proposed')>5 then
    raise exception 'a workflow may await at most five customer decisions' using errcode='22023';
  end if;
  perform audit.record('chat','chat.workflow_proposal','success','actions.commands',actual_id::text,
    jsonb_build_object('task_id',p_task_id),t.case_id,t.session_id);
  return actions.outcome(actual_id,cid);
end $$;

-- Standalone commands retain their original behaviour. Bound commands lock the session before the command row.
alter function public.action_confirm(uuid) set schema actions;
alter function actions.action_confirm(uuid) rename to confirm_recorded;
alter function public.action_reject(uuid) set schema actions;
alter function actions.action_reject(uuid) rename to reject_recorded;
revoke all on function actions.confirm_recorded(uuid),actions.reject_recorded(uuid) from public,anon,authenticated;

create function actions.require_workflow_decision(p_command_id uuid)
returns void language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); w conversation.agent_workflows; t conversation.turns; current_state text;
begin
  select a.* into w from actions.workflow_commands b join conversation.agent_workflows a using(turn_id)
    where b.command_id=p_command_id and b.customer_id=cid and a.customer_id=cid;
  if not found then return; end if;
  perform 1 from conversation.sessions where session_id=w.session_id for update;
  select * into w from conversation.agent_workflows where turn_id=w.turn_id;
  select state into current_state from actions.commands where command_id=p_command_id and customer_id=cid;
  if current_state<>'proposed' then return; end if;
  select * into t from conversation.turns where turn_id=w.turn_id and customer_id=cid;
  perform conversation.require_checkpoint_run(w.session_id,w.case_id,t.run_id,null);
  if not w.ready then raise exception 'the workflow is still saving its review checkpoint; reload before deciding' using errcode='55P03'; end if;
end $$;
revoke all on function actions.require_workflow_decision(uuid) from public,anon,authenticated;

-- Recheck after the command row is visible/locked too, closing the race with an uncommitted binding.
create function actions.guard_workflow_confirmation()
returns trigger language plpgsql security definer set search_path=''
as $$ begin perform actions.require_workflow_decision(new.command_id); return new; end $$;
revoke all on function actions.guard_workflow_confirmation() from public,anon,authenticated;
create trigger guard_workflow_confirmation before update of state on actions.commands for each row
  when(old.state='proposed' and new.state='confirmed') execute function actions.guard_workflow_confirmation();

create function public.action_confirm(p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$ begin perform actions.require_workflow_decision(p_command_id); return actions.confirm_recorded(p_command_id); end $$;
create function public.action_reject(p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$ begin perform actions.require_workflow_decision(p_command_id); return actions.reject_recorded(p_command_id); end $$;

create function public.workflow_mark_waiting(p_turn_id uuid,p_run_id uuid,p_checkpoint_id text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); w conversation.agent_workflows;
begin
  select * into w from conversation.agent_workflows where turn_id=p_turn_id and customer_id=cid;
  if not found then raise exception 'owned workflow not found' using errcode='P0002'; end if;
  perform conversation.require_checkpoint_run(w.session_id,w.case_id,p_run_id,null);
  if p_checkpoint_id is null or p_checkpoint_id is distinct from (select checkpoint_id from conversation.checkpoints
      where session_id=w.session_id and case_id=w.case_id and namespace='' order by checkpoint_id desc limit 1) then
    raise exception 'workflow review requires its latest saved checkpoint' using errcode='23505';
  end if;
  update conversation.agent_workflows set ready=true,checkpoint_id=p_checkpoint_id where turn_id=p_turn_id;
  perform audit.record('chat','chat.workflow_waiting','success','conversation.turns',p_turn_id::text,'{}',w.case_id,w.session_id);
  return conversation.workflow_status(p_turn_id,cid);
end $$;

create function public.workflow_acquire_resume(p_turn_id uuid,p_worker_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); w conversation.agent_workflows; t conversation.turns; acquired boolean:=false;
begin
  select * into w from conversation.agent_workflows where turn_id=p_turn_id and customer_id=cid;
  if not found then raise exception 'owned workflow not found' using errcode='P0002'; end if;
  if p_worker_id is null then raise exception 'resume requires a private worker id' using errcode='22023'; end if;
  perform 1 from conversation.sessions where session_id=w.session_id for update;
  select * into w from conversation.agent_workflows where turn_id=p_turn_id for update;
  select * into t from conversation.turns where turn_id=p_turn_id and customer_id=cid;
  if t.state='processing' and w.ready and not exists(select 1 from actions.workflow_commands b join actions.commands c using(command_id)
      where b.turn_id=p_turn_id and c.state='proposed') and (w.state in ('awaiting_confirmation','failed')
      or (w.state='running' and (w.resume_owner=p_worker_id or w.lease_until<=clock_timestamp()))) then
    perform conversation.require_checkpoint_run(w.session_id,w.case_id,t.run_id,null);
    update conversation.turns set run_id=p_worker_id where turn_id=p_turn_id returning * into t;
    update conversation.agent_workflows set state='running',resume_owner=p_worker_id,lease_until=clock_timestamp()+interval '2 minutes',error_code=null where turn_id=p_turn_id;
    acquired:=true;
    perform audit.record('chat','chat.workflow_resumed','success','conversation.turns',p_turn_id::text,'{}',w.case_id,w.session_id);
  end if;
  return jsonb_build_object('acquired',acquired,'workflow',conversation.workflow_status(p_turn_id,cid),'turn',to_jsonb(t));
end $$;

create function public.workflow_resume_failed(p_turn_id uuid,p_worker_id uuid)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); w conversation.agent_workflows;
begin
  select * into w from conversation.agent_workflows where turn_id=p_turn_id and customer_id=cid;
  if not found then raise exception 'owned workflow not found' using errcode='P0002'; end if;
  perform 1 from conversation.sessions where session_id=w.session_id for update;
  select * into w from conversation.agent_workflows where turn_id=p_turn_id for update;
  if w.state<>'running' or w.resume_owner is distinct from p_worker_id then raise exception 'workflow resume owner changed' using errcode='23505'; end if;
  update conversation.agent_workflows set state='failed',resume_owner=null,lease_until=null,error_code='resume_failed' where turn_id=p_turn_id;
  perform audit.record('chat','chat.workflow_failed','failed','conversation.turns',p_turn_id::text,'{}',w.case_id,w.session_id);
  return conversation.workflow_status(p_turn_id,cid);
end $$;

create function public.workflow_read_customer(p_turn_id uuid,p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id(); result jsonb;
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'workflow scope must match the current customer' using errcode='42501';
  end if;
  select jsonb_build_object('workflow',conversation.workflow_status(t.turn_id,cid),'turn',to_jsonb(t)) into result
    from conversation.turns t join conversation.agent_workflows w using(turn_id)
    where t.turn_id=p_turn_id and t.customer_id=cid and w.customer_id=cid;
  if result is null then raise exception 'owned workflow not found' using errcode='P0002'; end if;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','conversation.agent_workflows','resource_id',p_turn_id::text,'details',jsonb_build_object('tool','workflow.read','result_count',1))));
  return result;
end $$;

create function conversation.finish_agent_workflow()
returns trigger language plpgsql security definer set search_path=''
as $$
declare command actions.commands; failure_code text;
begin
  if new.state='processing' or old.state<>'processing' then return null; end if;
  if new.state='completed' and exists(select 1 from actions.workflow_commands b join actions.commands c using(command_id)
      where b.turn_id=new.turn_id and c.state='proposed') then
    raise exception 'a turn cannot complete with undecided workflow actions' using errcode='23514';
  end if;
  if new.state='failed' then
    for command in select c.* from actions.workflow_commands b join actions.commands c using(command_id)
      where b.turn_id=new.turn_id and c.state='proposed' for update of c loop
      update actions.commands set state='rejected' where command_id=command.command_id;
      perform audit.record('action','action.rejected','success','actions.commands',command.command_id::text,
        jsonb_build_object('reason','workflow_stopped'),new.case_id,new.session_id);
    end loop;
    select payload#>>'{error,code}' into failure_code from conversation.messages where message_id=new.answer_message_id;
  end if;
  update conversation.agent_workflows set state=case when new.state='completed' then 'completed' else 'cancelled' end,
    resume_owner=null,lease_until=null,error_code=failure_code where turn_id=new.turn_id;
  return null;
end $$;
revoke all on function conversation.finish_agent_workflow() from public,anon,authenticated;
create trigger finish_agent_workflow after update of state on conversation.turns for each row execute function conversation.finish_agent_workflow();

revoke all on function public.workflow_propose_action(uuid,uuid,uuid,text,uuid,text,jsonb,text),public.workflow_mark_waiting(uuid,uuid,text),
  public.workflow_acquire_resume(uuid,uuid),public.workflow_resume_failed(uuid,uuid),public.action_confirm(uuid),public.action_reject(uuid) from public,anon;
grant execute on function public.workflow_propose_action(uuid,uuid,uuid,text,uuid,text,jsonb,text),public.workflow_mark_waiting(uuid,uuid,text),
  public.workflow_acquire_resume(uuid,uuid),public.workflow_resume_failed(uuid,uuid),public.action_confirm(uuid),public.action_reject(uuid) to authenticated;
revoke all on function public.workflow_read_customer(uuid,bigint) from public,anon;
grant execute on function public.workflow_read_customer(uuid,bigint) to authenticated;
