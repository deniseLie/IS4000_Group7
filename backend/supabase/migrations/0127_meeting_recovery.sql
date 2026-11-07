-- Reviewed recovery never changes stored evidence, approved minutes or an expired worker claim.
create table meetings.call_time_corrections (
 command_id uuid primary key,meeting_id uuid not null references meetings.workflows(meeting_id),revision int not null,
 previous_start timestamptz,previous_end timestamptz,started_at timestamptz not null,ended_at timestamptz not null,
 reason text not null,actor uuid not null,created_at timestamptz not null default clock_timestamp()
);
create table meetings.provider_call_times (
 binding_id uuid primary key references meetings.zoom_bindings(binding_id),started_at timestamptz not null,
 ended_at timestamptz,conflict boolean not null,received_at timestamptz not null default clock_timestamp()
);
alter table meetings.call_time_corrections enable row level security;
alter table meetings.provider_call_times enable row level security;
revoke all on meetings.call_time_corrections,meetings.provider_call_times from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function meetings.call_time_change_allowed(p_meeting uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
begin
 if exists(select 1 from meetings.sources s join meetings.workflows w using(meeting_id)
  where s.meeting_id=p_meeting and s.revision=w.revision)
 then raise exception 'call times cannot change after evidence was saved; preserve it and submit a new reviewed source instead' using errcode='P1001';end if;
 if exists(select 1 from meetings.events where meeting_id=p_meeting and status='processing' and available_at>clock_timestamp())
 then raise exception 'wait for the current worker claim to finish or expire before correcting call times' using errcode='P1001';end if;
end $$;

create function meetings.save_call_time_correction(p_meeting uuid,p_revision int,p_start timestamptz,p_end timestamptz,p_reason text,p_command uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;occurrence uuid;
begin
 w:=meetings.require_rm(p_meeting);perform meetings.call_time_change_allowed(p_meeting);
 if p_reason is null or length(btrim(p_reason)) not between 1 and 300 then raise exception 'explain why the recorded call times need correction' using errcode='22023';end if;
 perform conversation.require_redacted(p_reason);
 if p_revision is distinct from w.revision or p_start is null or p_end is null or p_end<=p_start or p_end>clock_timestamp()
 then raise exception 'corrected call times must use the current revision and an ordered completed call in the past' using errcode='P1001';end if;
 occurrence:=coalesce(w.occurrence_id,gen_random_uuid());
 insert into meetings.call_time_corrections values(p_command,p_meeting,w.revision,w.started_at,w.ended_at,p_start,p_end,p_reason,auth.uid(),clock_timestamp());
 update meetings.workflows set started_at=p_start,ended_at=p_end,state='ended',occurrence_id=occurrence where meeting_id=p_meeting;
 perform meetings.enqueue_v2('meeting.started',p_meeting,jsonb_build_object('revision',w.revision,'occurrence_id',occurrence),'recovery_start:'||p_command);
 perform meetings.enqueue_v2('meeting.ended',p_meeting,jsonb_build_object('revision',w.revision,'occurrence_id',occurrence),'recovery_end:'||p_command);
 perform audit.record('action','action.meeting_times_corrected','success','rm.appointments',p_meeting::text,
  jsonb_build_object('revision',w.revision,'command_id',p_command),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting));
 return jsonb_build_object('started_at',p_start,'ended_at',p_end,'revision',w.revision);
end $$;

create function public.meeting_correct_call_times(p_meeting_id uuid,p_revision int,p_expected_start timestamptz,p_expected_end timestamptz,
 p_started_at timestamptz,p_ended_at timestamptz,p_reason text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;receipt jsonb;params jsonb:=jsonb_build_array(p_revision,p_expected_start,p_expected_end,p_started_at,p_ended_at,p_reason);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'correct_call_times',params);if receipt is not null then return receipt;end if;
 if w.state<>'ended' or w.started_at is distinct from p_expected_start or w.ended_at is distinct from p_expected_end
 then raise exception 'the recorded call times changed; refresh before reviewing the correction' using errcode='P1001';end if;
 if exists(select 1 from meetings.provider_call_times t join meetings.zoom_bindings b using(binding_id) where b.meeting_id=p_meeting_id and b.revision=w.revision)
 then raise exception 'verified Zoom times are available; review those provider times instead of entering replacements' using errcode='P1001';end if;
 if p_started_at<clock_timestamp()-interval '30 days' then raise exception 'manual time correction is limited to calls in the last thirty days' using errcode='22023';end if;
 receipt:=meetings.save_call_time_correction(p_meeting_id,p_revision,p_started_at,p_ended_at,p_reason,p_command_id);
 return meetings.remember(p_command_id,p_meeting_id,'correct_call_times',params,receipt);
end $$;

create function public.meeting_reconcile_zoom_times(p_meeting_id uuid,p_revision int,p_expected_start timestamptz,p_expected_end timestamptz,
 p_reason text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;t meetings.provider_call_times;receipt jsonb;params jsonb:=jsonb_build_array(p_revision,p_expected_start,p_expected_end,p_reason);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'reconcile_zoom_times',params);if receipt is not null then return receipt;end if;
 if w.started_at is distinct from p_expected_start or w.ended_at is distinct from p_expected_end then
 raise exception 'recorded times changed; refresh before reconciling the provider evidence' using errcode='P1001';end if;
 select x.* into t from meetings.provider_call_times x join meetings.zoom_bindings b using(binding_id) where b.meeting_id=p_meeting_id and b.revision=w.revision;
 if not found or t.ended_at is null then raise exception 'a verified completed Zoom occurrence is required for reconciliation' using errcode='P1001';end if;
 receipt:=meetings.save_call_time_correction(p_meeting_id,p_revision,t.started_at,t.ended_at,p_reason,p_command_id);
 update meetings.provider_call_times set conflict=false where binding_id=t.binding_id;
 return meetings.remember(p_command_id,p_meeting_id,'reconcile_zoom_times',params,receipt);
end $$;

create or replace function meetings.accept_zoom_call(p_account text,p_host text,p_provider bigint,p_uuid text,p_type text,p_start timestamptz,p_end timestamptz) returns uuid
language plpgsql volatile security definer set search_path='' as $$
declare b meetings.zoom_bindings;w meetings.workflows;t meetings.provider_call_times;occurrence uuid;conflicting boolean;
begin
 select x.* into b from meetings.zoom_bindings x join meetings.workflows wf using(meeting_id)
 where x.account_id=p_account and x.host_id=p_host and x.provider_id=p_provider and x.original_uuid=p_uuid and x.revision=wf.revision;
 if not found then raise exception 'Zoom call account, host and original occurrence are not registered' using errcode='42501';end if;
 if p_type is null or p_type not in ('meeting.started','meeting.ended') or p_start is null or p_start>clock_timestamp()+interval '30 seconds'
  or (p_type='meeting.ended' and (p_end is null or p_end<=p_start or p_end>clock_timestamp()+interval '30 seconds'))
 then raise exception 'invalid Zoom call event or time order' using errcode='22023';end if;
 w:=meetings.require_current(b.meeting_id);
 if b.schedule_hash<>w.schedule_hash then raise exception 'Zoom call schedule is stale' using errcode='P1001';end if;
 select * into t from meetings.provider_call_times where binding_id=b.binding_id;
 if found and (t.started_at is distinct from p_start or (p_type='meeting.ended' and t.ended_at is not null and t.ended_at is distinct from p_end))
 then raise exception 'the provider supplied different times for the same original occurrence' using errcode='23505';end if;
 conflicting:=(w.started_at is not null and w.started_at is distinct from p_start)
  or (p_type='meeting.ended' and w.ended_at is not null and w.ended_at is distinct from p_end);
 insert into meetings.provider_call_times values(b.binding_id,p_start,case when p_type='meeting.ended' then p_end else null end,conflicting,clock_timestamp())
 on conflict(binding_id) do update set ended_at=coalesce(excluded.ended_at,meetings.provider_call_times.ended_at),conflict=excluded.conflict;
 if conflicting then
  perform audit.record('action','action.meeting_provider_times_conflict','denied','rm.appointments',b.meeting_id::text,
   jsonb_build_object('revision',w.revision),p_actor_type=>'system',p_actor_id=>'meetings',
   p_customer_id=>(select customer_id from rm.appointments where appointment_id=b.meeting_id));
  return coalesce(w.occurrence_id,b.binding_id);
 end if;
 occurrence:=coalesce(w.occurrence_id,gen_random_uuid());
 update meetings.workflows set started_at=p_start,occurrence_id=occurrence,
  ended_at=case when p_type='meeting.ended' then p_end else ended_at end,
  state=case when p_type='meeting.ended' or w.state='ended' then 'ended' else 'in_progress' end where meeting_id=b.meeting_id;
 perform meetings.enqueue_v2('meeting.started',b.meeting_id,jsonb_build_object('revision',w.revision,'occurrence_id',occurrence),'zoom_start:'||b.binding_id);
 if p_type='meeting.ended' then perform meetings.enqueue_v2('meeting.ended',b.meeting_id,jsonb_build_object('revision',w.revision,'occurrence_id',occurrence),'zoom_end:'||b.binding_id);end if;
 return occurrence;
end $$;

create function meetings.retry_problem(e meetings.events,w meetings.workflows) returns text
language plpgsql volatile security definer set search_path='' as $$
begin
 if e.meeting_id is distinct from w.meeting_id or (e.payload->>'revision')::int is distinct from w.revision then return 'meeting_revision_changed';end if;
 if e.status<>'failed' then return 'job_not_failed';end if;
 if e.event_type not in ('transcript.fetch','transcript.ready') then return 'job_type_not_retryable';end if;
 if w.state<>'ended' then return 'call_not_ended';end if;
 if not meetings.recording_allowed(w.meeting_id) then return 'transcript_permission_unavailable';end if;
 if e.last_error_code in ('zoom_permission_changed','source_authorization_changed','zoom_owner_or_note_mismatch','meeting_cancelled') then return 'authorization_refusal_requires_new_source';end if;
 if e.last_error_code='zoom_evidence_conflict' and not exists(select 1 from meetings.call_time_corrections where meeting_id=w.meeting_id and created_at>e.processed_at)
 then return 'review_and_correct_call_times_first';end if;
 if exists(select 1 from meetings.events where meeting_id=w.meeting_id and status='processing' and available_at>clock_timestamp()) then return 'worker_claim_active';end if;
 if e.event_type='transcript.ready' and not exists(select 1 from meetings.sources where source_id=(e.payload->>'source_id')::uuid
  and source_id=w.current_source and meeting_id=w.meeting_id and revision=w.revision and consent_epoch=w.consent_epoch
  and kind='transcript' and revoked_at is null and purged_at is null) then return 'source_changed';end if;
 return null;
end $$;

create function public.meeting_retry_job(p_meeting_id uuid,p_event_id bigint,p_revision int,p_reason text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;e meetings.events;problem text;receipt jsonb;params jsonb:=jsonb_build_array(p_event_id,p_revision,p_reason);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'retry_meeting_job',params);if receipt is not null then return receipt;end if;
 if p_revision is distinct from w.revision then raise exception 'meeting revision changed; refresh before retrying' using errcode='P1001';end if;
 if p_reason is null or length(btrim(p_reason)) not between 1 and 300 then raise exception 'explain the correction or resolved provider issue before retrying' using errcode='22023';end if;
 perform conversation.require_redacted(p_reason);
 select * into e from meetings.events where event_id=p_event_id and meeting_id=p_meeting_id for update;
 if not found then raise exception 'failed job does not belong to this meeting' using errcode='42501';end if;
 problem:=meetings.retry_problem(e,w);if problem is not null then raise exception 'meeting job retry refused: %',problem using errcode='P1001';end if;
 update meetings.events set status='pending',attempts=0,claim_token=null,claimed_at=null,processed_at=null,last_error_code=null,available_at=clock_timestamp() where event_id=e.event_id;
 perform audit.record('action','action.meeting_job_retry_reviewed','success','meetings.events',e.event_id::text,
  jsonb_build_object('revision',w.revision,'command_id',p_command_id),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>e.customer_id);
 return meetings.remember(p_command_id,p_meeting_id,'retry_meeting_job',params,jsonb_build_object('event_id',e.event_id,'status','pending'));
end $$;

create function public.meeting_recovery(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;t meetings.provider_call_times;
begin
 w:=meetings.require_rm(p_meeting_id);
 select x.* into t from meetings.provider_call_times x join meetings.zoom_bindings b using(binding_id) where b.meeting_id=p_meeting_id and b.revision=w.revision;
 return jsonb_build_object('revision',w.revision,'started_at',w.started_at,'ended_at',w.ended_at,
  'evidence_saved',exists(select 1 from meetings.sources where meeting_id=p_meeting_id and revision=w.revision),
  'provider_times',case when t.binding_id is null then null else jsonb_build_object('started_at',t.started_at,'ended_at',t.ended_at,'conflict',t.conflict) end,
  'failed_jobs',(select coalesce(jsonb_agg(jsonb_build_object('event_id',e.event_id,'type',e.event_type,'error_code',e.last_error_code,
    'retry_problem',meetings.retry_problem(e,w)) order by e.event_id),'[]') from meetings.events e where e.meeting_id=p_meeting_id and e.status='failed'));
end $$;

revoke all on function meetings.call_time_change_allowed(uuid),meetings.save_call_time_correction(uuid,int,timestamptz,timestamptz,text,uuid),
 meetings.retry_problem(meetings.events,meetings.workflows) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
revoke all on function public.meeting_correct_call_times(uuid,int,timestamptz,timestamptz,timestamptz,timestamptz,text,uuid),
 public.meeting_reconcile_zoom_times(uuid,int,timestamptz,timestamptz,text,uuid),public.meeting_retry_job(uuid,bigint,int,text,uuid),public.meeting_recovery(uuid) from public,anon;
grant execute on function public.meeting_correct_call_times(uuid,int,timestamptz,timestamptz,timestamptz,timestamptz,text,uuid),
 public.meeting_reconcile_zoom_times(uuid,int,timestamptz,timestamptz,text,uuid),public.meeting_retry_job(uuid,bigint,int,text,uuid),public.meeting_recovery(uuid) to authenticated;
