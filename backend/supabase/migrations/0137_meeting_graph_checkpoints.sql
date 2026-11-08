-- One persistent LangGraph thread per meeting. Checkpoint envelopes are HMAC-authenticated by the worker, not trusted JSON/pickle.
create table meetings.graph_runs (
 meeting_id uuid primary key references rm.appointments on delete cascade,run_id uuid not null unique,
 event_id bigint not null references meetings.events,claim_token uuid not null,finish_requested boolean not null,
 closed_at timestamptz
);
create table meetings.graph_checkpoints (
 meeting_id uuid not null references rm.appointments on delete cascade,namespace text not null,checkpoint_id text not null,
 data jsonb not null,created_at timestamptz not null default clock_timestamp(),primary key(meeting_id,namespace,checkpoint_id)
);
create table meetings.graph_writes (
 meeting_id uuid not null references rm.appointments on delete cascade,namespace text not null,checkpoint_id text not null,
 task_id text not null,task_path text not null,write_index int not null,channel text not null,data jsonb not null,
 primary key(meeting_id,namespace,checkpoint_id,task_id,write_index)
);
create table meetings.graph_pending (
 meeting_id uuid primary key references rm.appointments on delete cascade,fingerprint text not null
);
do $$ declare name text;begin
 foreach name in array array['graph_runs','graph_checkpoints','graph_writes','graph_pending'] loop
  execute format('alter table meetings.%I enable row level security',name);
  execute format('revoke all on meetings.%I from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress',name);
 end loop;
end $$;

create function meetings.graph_open(p_event bigint,p_token uuid,p_run uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;r meetings.graph_runs;requested boolean:=false;
begin
 e:=meetings.require_claim(p_event,p_token);
 if p_run is null then raise exception 'a private graph run ID is required' using errcode='22023';end if;
 select * into r from meetings.graph_runs where meeting_id=e.meeting_id;
 requested:=coalesce(r.event_id=e.event_id and r.finish_requested and r.closed_at is null,false);
 insert into meetings.graph_runs values(e.meeting_id,p_run,e.event_id,p_token,requested,null)
 on conflict(meeting_id) do update set run_id=excluded.run_id,event_id=excluded.event_id,claim_token=excluded.claim_token,
  finish_requested=excluded.finish_requested,closed_at=null;
 return jsonb_build_object('meeting_id',e.meeting_id,'event_id',e.event_id,'run_id',p_run);
end $$;
create function meetings.require_graph(p_meeting uuid,p_run uuid) returns meetings.graph_runs
language plpgsql volatile security definer set search_path='' as $$
declare r meetings.graph_runs;
begin
 perform 1 from rm.appointments where appointment_id=p_meeting for update;
 select * into r from meetings.graph_runs where meeting_id=p_meeting for update;
 if not found or p_run is null or r.run_id<>p_run or r.closed_at is not null then
  raise exception 'meeting graph run is closed or superseded' using errcode='P1002';end if;
 perform meetings.require_claim(r.event_id,r.claim_token);
 return r;
end $$;

alter function meetings.finish(bigint,uuid,boolean,text) rename to finish_before_graph;
create function meetings.finish(p_event_id bigint,p_token uuid,p_ok boolean,p_error_code text) returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
 if p_ok and p_error_code is null and exists(select 1 from meetings.graph_runs where event_id=p_event_id and claim_token=p_token and closed_at is null) then
  e:=meetings.require_claim(p_event_id,p_token);
  update meetings.graph_runs set finish_requested=true where event_id=p_event_id and claim_token=p_token and closed_at is null;
  return e;
 end if;
 return meetings.finish_before_graph(p_event_id,p_token,p_ok,p_error_code);
end $$;
create function meetings.graph_close(p_meeting uuid,p_run uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare r meetings.graph_runs;
begin
 r:=meetings.require_graph(p_meeting,p_run);
 if not r.finish_requested then raise exception 'the claimed business event must finish before its graph run closes' using errcode='P1001';end if;
 perform meetings.finish_before_graph(r.event_id,r.claim_token,true,null);
 update meetings.graph_runs set closed_at=clock_timestamp() where meeting_id=p_meeting and run_id=p_run;
end $$;
create function meetings.graph_status(p_meeting uuid,p_run uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
 perform meetings.require_graph(p_meeting,p_run);
 return meetings.sync_lifecycle(p_meeting);
end $$;
create function meetings.graph_envelope(p_data jsonb) returns void language plpgsql immutable set search_path='' as $$
begin
 if p_data is null or (select array_agg(key order by key) from jsonb_object_keys(p_data) key) is distinct from array['payload','signature']
  or jsonb_typeof(p_data->'payload') is distinct from 'string' or octet_length(p_data->>'payload')>524288
  or jsonb_typeof(p_data->'signature') is distinct from 'string' or p_data->>'signature'!~'^[a-f0-9]{64}$'
 then raise exception 'checkpoint requires a bounded signed JSON envelope' using errcode='22023';end if;
end $$;
create function meetings.graph_read(p_meeting uuid,p_run uuid,p_namespace text,p_checkpoint text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare c meetings.graph_checkpoints;
begin
 perform meetings.require_graph(p_meeting,p_run);
 if p_namespace is distinct from '' then raise exception 'unknown meeting graph namespace' using errcode='22023';end if;
 select * into c from meetings.graph_checkpoints where meeting_id=p_meeting and namespace=p_namespace
  and (p_checkpoint is null or checkpoint_id=p_checkpoint) order by checkpoint_id desc limit 1;
 if not found then return null;end if;
 return jsonb_build_object('checkpoint_id',c.checkpoint_id,'namespace',c.namespace,'data',c.data,'writes',
  (select coalesce(jsonb_agg(jsonb_build_object('task_id',w.task_id,'task_path',w.task_path,'write_index',w.write_index,'channel',w.channel,'data',w.data)
   order by w.task_id,w.write_index),'[]') from meetings.graph_writes w where w.meeting_id=p_meeting and w.namespace=c.namespace and w.checkpoint_id=c.checkpoint_id));
end $$;
create function meetings.graph_save(p_meeting uuid,p_run uuid,p_namespace text,p_checkpoint text,p_data jsonb) returns void
language plpgsql volatile security definer set search_path='' as $$
declare existing jsonb;
begin
 perform meetings.require_graph(p_meeting,p_run);perform meetings.graph_envelope(p_data);
 if p_namespace is distinct from '' or p_checkpoint is null or length(p_checkpoint) not between 1 and 100 then
  raise exception 'checkpoint requires the current meeting namespace and a bounded ID' using errcode='22023';end if;
 select data into existing from meetings.graph_checkpoints where meeting_id=p_meeting and namespace=p_namespace and checkpoint_id=p_checkpoint;
 if existing is not null and existing<>p_data then raise exception 'checkpoint ID already contains different signed state' using errcode='23505';end if;
 insert into meetings.graph_checkpoints(meeting_id,namespace,checkpoint_id,data) values(p_meeting,p_namespace,p_checkpoint,p_data) on conflict do nothing;
end $$;
create function meetings.graph_save_writes(p_meeting uuid,p_run uuid,p_namespace text,p_checkpoint text,p_writes jsonb) returns void
language plpgsql volatile security definer set search_path='' as $$
declare item jsonb;r meetings.graph_runs;e meetings.events;
begin
 -- A domain failure may already have released its queue claim. Only its sanitised error write may follow that release.
 if jsonb_typeof(p_writes)='array' and jsonb_array_length(p_writes)=1 and p_writes->0->>'channel'='__error__' then
  perform 1 from rm.appointments where appointment_id=p_meeting for update;
  select * into r from meetings.graph_runs where meeting_id=p_meeting for update;
  select * into e from meetings.events where event_id=r.event_id;
  if r.run_id is distinct from p_run or r.closed_at is not null or
   not (e.status in ('pending','failed') and e.claim_token is null or e.status='processing' and e.claim_token=r.claim_token and e.available_at>clock_timestamp())
  then raise exception 'error checkpoint run is closed, expired or superseded' using errcode='P1002';end if;
 else perform meetings.require_graph(p_meeting,p_run);end if;
 if p_namespace is distinct from '' or p_checkpoint is null or length(p_checkpoint) not between 1 and 100
  or jsonb_typeof(p_writes) is distinct from 'array' or jsonb_array_length(p_writes)>100 then
  raise exception 'pending writes require the current meeting namespace and bounded records' using errcode='22023';end if;
 for item in select value from jsonb_array_elements(p_writes) loop
  perform meetings.graph_envelope(item->'data');
  if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['channel','data','task_id','task_path','write_index']
   or length(item->>'task_id') not between 1 and 100 or length(item->>'task_path')>1000 or length(item->>'channel') not between 1 and 200
   or jsonb_typeof(item->'write_index') is distinct from 'number' or item->>'write_index'!~'^-?[0-9]{1,6}$'
  then raise exception 'invalid checkpoint pending write identity' using errcode='22023';end if;
  insert into meetings.graph_writes values(p_meeting,p_namespace,p_checkpoint,item->>'task_id',item->>'task_path',(item->>'write_index')::int,item->>'channel',item->'data')
  on conflict(meeting_id,namespace,checkpoint_id,task_id,write_index) do update set data=excluded.data where graph_writes.write_index<0;
 end loop;
end $$;

-- Business transitions wake the durable parent on the worker's next poll, without starting a model in a request.
alter table meetings.events drop constraint events_event_type_check;
alter table meetings.events add constraint events_event_type_check check(event_type in
 ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','brief.requested','meeting.started','meeting.ended','transcript.ready','transcript.fetch','transcript.discover','lifecycle.changed'));
create or replace function meetings.apply_lifecycle(p_event_id bigint,p_token uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
 e:=meetings.require_claim(p_event_id,p_token);
 if e.event_type not in ('brief.requested','lifecycle.changed') then perform meetings.apply_lifecycle_before_parent(p_event_id,p_token);end if;
 perform meetings.checkpoint_claim(p_event_id,p_token);
end $$;
create function meetings.mark_graph_pending() returns trigger language plpgsql security definer set search_path='' as $$
begin
 insert into meetings.graph_pending values(new.meeting_id,meetings.hash(jsonb_build_array(new.stage,new.waiting_for,new.brief_version,new.minutes_version,new.error_code,
  (select revision from meetings.workflows where meeting_id=new.meeting_id))))
 on conflict(meeting_id) do update set fingerprint=excluded.fingerprint;
 return new;
end $$;
create trigger meeting_graph_pending after insert or update of stage,waiting_for,brief_version,minutes_version,error_code on meetings.threads
 for each row execute function meetings.mark_graph_pending();
create function meetings.enqueue_graph_refresh(p_meeting uuid) returns int
language plpgsql volatile security definer set search_path='' as $$
declare pending_fingerprint text;a rm.appointments;revision int;
begin
 select * into a from rm.appointments where appointment_id=p_meeting for update skip locked;
 if not found then return 0;end if;
 select pending.fingerprint into pending_fingerprint from meetings.graph_pending pending where meeting_id=p_meeting for update;
 if not found then return 0;end if;
 select w.revision into revision from meetings.workflows w where meeting_id=p_meeting;
 insert into meetings.events(event_type,meeting_id,customer_id,advisor_id,payload,dedupe_key)
 values('lifecycle.changed',p_meeting,a.customer_id,a.advisor_id,jsonb_build_object('revision',coalesce(revision,1)),
  'lifecycle:'||p_meeting||':'||pending_fingerprint) on conflict(dedupe_key) do nothing;
 delete from meetings.graph_pending where meeting_id=p_meeting and graph_pending.fingerprint=pending_fingerprint;
 return 1;
end $$;
create function meetings.schedule_graph_updates() returns int language plpgsql volatile security definer set search_path='' as $$
declare id uuid;counted int:=0;
begin
 for id in select meeting_id from meetings.graph_pending order by meeting_id limit 100 loop counted:=counted+meetings.enqueue_graph_refresh(id);end loop;
 return counted;
end $$;
create function meetings.schedule_registered_graphs(p_meetings uuid[]) returns int
language plpgsql volatile security definer set search_path='' as $$
declare id uuid;counted int:=0;
begin
 if cardinality(p_meetings) not between 1 and 20 or array_position(p_meetings,null) is not null then raise exception 'register one to twenty explicit meeting IDs' using errcode='22023';end if;
 for id in select meeting_id from meetings.graph_pending where meeting_id=any(p_meetings) order by meeting_id limit 100
 loop counted:=counted+meetings.enqueue_graph_refresh(id);end loop;
 return counted;
end $$;
revoke all on function meetings.graph_open(bigint,uuid,uuid),meetings.require_graph(uuid,uuid),meetings.finish_before_graph(bigint,uuid,boolean,text),
 meetings.finish(bigint,uuid,boolean,text),meetings.graph_close(uuid,uuid),meetings.graph_status(uuid,uuid),meetings.graph_envelope(jsonb),
 meetings.graph_read(uuid,uuid,text,text),meetings.graph_save(uuid,uuid,text,text,jsonb),meetings.graph_save_writes(uuid,uuid,text,text,jsonb),
 meetings.mark_graph_pending(),meetings.enqueue_graph_refresh(uuid),meetings.schedule_graph_updates(),meetings.schedule_registered_graphs(uuid[])
 from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
grant execute on function meetings.graph_open(bigint,uuid,uuid),meetings.finish(bigint,uuid,boolean,text),meetings.graph_close(uuid,uuid),meetings.graph_status(uuid,uuid),
 meetings.graph_read(uuid,uuid,text,text),meetings.graph_save(uuid,uuid,text,text,jsonb),meetings.graph_save_writes(uuid,uuid,text,text,jsonb),
 meetings.schedule_graph_updates(),meetings.schedule_registered_graphs(uuid[]) to demobank_meetings;
insert into meetings.graph_pending select meeting_id,meetings.hash(jsonb_build_array(stage,waiting_for,brief_version,minutes_version,error_code)) from meetings.threads;
