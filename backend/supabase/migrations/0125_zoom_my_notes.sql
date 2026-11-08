-- Isolated candidate: provider bindings and consent-fenced My Notes ingestion. Private schemas stay unexposed.
alter table meetings.events drop constraint events_event_type_check;
alter table meetings.events add constraint events_event_type_check check(event_type in
 ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','brief.requested','meeting.started','meeting.ended','transcript.ready','transcript.fetch'));
alter table meetings.events drop constraint events_status_check;
alter table meetings.events add constraint events_status_check check(status in ('pending','waiting','processing','done','failed'));
alter table meetings.sources drop constraint sources_origin_check;
alter table meetings.sources add constraint sources_origin_check check(origin in
 ('uploaded_synthetic_transcript','rm_typed_notes','zoom_export_vtt','zoom_export_txt','zoom_my_notes_api'));

create table meetings.zoom_bindings (
 binding_id uuid primary key, meeting_id uuid not null references meetings.workflows,
 revision int not null, schedule_hash text not null, provider_id bigint not null,
 account_id text not null check(length(account_id) between 1 and 128), host_id text not null check(length(host_id) between 1 and 128),
 original_uuid text not null check(length(original_uuid) between 1 and 200),
 unique(meeting_id,revision),unique(account_id,original_uuid)
);
create table meetings.zoom_fetch_jobs (
 job_id uuid primary key,binding_id uuid not null unique references meetings.zoom_bindings,
 note_id text not null check(length(note_id) between 1 and 200),event_id bigint unique references meetings.events,
 received_at timestamptz not null default clock_timestamp()
);
create table meetings.zoom_evidence (
 source_id uuid primary key references meetings.sources,job_id uuid not null references meetings.zoom_fetch_jobs,
 transcript_hash text not null check(transcript_hash~'^[a-f0-9]{64}$'),
 segments jsonb not null check(jsonb_typeof(segments)='array'),
 coverage text not null check(coverage='unknown'),reviewed_by uuid references auth.users,
 speaker_mapping jsonb not null check(jsonb_typeof(speaker_mapping)='object')
);
do $$ declare name text;begin
 foreach name in array array['zoom_bindings','zoom_fetch_jobs','zoom_evidence'] loop
 execute format('alter table meetings.%I enable row level security',name);
 execute format('revoke all on meetings.%I from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress',name);
 end loop;
end $$;

create function public.meeting_zoom_link_bind(p_meeting_id uuid,p_provider_id bigint,p_join_url text,p_starts_at timestamptz,
 p_account_id text,p_host_id text,p_original_uuid text) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;b meetings.zoom_bindings;
begin
 w:=meetings.require_rm(p_meeting_id);
 if p_account_id is null or length(p_account_id) not between 1 and 128 or p_host_id is null or length(p_host_id) not between 1 and 128
  or p_original_uuid is null or length(p_original_uuid) not between 1 and 200 or p_provider_id is null or p_provider_id<=0
 then raise exception 'verified Zoom account, host, original occurrence UUID and meeting number are required' using errcode='22023';end if;
 perform public.meeting_link_save(p_meeting_id,p_provider_id,p_join_url,p_starts_at);
 select * into b from meetings.zoom_bindings where meeting_id=p_meeting_id and revision=w.revision;
 if found then
  if b.provider_id<>p_provider_id or b.account_id<>p_account_id or b.host_id<>p_host_id or b.original_uuid<>p_original_uuid
  then raise exception 'Zoom occurrence is already bound; reconcile the provider outcome explicitly' using errcode='23505';end if;
 else
  insert into meetings.zoom_bindings values(gen_random_uuid(),p_meeting_id,w.revision,w.schedule_hash,p_provider_id,p_account_id,p_host_id,p_original_uuid);
 end if;
 return jsonb_build_object('linked',true,'meeting_id',p_meeting_id);
end $$;

create function meetings.accept_zoom_note(p_account text,p_operator text,p_meeting text,p_note text,p_type text) returns bigint
language plpgsql volatile security definer set search_path='' as $$
declare b meetings.zoom_bindings;w meetings.workflows;j meetings.zoom_fetch_jobs;id bigint;job uuid:=gen_random_uuid();
begin
 if p_type is null or p_type not in ('my_notes.transcript_generated','my_notes.note_completed') or p_note is null or length(p_note) not between 1 and 200
  or p_meeting is null or length(p_meeting) not between 1 and 200
 then raise exception 'a supported My Notes event needs a note and an associated meeting' using errcode='22023';end if;
 select x.* into b from meetings.zoom_bindings x join meetings.workflows wf using(meeting_id)
  where x.account_id=p_account and x.host_id=p_operator and x.revision=wf.revision
  and (x.original_uuid=p_meeting or x.provider_id::text=p_meeting);
 if not found then raise exception 'Zoom account, operator and occurrence are not registered' using errcode='42501';end if;
 w:=meetings.require_current(b.meeting_id);
 if w.revision<>b.revision or w.schedule_hash<>b.schedule_hash then raise exception 'Zoom occurrence binding is stale' using errcode='P1001';end if;
 select * into j from meetings.zoom_fetch_jobs where binding_id=b.binding_id;
 if found then
  if j.note_id<>p_note then raise exception 'a different note already exists for this occurrence; RM reconciliation is required' using errcode='23505';end if;
  return j.event_id;
 end if;
 insert into meetings.zoom_fetch_jobs(job_id,binding_id,note_id) values(job,b.binding_id,p_note);
 insert into meetings.events(event_type,meeting_id,customer_id,advisor_id,payload,dedupe_key,status)
 select 'transcript.fetch',b.meeting_id,a.customer_id,a.advisor_id,jsonb_build_object('revision',w.revision,'job_id',job),'zoom_note:'||job,
  case when w.state='ended' then 'pending' else 'waiting' end
 from rm.appointments a where a.appointment_id=b.meeting_id returning event_id into id;
 update meetings.zoom_fetch_jobs set event_id=id where job_id=job;
 perform audit.record('action','action.meeting_event_queued','success','meetings.events',id::text,
  jsonb_build_object('event_type','transcript.fetch','meeting_id',b.meeting_id),p_actor_type=>'system',p_actor_id=>'zoom_ingress',
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=b.meeting_id));
 return id;
end $$;

create function meetings.wake_zoom_fetch() returns trigger language plpgsql security definer set search_path='' as $$
begin
 if new.state='ended' then
  update meetings.events set status='pending',available_at=clock_timestamp()
   where meeting_id=new.meeting_id and event_type='transcript.fetch' and status='waiting';
 elsif new.state='cancelled' then
  update meetings.events set status='failed',processed_at=clock_timestamp(),last_error_code='meeting_cancelled'
   where meeting_id=new.meeting_id and event_type='transcript.fetch' and status='waiting';
 end if;
 return null;
end $$;
revoke all on function meetings.wake_zoom_fetch() from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create trigger wake_zoom_fetch after update of state on meetings.workflows for each row when(old.state is distinct from new.state)
 execute function meetings.wake_zoom_fetch();

create function meetings.zoom_fetch_permit(p_event bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;j meetings.zoom_fetch_jobs;b meetings.zoom_bindings;w meetings.workflows;
begin
 e:=meetings.require_claim(p_event,p_token);
 if e.event_type<>'transcript.fetch' then raise exception 'claim is not a My Notes fetch job' using errcode='42501';end if;
 select * into j from meetings.zoom_fetch_jobs where event_id=e.event_id;
 select * into b from meetings.zoom_bindings where binding_id=j.binding_id;
 w:=meetings.require_current(e.meeting_id);
 if b.meeting_id is distinct from e.meeting_id or b.revision<>w.revision or b.schedule_hash<>w.schedule_hash
  then raise exception 'provider binding changed; fetch is refused' using errcode='P1001';end if;
 if not meetings.recording_allowed(e.meeting_id) then raise exception 'transcript notice or permission is no longer current' using errcode='42501';end if;
 if w.state<>'ended' then raise exception 'wait for the documented or provider-confirmed call end before fetching' using errcode='P1003';end if;
 return jsonb_build_object('meeting_id',e.meeting_id,'job_id',j.job_id,'provider_id',b.provider_id,'account_id',b.account_id,
  'host_id',b.host_id,'original_uuid',b.original_uuid,'note_id',j.note_id,'revision',w.revision,'consent_epoch',w.consent_epoch,
  'source_exists',exists(select 1 from meetings.sources where source_id=j.job_id));
end $$;

create function meetings.commit_zoom_transcript(p_event bigint,p_token uuid,p_epoch int,p_cues jsonb,p_segments jsonb,p_hash text) returns bigint
language plpgsql volatile security definer set search_path='' as $$
declare permit jsonb;id bigint;job uuid;meeting uuid;segment jsonb;cue jsonb;position int:=0;previous bigint:=-1;
begin
 permit:=meetings.zoom_fetch_permit(p_event,p_token);job:=(permit->>'job_id')::uuid;meeting:=(permit->>'meeting_id')::uuid;
 if p_epoch is distinct from (permit->>'consent_epoch')::int then raise exception 'permission changed during Zoom retrieval' using errcode='P1001';end if;
 if p_hash is null or p_hash!~'^[a-f0-9]{64}$' or p_segments is null or jsonb_typeof(p_segments)<>'array'
  or jsonb_array_length(p_segments) is distinct from jsonb_array_length(p_cues)
 then raise exception 'Zoom transcript needs its hash and one timing/speaker record per cue' using errcode='22023';end if;
 perform meetings.validate_cues(p_cues,'transcript');
 for segment in select * from jsonb_array_elements(p_segments) loop
  cue:=p_cues->position;position:=position+1;
  if (select array_agg(key order by key) from jsonb_object_keys(segment) key) is distinct from array['cue_id','end_ms','speaker_key','start_ms']
   or segment->>'cue_id' is distinct from cue->>'cue_id' or jsonb_typeof(segment->'speaker_key') is distinct from 'string'
   or segment->>'speaker_key'!~'^speaker[1-9][0-9]{0,2}$'
   or jsonb_typeof(segment->'start_ms') is distinct from 'number' or segment->>'start_ms'!~'^[0-9]{1,10}$'
   or jsonb_typeof(segment->'end_ms') is distinct from 'number' or segment->>'end_ms'!~'^[0-9]{1,10}$'
   or (segment->>'end_ms')::bigint<=(segment->>'start_ms')::bigint
   or (segment->>'start_ms')::bigint<previous
   or (segment->>'end_ms')::bigint>1000*extract(epoch from (select ended_at-started_at from meetings.workflows where meeting_id=meeting))
   or (segment->>'start_ms')::bigint/1000 is distinct from (cue->>'start_seconds')::bigint
   or cue->>'speaker' is distinct from 'unmapped'
  then raise exception 'API timing/speaker metadata must match its unmapped cue' using errcode='22023';end if;
  previous:=(segment->>'start_ms')::bigint;
 end loop;
 id:=meetings.register_transcript(meeting,(permit->>'revision')::int,p_epoch,job,meetings.participants(meeting),p_cues,'zoom_transcript:'||job);
 if exists(select 1 from meetings.zoom_evidence where source_id=job and (transcript_hash<>p_hash or segments<>p_segments))
 then raise exception 'replayed Zoom job returned different evidence' using errcode='23505';end if;
 update meetings.sources set origin='zoom_my_notes_api' where source_id=job;
 insert into meetings.zoom_evidence values(job,job,p_hash,p_segments,'unknown',null,'{}') on conflict(source_id) do nothing;
 return id;
end $$;

create function public.meeting_zoom_evidence(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;s meetings.sources;z meetings.zoom_evidence;
begin
 w:=meetings.require_rm(p_meeting_id);
 if w.current_source is null then return null;end if;
 s:=meetings.require_source(p_meeting_id,w.current_source);
 select * into z from meetings.zoom_evidence where source_id=s.source_id;
 if not found then return null;end if;
 perform meetings.read_audit((select customer_id from rm.appointments where appointment_id=p_meeting_id),'meetings.sources',s.source_id::text,'meeting.zoom_evidence');
 return jsonb_build_object('source_id',s.source_id,'source_hash',s.source_hash,'revision',w.revision,'consent_epoch',w.consent_epoch,
  'cues',s.cues,'segments',z.segments,'mapping',z.speaker_mapping,'coverage',z.coverage);
end $$;

create function public.meeting_zoom_map_speakers(p_meeting_id uuid,p_source uuid,p_hash text,p_revision int,p_epoch int,p_mapping jsonb,p_command uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;s meetings.sources;z meetings.zoom_evidence;receipt jsonb;item record;cues jsonb;event bigint;
 params jsonb:=jsonb_build_array(p_source,p_hash,p_revision,p_epoch,p_mapping);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command,p_meeting_id,'zoom_speakers',params);if receipt is not null then return receipt;end if;
 s:=meetings.require_source(p_meeting_id,p_source);select * into z from meetings.zoom_evidence where source_id=s.source_id;
 if not found or p_hash is distinct from s.source_hash or p_revision is distinct from w.revision or p_epoch is distinct from w.consent_epoch
 then raise exception 'review the current API source and permission version before mapping speakers' using errcode='P1001';end if;
 if p_mapping is null or jsonb_typeof(p_mapping)<>'object' or octet_length(p_mapping::text)>4096 then raise exception 'speaker mapping must be a bounded object' using errcode='22023';end if;
 for item in select * from jsonb_each_text(p_mapping) loop
  if item.value not in ('rm','client','unmapped') or not exists(select 1 from jsonb_array_elements(z.segments) v where v->>'speaker_key'=item.key)
  then raise exception 'map only known source speakers to RM, client or unmapped' using errcode='22023';end if;
 end loop;
 select jsonb_agg(c.value||jsonb_build_object('speaker',coalesce(p_mapping->>(v.value->>'speaker_key'),'unmapped')) order by c.ordinality) into cues
 from jsonb_array_elements(s.cues) with ordinality c(value,ordinality) join jsonb_array_elements(z.segments) v(value) on v.value->>'cue_id'=c.value->>'cue_id';
 event:=meetings.register_transcript(p_meeting_id,w.revision,w.consent_epoch,p_command,meetings.participants(p_meeting_id),cues,'zoom_mapping:'||p_command);
 update meetings.sources set origin='zoom_my_notes_api' where source_id=p_command;
 insert into meetings.zoom_evidence values(p_command,z.job_id,z.transcript_hash,z.segments,z.coverage,auth.uid(),p_mapping);
 receipt:=jsonb_build_object('source_id',p_command,'event_id',event,'origin','zoom_my_notes_api','coverage',z.coverage);
 return meetings.remember(p_command,p_meeting_id,'zoom_speakers',params,receipt);
end $$;

alter function public.meeting_view(uuid) set schema meetings;
alter function meetings.meeting_view(uuid) rename to hybrid_view;
revoke all on function meetings.hybrid_view(uuid) from public,anon,authenticated;
create function public.meeting_view(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;b meetings.zoom_bindings;w meetings.workflows;j meetings.events;z meetings.zoom_evidence;
begin
 result:=meetings.hybrid_view(p_meeting_id);select * into w from meetings.workflows where meeting_id=p_meeting_id;
 select * into b from meetings.zoom_bindings where meeting_id=p_meeting_id and revision=w.revision;
 if found then
  select e.* into j from meetings.events e join meetings.zoom_fetch_jobs f on f.event_id=e.event_id where f.binding_id=b.binding_id;
  result:=result||jsonb_build_object('zoom_capture',jsonb_build_object('state',coalesce(j.status,'awaiting_note'),'error_code',j.last_error_code));
 end if;
 select * into z from meetings.zoom_evidence where source_id=w.current_source;
 if found then result:=result||jsonb_build_object('source_provenance',jsonb_build_object('origin','zoom_my_notes_api','coverage',z.coverage,
  'speaker_basis',case when z.reviewed_by is null then 'unmapped' else 'rm_asserted_mapping' end));end if;
 return result;
end $$;
revoke all on function public.meeting_view(uuid) from public,anon;
grant execute on function public.meeting_view(uuid) to authenticated;

create function meetings.accept_zoom_call(p_account text,p_host text,p_provider bigint,p_uuid text,p_type text,p_start timestamptz,p_end timestamptz) returns uuid
language plpgsql volatile security definer set search_path='' as $$
declare b meetings.zoom_bindings;w meetings.workflows;occurrence uuid;
begin
 select x.* into b from meetings.zoom_bindings x join meetings.workflows wf using(meeting_id)
 where x.account_id=p_account and x.host_id=p_host and x.provider_id=p_provider and x.original_uuid=p_uuid and x.revision=wf.revision;
 if not found then raise exception 'Zoom call account, host and original occurrence are not registered' using errcode='42501';end if;
 if p_type is null or p_type not in ('meeting.started','meeting.ended') or p_start is null or p_start>clock_timestamp()+interval '30 seconds'
  or (p_type='meeting.ended' and (p_end is null or p_end<p_start or p_end>clock_timestamp()+interval '30 seconds'))
 then raise exception 'invalid Zoom call event or time order' using errcode='22023';end if;
 w:=meetings.require_current(b.meeting_id);
 if b.schedule_hash<>w.schedule_hash then raise exception 'Zoom call schedule is stale' using errcode='P1001';end if;
 if w.started_at is not null and w.started_at is distinct from p_start then raise exception 'provider call start conflicts with documented evidence; reconcile explicitly' using errcode='P1001';end if;
 if p_type='meeting.ended' and w.ended_at is not null and w.ended_at is distinct from p_end then
 raise exception 'provider call end conflicts with documented evidence; reconcile explicitly' using errcode='P1001';end if;
 occurrence:=coalesce(w.occurrence_id,gen_random_uuid());
 update meetings.workflows set started_at=p_start,occurrence_id=occurrence,
  ended_at=case when p_type='meeting.ended' then p_end else ended_at end,
  state=case when p_type='meeting.ended' or w.state='ended' then 'ended' else 'in_progress' end where meeting_id=b.meeting_id;
 perform meetings.enqueue_v2('meeting.started',b.meeting_id,jsonb_build_object('revision',w.revision,'occurrence_id',occurrence),'zoom_start:'||b.binding_id);
 if p_type='meeting.ended' then
  perform meetings.enqueue_v2('meeting.ended',b.meeting_id,jsonb_build_object('revision',w.revision,'occurrence_id',occurrence),'zoom_end:'||b.binding_id);
 end if;
 return occurrence;
end $$;

revoke all on function public.meeting_zoom_link_bind(uuid,bigint,text,timestamptz,text,text,text),
 public.meeting_zoom_evidence(uuid),public.meeting_zoom_map_speakers(uuid,uuid,text,int,int,jsonb,uuid) from public,anon;
grant execute on function public.meeting_zoom_link_bind(uuid,bigint,text,timestamptz,text,text,text),
 public.meeting_zoom_evidence(uuid),public.meeting_zoom_map_speakers(uuid,uuid,text,int,int,jsonb,uuid) to authenticated;
revoke all on function meetings.accept_zoom_note(text,text,text,text,text),meetings.zoom_fetch_permit(bigint,uuid),
 meetings.commit_zoom_transcript(bigint,uuid,int,jsonb,jsonb,text) from public,anon,authenticated,demobank_meeting_ingress,demobank_meetings;
grant execute on function meetings.accept_zoom_note(text,text,text,text,text) to demobank_meeting_ingress;
grant execute on function meetings.zoom_fetch_permit(bigint,uuid),meetings.commit_zoom_transcript(bigint,uuid,int,jsonb,jsonb,text) to demobank_meetings;
revoke all on function meetings.accept_zoom_call(text,text,bigint,text,text,timestamptz,timestamptz) from public,anon,authenticated,demobank_meetings;
grant execute on function meetings.accept_zoom_call(text,text,bigint,text,text,timestamptz,timestamptz) to demobank_meeting_ingress;
