-- Full LangGraph snapshots and pending writes. Envelopes are authenticated by the server before restore.
alter table conversation.checkpoints add column case_id uuid references conversation.cases;
alter table conversation.checkpoints add column namespace text not null default '';
create index checkpoints_case_idx on conversation.checkpoints(session_id,case_id,namespace,checkpoint_id desc);
create table conversation.checkpoint_writes (
  session_id uuid not null,
  checkpoint_id text not null,
  customer_id bigint references banking.customer_profiles(customer_id) on delete cascade,
  task_id text not null,
  task_path text not null,
  write_index int not null,
  channel text not null,
  data jsonb not null,
  primary key(session_id,checkpoint_id,task_id,write_index),
  foreign key(session_id,checkpoint_id) references conversation.checkpoints on delete cascade
);
alter table conversation.checkpoint_writes enable row level security;
revoke all on conversation.checkpoint_writes from public,anon,authenticated;
grant select on conversation.checkpoint_writes to authenticated;
create policy own_rows on conversation.checkpoint_writes for select to authenticated using(customer_id=banking.my_customer_id());

create function conversation.require_checkpoint_case(p_session_id uuid,p_case_id uuid,p_guest_token text)
returns bigint language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_access(p_session_id,p_guest_token);
begin
  if not exists(select 1 from conversation.turns where session_id=p_session_id and case_id=p_case_id
      and customer_id is not distinct from cid) then
    raise exception 'workflow case not found in this conversation' using errcode='P0002';
  end if;
  return cid;
end $$;
revoke all on function conversation.require_checkpoint_case(uuid,uuid,text) from public,anon;
grant execute on function conversation.require_checkpoint_case(uuid,uuid,text) to authenticated;

-- Serialise workflow writes with cancellation/finish on the session lock.
create function conversation.require_checkpoint_run(p_session_id uuid,p_case_id uuid,p_run_id uuid,p_guest_token text)
returns bigint language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint; s conversation.sessions;
begin
  select * into s from conversation.sessions where session_id=p_session_id for update;
  cid:=conversation.require_checkpoint_case(p_session_id,p_case_id,p_guest_token);
  if not exists(select 1 from conversation.turns where session_id=p_session_id and case_id=p_case_id
      and run_id=p_run_id and state='processing' and turn_id=s.active_turn_id) then
    raise exception 'workflow is no longer the active processing turn' using errcode='23505';
  end if;
  return cid;
end $$;
revoke all on function conversation.require_checkpoint_run(uuid,uuid,uuid,text) from public,anon,authenticated;

create function public.workflow_save_checkpoint(p_session_id uuid,p_case_id uuid,p_run_id uuid,p_namespace text,p_checkpoint_id text,p_data jsonb,p_guest_token text)
returns void language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_checkpoint_run(p_session_id,p_case_id,p_run_id,p_guest_token); existing conversation.checkpoints;
begin
  if p_namespace is null or length(p_namespace)>200 or p_checkpoint_id is null or length(p_checkpoint_id) not between 1 and 100
      or p_data is null or jsonb_typeof(p_data)<>'object' or octet_length(p_data::text)>1048576 then
    raise exception 'checkpoint requires a bounded namespace, id and envelope' using errcode='22023';
  end if;
  select * into existing from conversation.checkpoints where session_id=p_session_id and checkpoint_id=p_checkpoint_id;
  if found then
    if existing.case_id is distinct from p_case_id or existing.namespace<>p_namespace or existing.data<>p_data then
      raise exception 'checkpoint id already names a different snapshot' using errcode='23505';
    end if;
    return;
  end if;
  insert into conversation.checkpoints(session_id,checkpoint_id,customer_id,case_id,namespace,data)
    values(p_session_id,p_checkpoint_id,cid,p_case_id,p_namespace,p_data);
  perform audit.record('chat','chat.checkpoint_saved','success','conversation.checkpoints',p_checkpoint_id,
    jsonb_build_object('snapshot_count',1),p_case_id,p_session_id);
end $$;

create function public.workflow_save_writes(p_session_id uuid,p_case_id uuid,p_run_id uuid,p_namespace text,p_checkpoint_id text,p_writes jsonb,p_guest_token text)
returns void language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_checkpoint_run(p_session_id,p_case_id,p_run_id,p_guest_token);
  item jsonb; changed int; total int:=0;
begin
  if not exists(select 1 from conversation.checkpoints where session_id=p_session_id and case_id=p_case_id
      and namespace=p_namespace and checkpoint_id=p_checkpoint_id) then
    raise exception 'pending writes require a saved checkpoint in this workflow' using errcode='P0002';
  end if;
  if p_writes is null or jsonb_typeof(p_writes)<>'array' or jsonb_array_length(p_writes)>100
      or octet_length(p_writes::text)>1048576 then
    raise exception 'pending writes require a bounded array' using errcode='22023';
  end if;
  for item in select value from jsonb_array_elements(p_writes) loop
    if jsonb_typeof(item->'task_id') is distinct from 'string' or length(item->>'task_id') not between 1 and 100
        or jsonb_typeof(item->'task_path') is distinct from 'string' or length(item->>'task_path')>1000
        or jsonb_typeof(item->'channel') is distinct from 'string' or length(item->>'channel') not between 1 and 200
        or jsonb_typeof(item->'write_index') is distinct from 'number' or (item->>'write_index') !~ '^-?[0-9]{1,8}$'
        or jsonb_typeof(item->'data') is distinct from 'object' then
      raise exception 'pending write requires task, channel, integer index and envelope' using errcode='22023';
    end if;
    insert into conversation.checkpoint_writes(session_id,checkpoint_id,customer_id,task_id,task_path,write_index,channel,data)
      values(p_session_id,p_checkpoint_id,cid,item->>'task_id',item->>'task_path',(item->>'write_index')::int,item->>'channel',item->'data')
      on conflict(session_id,checkpoint_id,task_id,write_index) do update set channel=excluded.channel,data=excluded.data,task_path=excluded.task_path
        where excluded.write_index<0 and (conversation.checkpoint_writes.channel,conversation.checkpoint_writes.data)
          is distinct from (excluded.channel,excluded.data);
    get diagnostics changed=row_count;
    total:=total+changed;
  end loop;
  if total>0 then
    perform audit.record('chat','chat.checkpoint_writes','success','conversation.checkpoints',p_checkpoint_id,
      jsonb_build_object('write_count',total),p_case_id,p_session_id);
  end if;
end $$;

-- Customer reads retain both explicit customer predicates and the caller's table RLS.
create function conversation.checkpoint_snapshot(p_session_id uuid,p_case_id uuid,p_customer_id bigint,p_namespace text,p_checkpoint_id text)
returns jsonb language sql stable security invoker set search_path=''
as $$
  select jsonb_build_object('checkpoint_id',c.checkpoint_id,'namespace',c.namespace,'data',c.data,
    'writes',coalesce((select jsonb_agg(jsonb_build_object('task_id',w.task_id,'task_path',w.task_path,'write_index',w.write_index,'channel',w.channel,'data',w.data)
      order by w.task_path,w.task_id,w.write_index) from conversation.checkpoint_writes w where w.session_id=c.session_id and w.checkpoint_id=c.checkpoint_id
        and w.customer_id is not distinct from p_customer_id),'[]'::jsonb))
  from conversation.checkpoints c where c.session_id=p_session_id and c.case_id=p_case_id and c.namespace=p_namespace
    and c.customer_id is not distinct from p_customer_id and (p_checkpoint_id is null or c.checkpoint_id=p_checkpoint_id)
  order by c.checkpoint_id desc limit 1
$$;
revoke all on function conversation.checkpoint_snapshot(uuid,uuid,bigint,text,text) from public,anon;
grant execute on function conversation.checkpoint_snapshot(uuid,uuid,bigint,text,text) to authenticated;

create function public.workflow_read_customer(p_session_id uuid,p_case_id uuid,p_namespace text,p_checkpoint_id text)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=conversation.require_checkpoint_case(p_session_id,p_case_id,null); result jsonb;
begin
  result:=conversation.checkpoint_snapshot(p_session_id,p_case_id,cid,p_namespace,p_checkpoint_id);
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type','conversation.checkpoints','resource_id',p_case_id::text,'session_id',p_session_id::text,'case_id',p_case_id::text,
    'details',jsonb_build_object('tool','workflow.read','snapshot_count',case when result is null then 0 else 1 end))));
  return result;
end $$;
create function public.workflow_read_guest(p_session_id uuid,p_case_id uuid,p_namespace text,p_checkpoint_id text,p_guest_token text)
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_checkpoint_case(p_session_id,p_case_id,p_guest_token); result jsonb;
begin
  if auth.uid() is not null then raise exception 'guest workflow requires a guest session' using errcode='42501'; end if;
  result:=conversation.checkpoint_snapshot(p_session_id,p_case_id,cid,p_namespace,p_checkpoint_id);
  perform audit.record('data','data.read','success','conversation.checkpoints',p_case_id::text,
    jsonb_build_object('tool','workflow.read','snapshot_count',case when result is null then 0 else 1 end),p_case_id,p_session_id);
  return result;
end $$;
revoke all on function public.workflow_save_checkpoint(uuid,uuid,uuid,text,text,jsonb,text),
  public.workflow_save_writes(uuid,uuid,uuid,text,text,jsonb,text),public.workflow_read_guest(uuid,uuid,text,text,text) from public;
grant execute on function public.workflow_save_checkpoint(uuid,uuid,uuid,text,text,jsonb,text),
  public.workflow_save_writes(uuid,uuid,uuid,text,text,jsonb,text),public.workflow_read_guest(uuid,uuid,text,text,text) to anon,authenticated;
revoke all on function public.workflow_read_customer(uuid,uuid,text,text) from public,anon;
grant execute on function public.workflow_read_customer(uuid,uuid,text,text) to authenticated;
