-- LangGraph schedules pending writes independently of snapshot saves, including the initial step.
-- Scope writes to their owned case directly; a snapshot may arrive after its writes. No retry hides this ordering.
alter table conversation.checkpoint_writes add column case_id uuid references conversation.cases on delete cascade;
alter table conversation.checkpoint_writes add column namespace text;
update conversation.checkpoint_writes w set case_id=c.case_id,namespace=c.namespace
  from conversation.checkpoints c where c.session_id=w.session_id and c.checkpoint_id=w.checkpoint_id;
alter table conversation.checkpoint_writes alter column case_id set not null;
alter table conversation.checkpoint_writes alter column namespace set not null;
alter table conversation.checkpoint_writes drop constraint checkpoint_writes_session_id_checkpoint_id_fkey;
alter table conversation.checkpoint_writes add foreign key(session_id) references conversation.sessions on delete cascade;
alter table conversation.checkpoint_writes drop constraint checkpoint_writes_pkey;
alter table conversation.checkpoint_writes add primary key(session_id,case_id,namespace,checkpoint_id,task_id,write_index);

create or replace function public.workflow_save_writes(p_session_id uuid,p_case_id uuid,p_run_id uuid,p_namespace text,p_checkpoint_id text,p_writes jsonb,p_guest_token text)
returns void language plpgsql volatile security definer set search_path=''
as $$
declare cid bigint:=conversation.require_checkpoint_run(p_session_id,p_case_id,p_run_id,p_guest_token);
  item jsonb; changed int; total int:=0;
begin
  if p_namespace is null or length(p_namespace)>200 or p_checkpoint_id is null or length(p_checkpoint_id) not between 1 and 100
      or p_writes is null or jsonb_typeof(p_writes)<>'array' or jsonb_array_length(p_writes)>100
      or octet_length(p_writes::text)>1048576 then
    raise exception 'pending writes require a bounded namespace, checkpoint id and array' using errcode='22023';
  end if;
  for item in select value from jsonb_array_elements(p_writes) loop
    if jsonb_typeof(item->'task_id') is distinct from 'string' or length(item->>'task_id') not between 1 and 100
        or jsonb_typeof(item->'task_path') is distinct from 'string' or length(item->>'task_path')>1000
        or jsonb_typeof(item->'channel') is distinct from 'string' or length(item->>'channel') not between 1 and 200
        or jsonb_typeof(item->'write_index') is distinct from 'number' or (item->>'write_index') !~ '^-?[0-9]{1,8}$'
        or jsonb_typeof(item->'data') is distinct from 'object' then
      raise exception 'pending write requires task, channel, integer index and envelope' using errcode='22023';
    end if;
    insert into conversation.checkpoint_writes(session_id,case_id,namespace,checkpoint_id,customer_id,task_id,task_path,write_index,channel,data)
      values(p_session_id,p_case_id,p_namespace,p_checkpoint_id,cid,item->>'task_id',item->>'task_path',(item->>'write_index')::int,item->>'channel',item->'data')
      on conflict(session_id,case_id,namespace,checkpoint_id,task_id,write_index) do update set channel=excluded.channel,data=excluded.data,task_path=excluded.task_path
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

create or replace function conversation.checkpoint_snapshot(p_session_id uuid,p_case_id uuid,p_customer_id bigint,p_namespace text,p_checkpoint_id text)
returns jsonb language sql stable security invoker set search_path=''
as $$
  select jsonb_build_object('checkpoint_id',c.checkpoint_id,'namespace',c.namespace,'data',c.data,
    'writes',coalesce((select jsonb_agg(jsonb_build_object('task_id',w.task_id,'task_path',w.task_path,'write_index',w.write_index,'channel',w.channel,'data',w.data)
      order by w.task_path,w.task_id,w.write_index) from conversation.checkpoint_writes w where w.session_id=c.session_id and w.case_id=c.case_id
        and w.namespace=c.namespace and w.checkpoint_id=c.checkpoint_id and w.customer_id is not distinct from p_customer_id),'[]'::jsonb))
  from conversation.checkpoints c where c.session_id=p_session_id and c.case_id=p_case_id and c.namespace=p_namespace
    and c.customer_id is not distinct from p_customer_id and (p_checkpoint_id is null or c.checkpoint_id=p_checkpoint_id)
  order by c.checkpoint_id desc limit 1
$$;
