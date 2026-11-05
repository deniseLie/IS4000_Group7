-- Meeting-side review and approval state. Requires migrations 0105 and 0120.
-- New public functions run under verified JWT claims; workers never receive banking readers.
create role demobank_meeting_ingress nologin;
grant usage on schema meetings to demobank_meeting_ingress;

create table meetings.notice_versions (
  notice_id uuid primary key, version text not null unique, body text not null,
  maximum_days int not null check(maximum_days between 1 and 365), active boolean not null
);
create table meetings.workflows (
  meeting_id uuid primary key references rm.appointments(appointment_id),
  revision int not null check(revision>0), consent_epoch int not null check(consent_epoch>0),
  schedule_hash text not null, notice_id uuid not null references meetings.notice_versions(notice_id),
  initiator text not null check(initiator in ('client','rm')), purpose text not null check(length(btrim(purpose)) between 1 and 200),
  outcome text not null check(length(btrim(outcome)) between 1 and 300),
  state text not null check(state in ('scheduled','in_progress','ended','cancelled')),
  occurrence_id uuid, current_source uuid, current_draft uuid, started_at timestamptz, ended_at timestamptz
);
create table meetings.recording_grants (
  grant_id uuid primary key, meeting_id uuid not null references meetings.workflows(meeting_id),
  participant uuid not null references auth.users(id), notice_id uuid not null references meetings.notice_versions(notice_id),
  revision int not null, expires_at timestamptz not null, revoked_at timestamptz,
  created_at timestamptz not null default clock_timestamp()
);
create table meetings.sources (
  source_id uuid primary key, meeting_id uuid not null references meetings.workflows(meeting_id),
  kind text not null check(kind in ('transcript','rm_notes')), origin text not null check(origin in ('uploaded_synthetic_transcript','rm_typed_notes')),
  revision int not null, consent_epoch int not null, source_hash text not null,
  cues jsonb not null, occurrence_id uuid not null, created_at timestamptz not null default clock_timestamp()
);
create table meetings.drafts (
  draft_id uuid primary key, meeting_id uuid not null references meetings.workflows(meeting_id),
  version int not null, revision int not null, consent_epoch int not null,
  source_id uuid not null references meetings.sources(source_id), source_hash text not null,
  content jsonb not null, content_hash text not null, note_id uuid references rm.meeting_notes(note_id),
  job_id bigint unique references meetings.events(event_id), created_at timestamptz not null default clock_timestamp(),
  unique(meeting_id,version)
);
create table meetings.commands (
  command_id uuid primary key, meeting_id uuid not null references meetings.workflows(meeting_id),
  operation text not null, actor uuid references auth.users(id), fingerprint text not null, receipt jsonb not null,
  created_at timestamptz not null default clock_timestamp()
);
create table meetings.followups (
  followup_id uuid primary key, meeting_id uuid not null references meetings.workflows(meeting_id),
  draft_id uuid not null references meetings.drafts(draft_id), source_id uuid not null references meetings.sources(source_id),
  item_key text not null, action jsonb not null, owner_role text not null check(owner_role in ('rm','client')),
  task_id uuid references rm.tasks(task_id), status text not null check(status in ('open','done')),
  completion_evidence text, completed_at timestamptz, unique(meeting_id,item_key)
);
create table meetings.effects (
  draft_id uuid not null references meetings.drafts(draft_id), scope text not null, target_key text not null,
  receipt jsonb not null, primary key(draft_id,scope,target_key)
);
do $$ declare name text; begin
  foreach name in array array['notice_versions','workflows','recording_grants','sources','drafts','commands','followups','effects'] loop
    execute format('alter table meetings.%I enable row level security',name);
    execute format('revoke all on meetings.%I from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress',name);
  end loop;
end $$;

create function meetings.hash(p_value jsonb) returns text language sql immutable set search_path='' as
$$ select encode(sha256(convert_to(p_value::text,'UTF8')),'hex') $$;

create function meetings.caller_actor() returns text language sql stable set search_path='' as
$$ select case when auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then 'staff' else 'customer' end $$;

create function meetings.schedule_hash(p_meeting uuid) returns text language sql stable security definer set search_path='' as $$
 select meetings.hash(jsonb_build_array(appointment_id,customer_id,advisor_id,extract(epoch from starts_at),extract(epoch from ends_at),status))
 from rm.appointments where appointment_id=p_meeting
$$;

create function meetings.require_current(p_meeting uuid) returns meetings.workflows
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; a rm.appointments;
begin
  select * into a from rm.appointments where appointment_id=p_meeting for update;
  perform 1 from rm.assignments where advisor_id=a.advisor_id and customer_id=a.customer_id and ended_on is null for share;
  select * into w from meetings.workflows where meeting_id=p_meeting for update;
  if not found then raise exception 'meeting workflow not found' using errcode='P0002'; end if;
  if a.status<>'confirmed' or w.state='cancelled' or w.schedule_hash is distinct from meetings.schedule_hash(p_meeting)
      or not rm.in_book(a.advisor_id,a.customer_id) then
    raise exception 'meeting changed, cancelled or reassigned; refresh before continuing' using errcode='P1001';
  end if;
  return w;
end $$;

create function meetings.require_rm(p_meeting uuid) returns meetings.workflows
language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor(); w meetings.workflows;
begin
  perform 1 from rm.appointments where appointment_id=p_meeting and advisor_id=advisor and rm.in_book(advisor,customer_id) for update;
  if not found then
    raise exception 'this meeting is not owned by your current RM book' using errcode='42501';
  end if;
  w:=meetings.require_current(p_meeting);
  return w;
end $$;

create function meetings.participants(p_meeting uuid) returns uuid[] language sql stable security definer set search_path='' as $$
  select array_agg(id order by id) from (
    select c.auth_user_id id from rm.appointments a join banking.customer_profiles c using(customer_id) where appointment_id=p_meeting
    union select s.auth_user_id from rm.appointments a join rm.staff_logins s using(advisor_id) where appointment_id=p_meeting
  ) people
$$;

create function meetings.require_participant(p_meeting uuid) returns meetings.workflows
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; a rm.appointments;
begin
  if auth.uid() is null or auth.jwt()->>'role' is distinct from 'authenticated' then raise exception 'sign in to this meeting' using errcode='42501'; end if;
  select * into a from rm.appointments where appointment_id=p_meeting;
  if not found then raise exception 'meeting is not available to this login' using errcode='42501'; end if;
  if banking.my_customer_id()=a.customer_id then return meetings.require_current(p_meeting); end if;
  return meetings.require_rm(p_meeting);
end $$;

create function meetings.recording_allowed(p_meeting uuid) returns boolean language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; person uuid; people uuid[];
begin
  w:=meetings.require_current(p_meeting);
  people:=meetings.participants(p_meeting);
  -- This slice supports exactly the authenticated client and assigned RM. Unknown/unlinked attendees fail closed.
  if cardinality(people)<>2 or array_position(people,null) is not null then return false; end if;
  foreach person in array people loop
    if not exists(select 1 from meetings.recording_grants g join meetings.notice_versions n using(notice_id)
      where g.meeting_id=p_meeting and g.participant=person and g.revision=w.revision and g.notice_id=w.notice_id and n.active
        and g.revoked_at is null and g.expires_at>clock_timestamp()) then return false; end if;
  end loop;
  return true;
end $$;

create function meetings.replay(p_command uuid,p_meeting uuid,p_operation text,p_params jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare c meetings.commands;
begin
  if p_command is null then raise exception 'command_id is required' using errcode='22023'; end if;
  select * into c from meetings.commands where command_id=p_command;
  if not found then return null; end if;
  if c.meeting_id is distinct from p_meeting or c.operation is distinct from p_operation or c.actor is distinct from auth.uid()
      or c.fingerprint is distinct from meetings.hash(p_params) then
    raise exception 'command_id was already used with different content, scope or actor' using errcode='23505';
  end if;
  return c.receipt;
end $$;

create function meetings.remember(p_command uuid,p_meeting uuid,p_operation text,p_params jsonb,p_receipt jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
  insert into meetings.commands(command_id,meeting_id,operation,actor,fingerprint,receipt)
    values(p_command,p_meeting,p_operation,auth.uid(),meetings.hash(p_params),p_receipt);
  return p_receipt;
end $$;

create function public.meeting_start_workflow(p_meeting_id uuid,p_notice_id uuid,p_purpose text,p_outcome text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments; role_name text; receipt jsonb; params jsonb:=jsonb_build_array(p_notice_id,p_purpose,p_outcome);
begin
  select * into a from rm.appointments where appointment_id=p_meeting_id for update;
  if not found then raise exception 'confirmed appointment not found' using errcode='P0002'; end if;
  if a.status<>'confirmed' or not rm.in_book(a.advisor_id,a.customer_id) then raise exception 'appointment must be confirmed in the current RM book' using errcode='P1001'; end if;
  if banking.my_customer_id()=a.customer_id then role_name:='client';
  elsif rm.current_advisor()=a.advisor_id then role_name:='rm';
  else raise exception 'this appointment belongs to another participant' using errcode='42501'; end if;
  if p_notice_id is null or not exists(select 1 from meetings.notice_versions where notice_id=p_notice_id and active) then raise exception 'choose a current recording notice' using errcode='22023'; end if;
  perform rm.require_text(p_purpose,200,'meeting purpose'); perform rm.require_text(p_outcome,300,'desired outcome');
  if exists(select 1 from meetings.workflows where meeting_id=p_meeting_id) then
    receipt:=meetings.replay(p_command_id,p_meeting_id,'start',params);
    if receipt is not null then return receipt; end if;
    raise exception 'workflow already exists; read its current state' using errcode='23505';
  end if;
  insert into meetings.workflows(meeting_id,revision,consent_epoch,schedule_hash,notice_id,initiator,purpose,outcome,state)
    values(p_meeting_id,1,1,meetings.schedule_hash(p_meeting_id),p_notice_id,role_name,p_purpose,p_outcome,'scheduled');
  perform meetings.enqueue_v2('meeting.scheduled',p_meeting_id,jsonb_build_object('revision',1),'command:'||p_command_id);
  receipt:=jsonb_build_object('meeting_id',p_meeting_id,'revision',1,'initiator',role_name);
  perform audit.record('action','action.meeting_started','success','rm.appointments',p_meeting_id::text,
    jsonb_build_object('revision',1),p_actor_type=>meetings.caller_actor(),p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return meetings.remember(p_command_id,p_meeting_id,'start',params,receipt);
end $$;

create function public.meeting_grant_recording(p_meeting_id uuid,p_notice_id uuid,p_revision int,p_expires_at timestamptz,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; receipt jsonb; id uuid:=gen_random_uuid(); days int;
  params jsonb:=jsonb_build_array(p_notice_id,p_revision,extract(epoch from p_expires_at));
begin
  w:=meetings.require_participant(p_meeting_id);
  select maximum_days into days from meetings.notice_versions where notice_id=p_notice_id and active;
  if p_notice_id is distinct from w.notice_id or p_revision is distinct from w.revision or days is null
      or p_expires_at is null or p_expires_at<=clock_timestamp() or p_expires_at>clock_timestamp()+make_interval(days=>days) then
    raise exception 'recording grant must match the current meeting and notice with a valid explicit expiry' using errcode='22023';
  end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'grant_recording',params); if receipt is not null then return receipt; end if;
  update meetings.recording_grants set revoked_at=clock_timestamp() where meeting_id=p_meeting_id and participant=auth.uid() and revoked_at is null;
  insert into meetings.recording_grants(grant_id,meeting_id,participant,notice_id,revision,expires_at)
    values(id,p_meeting_id,auth.uid(),p_notice_id,p_revision,p_expires_at);
  update meetings.workflows set consent_epoch=consent_epoch+1 where meeting_id=p_meeting_id returning * into w;
  perform audit.record('consent','consent.meeting_granted','success','meetings.recording_grants',id::text,
    jsonb_build_object('meeting_id',p_meeting_id,'notice_id',p_notice_id,'revision',w.revision),
    p_actor_type=>meetings.caller_actor(),p_actor_id=>auth.uid()::text,
    p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
  return meetings.remember(p_command_id,p_meeting_id,'grant_recording',params,jsonb_build_object('grant_id',id,'consent_epoch',w.consent_epoch));
end $$;

create function public.meeting_revoke_recording(p_meeting_id uuid,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; receipt jsonb;
begin
  perform 1 from rm.appointments where appointment_id=p_meeting_id for update;
  select * into w from meetings.workflows where meeting_id=p_meeting_id for update;
  if not found then raise exception 'meeting workflow not found' using errcode='P0002'; end if;
  if auth.uid() is null or auth.jwt()->>'role' is distinct from 'authenticated' or
      (not coalesce(auth.uid()=any(meetings.participants(p_meeting_id)),false) and
       not exists(select 1 from meetings.recording_grants where meeting_id=p_meeting_id and participant=auth.uid())) then
    raise exception 'only a meeting participant may revoke their own recording permission' using errcode='42501';
  end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'revoke_recording','{}'); if receipt is not null then return receipt; end if;
  update meetings.recording_grants set revoked_at=clock_timestamp() where meeting_id=p_meeting_id and participant=auth.uid() and revoked_at is null;
  update meetings.workflows set consent_epoch=consent_epoch+1 where meeting_id=p_meeting_id returning * into w;
  perform audit.record('consent','consent.meeting_revoked','success','rm.appointments',p_meeting_id::text,jsonb_build_object('consent_epoch',w.consent_epoch),
    p_actor_type=>meetings.caller_actor(),p_actor_id=>auth.uid()::text,
    p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
  return meetings.remember(p_command_id,p_meeting_id,'revoke_recording','{}',jsonb_build_object('consent_epoch',w.consent_epoch));
end $$;

create function public.meeting_document_call_start(p_meeting_id uuid,p_revision int,p_at timestamptz,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; id uuid:=gen_random_uuid(); receipt jsonb; params jsonb:=jsonb_build_array(p_revision,extract(epoch from p_at));
begin
  w:=meetings.require_rm(p_meeting_id);
  receipt:=meetings.replay(p_command_id,p_meeting_id,'document_start',params); if receipt is not null then return receipt; end if;
  if p_revision is distinct from w.revision or w.state<>'scheduled' or p_at is null or p_at>clock_timestamp() or p_at<clock_timestamp()-interval '1 day' then
    raise exception 'documented call start requires the current scheduled meeting and a recent past timestamp' using errcode='P1001'; end if;
  update meetings.workflows set state='in_progress',occurrence_id=id,started_at=p_at where meeting_id=p_meeting_id;
  perform meetings.enqueue_v2('meeting.started',p_meeting_id,jsonb_build_object('revision',w.revision,'occurrence_id',id),'command:'||p_command_id);
  return meetings.remember(p_command_id,p_meeting_id,'document_start',params,jsonb_build_object('occurrence_id',id,'state','in_progress'));
end $$;

create function public.meeting_document_call_end(p_meeting_id uuid,p_revision int,p_at timestamptz,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; receipt jsonb; params jsonb:=jsonb_build_array(p_revision,extract(epoch from p_at));
begin
  w:=meetings.require_rm(p_meeting_id);
  receipt:=meetings.replay(p_command_id,p_meeting_id,'document_end',params); if receipt is not null then return receipt; end if;
  if p_revision is distinct from w.revision or w.state<>'in_progress' or p_at is null or p_at<w.started_at or p_at>clock_timestamp() then
    raise exception 'documented call end must follow the current occurrence start and cannot be in the future' using errcode='P1001'; end if;
  update meetings.workflows set state='ended',ended_at=p_at where meeting_id=p_meeting_id;
  perform meetings.enqueue_v2('meeting.ended',p_meeting_id,jsonb_build_object('revision',w.revision,'occurrence_id',w.occurrence_id),'command:'||p_command_id);
  return meetings.remember(p_command_id,p_meeting_id,'document_end',params,jsonb_build_object('occurrence_id',w.occurrence_id,'state','ended'));
end $$;

create function meetings.validate_cues(p_cues jsonb,p_kind text) returns void language plpgsql set search_path='' as $$
declare cue jsonb; ids text[]:='{}';
begin
  if p_cues is null or jsonb_typeof(p_cues)<>'array' or jsonb_array_length(p_cues) not between 1 and 100 or octet_length(p_cues::text)>16000 then raise exception 'source requires 1-100 bounded redacted cues' using errcode='22023'; end if;
  for cue in select * from jsonb_array_elements(p_cues) loop
    if (select array_agg(key order by key) from jsonb_object_keys(cue) key) is distinct from array['cue_id','speaker','start_seconds','text']
        or jsonb_typeof(cue->'cue_id') is distinct from 'string' or cue->>'cue_id' !~ '^[a-z][a-z0-9_-]{0,39}$' or cue->>'cue_id'=any(ids)
        or jsonb_typeof(cue->'text') is distinct from 'string' or length(btrim(cue->>'text')) not between 1 and 2000
        or jsonb_typeof(cue->'speaker') is distinct from 'string' or cue->>'speaker' not in ('rm','client','unmapped') then raise exception 'invalid source cue identity, text or speaker' using errcode='22023'; end if;
    if p_kind='transcript' and (jsonb_typeof(cue->'start_seconds') is distinct from 'number' or cue->>'start_seconds' !~ '^[0-9]{1,7}$') then raise exception 'transcript cues require a nonnegative integer position' using errcode='22023'; end if;
    if p_kind='rm_notes' and cue->'start_seconds'<>'null'::jsonb then raise exception 'typed notes must not claim transcript timestamps' using errcode='22023'; end if;
    perform conversation.require_redacted(cue->>'text'); ids:=array_append(ids,cue->>'cue_id');
  end loop;
end $$;

create function public.meeting_submit_notes(p_meeting_id uuid,p_revision int,p_cues jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; id uuid:=gen_random_uuid(); hash text:=meetings.hash(p_cues); receipt jsonb;
  params jsonb:=jsonb_build_array(p_revision,hash);
begin
  w:=meetings.require_rm(p_meeting_id);
  if w.state<>'ended' or p_revision is distinct from w.revision then raise exception 'enter notes for the current completed call occurrence' using errcode='P1001'; end if;
  perform meetings.validate_cues(p_cues,'rm_notes');
  receipt:=meetings.replay(p_command_id,p_meeting_id,'submit_notes',params); if receipt is not null then return receipt; end if;
  insert into meetings.sources(source_id,meeting_id,kind,origin,revision,consent_epoch,source_hash,cues,occurrence_id)
    values(id,p_meeting_id,'rm_notes','rm_typed_notes',w.revision,w.consent_epoch,hash,p_cues,w.occurrence_id);
  update meetings.workflows set current_source=id,current_draft=null where meeting_id=p_meeting_id;
  perform audit.record('document','document.meeting_notes_stored','success','meetings.sources',id::text,
    jsonb_build_object('meeting_id',p_meeting_id,'cue_count',jsonb_array_length(p_cues)),
    p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
    p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
  return meetings.remember(p_command_id,p_meeting_id,'submit_notes',params,jsonb_build_object('source_id',id,'source_hash',hash));
end $$;

-- Call before an external fetch, then require the same permission epoch when registering its result.
-- External retrieval must use this permit before fetching and recheck it before storage.
create function meetings.transcript_ingress_permit(p_meeting_id uuid,p_revision int,p_people uuid[]) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; people uuid[];
begin
  w:=meetings.require_current(p_meeting_id);
  select array_agg(id order by id) into people from unnest(p_people) id;
  if p_revision is distinct from w.revision or w.state<>'ended' or people is distinct from meetings.participants(p_meeting_id)
      or not meetings.recording_allowed(p_meeting_id) then raise exception 'recording permission, notice, occurrence or participant roster is not current' using errcode='42501'; end if;
  return jsonb_build_object('meeting_id',p_meeting_id,'revision',w.revision,'consent_epoch',w.consent_epoch,'occurrence_id',w.occurrence_id);
end $$;

create function meetings.register_transcript(p_meeting_id uuid,p_revision int,p_consent_epoch int,p_source_id uuid,p_people uuid[],p_cues jsonb,p_dedupe_key text) returns bigint
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; people uuid[]; s meetings.sources;
begin
  w:=meetings.require_current(p_meeting_id);
  select array_agg(id order by id) into people from unnest(p_people) id;
  if p_revision is distinct from w.revision or p_consent_epoch is distinct from w.consent_epoch or w.state<>'ended' or people is distinct from meetings.participants(p_meeting_id)
      or not meetings.recording_allowed(p_meeting_id) then raise exception 'recording permission, notice, occurrence or participant roster is not current' using errcode='42501'; end if;
  perform meetings.validate_cues(p_cues,'transcript');
  if exists(select 1 from jsonb_array_elements(p_cues) cue where (cue->>'start_seconds')::int>extract(epoch from w.ended_at-w.started_at)) then
    raise exception 'transcript position exceeds the documented occurrence duration' using errcode='22023';
  end if;
  select * into s from meetings.sources where source_id=p_source_id;
  if found then
    if s.meeting_id is distinct from p_meeting_id or s.source_hash is distinct from meetings.hash(p_cues) or s.kind<>'transcript'
        or s.revision<>w.revision or s.consent_epoch<>w.consent_epoch then raise exception 'source_id already belongs to different evidence or permission version' using errcode='23505'; end if;
  else
    insert into meetings.sources(source_id,meeting_id,kind,origin,revision,consent_epoch,source_hash,cues,occurrence_id)
      values(p_source_id,p_meeting_id,'transcript','uploaded_synthetic_transcript',w.revision,w.consent_epoch,meetings.hash(p_cues),p_cues,w.occurrence_id);
    update meetings.workflows set current_source=p_source_id,current_draft=null where meeting_id=p_meeting_id;
    perform audit.record('document','document.meeting_transcript_registered','success','meetings.sources',p_source_id::text,
      jsonb_build_object('meeting_id',p_meeting_id,'cue_count',jsonb_array_length(p_cues)),p_actor_type=>'system',p_actor_id=>'meeting_ingress',
      p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
  end if;
  return meetings.enqueue_v2('transcript.ready',p_meeting_id,jsonb_build_object('revision',w.revision,'source_id',p_source_id),p_dedupe_key);
end $$;

create function meetings.require_source(p_meeting uuid,p_source uuid) returns meetings.sources
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; s meetings.sources;
begin
  w:=meetings.require_current(p_meeting);
  select * into s from meetings.sources where source_id=p_source and meeting_id=p_meeting;
  if not found then raise exception 'source does not belong to this meeting' using errcode='42501'; end if;
  if s.source_id is distinct from w.current_source or s.revision<>w.revision or s.occurrence_id is distinct from w.occurrence_id
      or (s.kind='transcript' and (s.consent_epoch<>w.consent_epoch or not meetings.recording_allowed(p_meeting))) then
    raise exception 'source was replaced or its recording permission changed' using errcode='P1001';
  end if;
  return s;
end $$;

create function public.meeting_notes_context(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; s meetings.sources;
begin
  w:=meetings.require_rm(p_meeting_id); s:=meetings.require_source(p_meeting_id,w.current_source);
  if s.kind<>'rm_notes' then raise exception 'use the restricted transcript worker for this source' using errcode='42501'; end if;
  return jsonb_build_object('meeting_id',p_meeting_id,'revision',w.revision,'consent_epoch',w.consent_epoch,
    'source_id',s.source_id,'source_hash',s.source_hash,'source_kind',s.kind,'cues',s.cues,'purpose',w.purpose,'outcome',w.outcome);
end $$;

create function meetings.job_context(p_event_id bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events; w meetings.workflows; s meetings.sources; draft uuid;
begin
  e:=meetings.require_claim(p_event_id,p_token); w:=meetings.require_current(e.meeting_id);
  if e.event_type<>'transcript.ready' or (e.payload->>'revision')::int<>w.revision then raise exception 'job is not a current transcript event' using errcode='42501'; end if;
  s:=meetings.require_source(e.meeting_id,(e.payload->>'source_id')::uuid);
  if s.kind<>'transcript' then raise exception 'worker cannot read RM notes or banking records' using errcode='42501'; end if;
  select draft_id into draft from meetings.drafts where job_id=p_event_id;
  return jsonb_build_object('meeting_id',e.meeting_id,'revision',w.revision,'consent_epoch',w.consent_epoch,
    'source_id',s.source_id,'source_hash',s.source_hash,'source_kind',s.kind,'cues',s.cues,'draft_id',draft);
end $$;

create function meetings.validate_quote(p_cues jsonb,p_item jsonb) returns jsonb language plpgsql set search_path='' as $$
declare cue jsonb;
begin
  select value into cue from jsonb_array_elements(p_cues) where value->>'cue_id'=p_item->>'cue_id';
  if cue is null or jsonb_typeof(p_item->'text') is distinct from 'string' or length(btrim(p_item->>'text')) not between 1 and 500
      or jsonb_typeof(p_item->'quote') is distinct from 'string' or length(btrim(p_item->>'quote')) not between 1 and 1000
      or position(p_item->>'quote' in cue->>'text')=0 then raise exception 'item requires an exact quote in its named source cue' using errcode='22023'; end if;
  perform conversation.require_redacted(p_item->>'text');
  perform conversation.require_redacted(p_item->>'quote');
  return cue;
end $$;

create function meetings.validate_content(p_cues jsonb,p_content jsonb) returns void language plpgsql set search_path='' as $$
declare item jsonb; cue jsonb; ids uuid[]:='{}'; id uuid;
begin
  if p_content is null or jsonb_typeof(p_content)<>'object' or octet_length(p_content::text)>24000
      or (select array_agg(key order by key) from jsonb_object_keys(p_content) key) is distinct from array['actions','client_recap','decisions','summary','title'] then raise exception 'minutes content has an invalid schema' using errcode='22023'; end if;
  if jsonb_typeof(p_content->'title') is distinct from 'string' or length(btrim(p_content->>'title')) not between 1 and 160
      or jsonb_typeof(p_content->'client_recap') is distinct from 'string' or length(btrim(p_content->>'client_recap')) not between 1 and 500
      or jsonb_typeof(p_content->'summary') is distinct from 'array' or jsonb_array_length(p_content->'summary') not between 1 and 4
      or jsonb_typeof(p_content->'decisions') is distinct from 'array' or jsonb_array_length(p_content->'decisions')>10
      or jsonb_typeof(p_content->'actions') is distinct from 'array' or jsonb_array_length(p_content->'actions')>10 then raise exception 'minutes fields exceed their bounds or have incorrect types' using errcode='22023'; end if;
  -- Inspect human text fields, not serialized UUIDs or typed metadata.
  perform conversation.require_redacted(p_content->>'title');
  perform conversation.require_redacted(p_content->>'client_recap');
  for item in select value from jsonb_array_elements((p_content->'summary')||(p_content->'decisions')) loop
    if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['cue_id','quote','text'] then raise exception 'factual items need only cue_id, quote and text' using errcode='22023'; end if;
    perform meetings.validate_quote(p_cues,item);
  end loop;
  for item in select value from jsonb_array_elements(p_content->'actions') loop
    if (select array_agg(key order by key) from jsonb_object_keys(item) key) is distinct from array['action_id','commitment','condition','cue_id','due_on','due_quote','owner','quote','text'] then raise exception 'action has an invalid schema' using errcode='22023'; end if;
    cue:=meetings.validate_quote(p_cues,item); id:=(item->>'action_id')::uuid;
    if id is null or id=any(ids) or length(item->>'text')>200 or jsonb_typeof(item->'owner') is distinct from 'string' or item->>'owner' not in ('rm','client','unassigned')
        or jsonb_typeof(item->'commitment') is distinct from 'string' or item->>'commitment' not in ('agreed','conditional','tentative','unclear') then raise exception 'action identity, owner or commitment is invalid' using errcode='22023'; end if;
    ids:=array_append(ids,id);
    if item->>'owner'<>'unassigned' and item->>'owner' is distinct from cue->>'speaker' then raise exception 'action owner must match the quoted speaker or remain unassigned' using errcode='22023'; end if;
    if item->>'commitment'='conditional' then
      if jsonb_typeof(item->'condition') is distinct from 'string' or length(btrim(item->>'condition'))=0 or position(item->>'condition' in cue->>'text')=0 then raise exception 'conditional action needs an exact condition quote' using errcode='22023'; end if;
    elsif item->'condition'<>'null'::jsonb then raise exception 'only conditional actions carry a condition' using errcode='22023'; end if;
    if item->>'commitment'='agreed' and lower(item->>'quote') ~ '\m(if|unless|might|maybe|possibly)\M' then raise exception 'conditional or tentative wording cannot become an unconditional agreed action' using errcode='22023'; end if;
    if item->'due_on'<>'null'::jsonb then
      if jsonb_typeof(item->'due_on') is distinct from 'string' or item->>'due_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
          or jsonb_typeof(item->'due_quote') is distinct from 'string' or length(btrim(item->>'due_quote'))=0
          or position(item->>'due_on' in item->>'due_quote')=0 or position(item->>'due_quote' in cue->>'text')=0 then raise exception 'due date needs exact ISO-date evidence in the same cue' using errcode='22023'; end if;
      perform (item->>'due_on')::date;
    elsif item->'due_quote'<>'null'::jsonb then raise exception 'due_quote requires an explicit due_on date' using errcode='22023'; end if;
  end loop;
end $$;

create function meetings.store_draft(p_meeting uuid,p_source uuid,p_source_hash text,p_revision int,p_epoch int,p_content jsonb,p_job bigint) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; s meetings.sources; id uuid:=gen_random_uuid(); version int; existing meetings.drafts;
begin
  w:=meetings.require_current(p_meeting); s:=meetings.require_source(p_meeting,p_source);
  if p_revision is distinct from w.revision or p_epoch is distinct from w.consent_epoch or p_source_hash is distinct from s.source_hash then raise exception 'draft inputs are stale; refresh and regenerate' using errcode='P1001'; end if;
  perform meetings.validate_content(s.cues,p_content);
  if p_job is not null then
    select * into existing from meetings.drafts where job_id=p_job;
    if found then return jsonb_build_object('draft_id',existing.draft_id,'version',existing.version,'content_hash',existing.content_hash,
      'revision',existing.revision,'consent_epoch',existing.consent_epoch); end if;
  end if;
  select coalesce(max(d.version),0)+1 into version from meetings.drafts d where d.meeting_id=p_meeting;
  insert into meetings.drafts(draft_id,meeting_id,version,revision,consent_epoch,source_id,source_hash,content,content_hash,job_id)
    values(id,p_meeting,version,w.revision,w.consent_epoch,p_source,s.source_hash,p_content,meetings.hash(p_content),p_job);
  update meetings.workflows set current_draft=id where meeting_id=p_meeting;
  perform audit.record('document','document.meeting_draft_saved','success','meetings.drafts',id::text,
    jsonb_build_object('meeting_id',p_meeting,'version',version,'source_id',p_source),
    p_actor_type=>case when auth.uid() is null then 'system' else 'staff' end,
    p_actor_id=>coalesce(auth.uid()::text,'meetings'),p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting));
  return jsonb_build_object('draft_id',id,'version',version,'content_hash',meetings.hash(p_content),'revision',w.revision,'consent_epoch',w.consent_epoch);
end $$;

create function public.meeting_save_notes_draft(p_meeting_id uuid,p_source_id uuid,p_source_hash text,p_revision int,p_consent_epoch int,p_content jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; s meetings.sources; receipt jsonb;
  params jsonb:=jsonb_build_array(p_source_id,p_source_hash,p_revision,p_consent_epoch,meetings.hash(p_content));
begin
  w:=meetings.require_rm(p_meeting_id); s:=meetings.require_source(p_meeting_id,p_source_id);
  if s.kind<>'rm_notes' then raise exception 'transcript drafts are saved only by the restricted worker' using errcode='42501'; end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'save_draft',params); if receipt is not null then return receipt; end if;
  receipt:=meetings.store_draft(p_meeting_id,p_source_id,p_source_hash,p_revision,p_consent_epoch,p_content,null);
  return meetings.remember(p_command_id,p_meeting_id,'save_draft',params,receipt);
end $$;

create function meetings.save_job_draft(p_event_id bigint,p_token uuid,p_source_hash text,p_revision int,p_consent_epoch int,p_content jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare ctx jsonb;
begin
  ctx:=meetings.job_context(p_event_id,p_token);
  return meetings.store_draft((ctx->>'meeting_id')::uuid,(ctx->>'source_id')::uuid,p_source_hash,p_revision,p_consent_epoch,p_content,p_event_id);
end $$;

create function public.meeting_generation_receipt(p_meeting_id uuid,p_source_id uuid,p_source_hash text,p_revision int,p_consent_epoch int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; s meetings.sources; receipt jsonb;
begin
  w:=meetings.require_rm(p_meeting_id); s:=meetings.require_source(p_meeting_id,p_source_id);
  if s.kind<>'rm_notes' or s.source_hash is distinct from p_source_hash or p_revision is distinct from w.revision or p_consent_epoch is distinct from w.consent_epoch then
    raise exception 'generation request does not match current typed notes and permissions' using errcode='P1001'; end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'generate_minutes',jsonb_build_array(p_source_id,p_source_hash,p_revision,p_consent_epoch));
  if receipt is not null and (receipt->>'draft_id')::uuid is distinct from w.current_draft then raise exception 'the generated draft was replaced; refresh before continuing' using errcode='P1001'; end if;
  return receipt;
end $$;

create function public.meeting_save_generated_draft(p_meeting_id uuid,p_source_id uuid,p_source_hash text,p_revision int,p_consent_epoch int,p_content jsonb,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare receipt jsonb;
begin
  receipt:=public.meeting_generation_receipt(p_meeting_id,p_source_id,p_source_hash,p_revision,p_consent_epoch,p_command_id);
  if receipt is not null then return receipt; end if;
  receipt:=meetings.store_draft(p_meeting_id,p_source_id,p_source_hash,p_revision,p_consent_epoch,p_content,null);
  return meetings.remember(p_command_id,p_meeting_id,'generate_minutes',jsonb_build_array(p_source_id,p_source_hash,p_revision,p_consent_epoch),receipt);
end $$;

create function meetings.require_draft(p_meeting uuid,p_draft uuid,p_version int,p_hash text,p_revision int,p_epoch int) returns meetings.drafts
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; d meetings.drafts;
begin
  w:=meetings.require_rm(p_meeting);
  select * into d from meetings.drafts where draft_id=p_draft and meeting_id=p_meeting for update;
  if not found then raise exception 'draft does not belong to this meeting' using errcode='42501'; end if;
  perform meetings.require_source(p_meeting,d.source_id);
  if d.draft_id is distinct from w.current_draft or p_version is distinct from d.version or p_hash is distinct from d.content_hash
      or p_revision is distinct from w.revision or d.revision<>w.revision or p_epoch is distinct from w.consent_epoch or d.consent_epoch<>w.consent_epoch then
    raise exception 'approval references a replaced draft or stale meeting, source or permission version' using errcode='P1001'; end if;
  return d;
end $$;

create function public.meeting_approve_minutes(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts; w meetings.workflows; a rm.appointments; receipt jsonb; summary text; decisions text[]; commitments text[];
  params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
begin
  d:=meetings.require_draft(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
  receipt:=meetings.replay(p_command_id,p_meeting_id,'approve_minutes',params); if receipt is not null then return receipt; end if;
  if d.note_id is not null then receipt:=jsonb_build_object('note_id',d.note_id,'draft_id',d.draft_id);
  else
    select * into w from meetings.workflows where meeting_id=p_meeting_id; select * into a from rm.appointments where appointment_id=p_meeting_id;
    select string_agg(value->>'text',E'\n') into summary from jsonb_array_elements(d.content->'summary');
    select coalesce(array_agg(value->>'text'),'{}') into decisions from jsonb_array_elements(d.content->'decisions');
    select coalesce(array_agg(value->>'text'),'{}') into commitments from jsonb_array_elements(d.content->'actions') where value->>'commitment'='agreed';
    receipt:=public.rm_save_interaction(d.draft_id,a.customer_id,(w.ended_at at time zone 'Asia/Singapore')::date,a.format,d.content->>'title',
      summary,commitments,decisions,'{}',null,jsonb_build_object('source','meeting_lifecycle','version',d.version),'[]');
    if receipt->>'denied'='true' then raise exception 'RM authorization changed before approval' using errcode='42501'; end if;
    update meetings.drafts set note_id=(receipt->>'note_id')::uuid where draft_id=d.draft_id;
    update rm.meeting_notes set appointment_id=p_meeting_id where note_id=(receipt->>'note_id')::uuid;
    receipt:=jsonb_build_object('note_id',receipt->'note_id','draft_id',d.draft_id);
    insert into meetings.effects values(d.draft_id,'minutes','internal',receipt);
  end if;
  return meetings.remember(p_command_id,p_meeting_id,'approve_minutes',params,receipt);
end $$;

create function public.meeting_create_followups(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_action_ids uuid[],p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts; a rm.appointments; action jsonb; id uuid; computed_key text; existing meetings.followups; task jsonb; task_id uuid; result jsonb:='[]'; receipt jsonb;
  params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch,to_jsonb(p_action_ids));
begin
  d:=meetings.require_draft(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
  if d.note_id is null then raise exception 'approve the exact minutes before approving follow-up creation' using errcode='P1001'; end if;
  if p_action_ids is null or cardinality(p_action_ids) not between 1 and 10 or cardinality(p_action_ids)<>(select count(distinct v) from unnest(p_action_ids) v) then raise exception 'select 1-10 distinct agreed action IDs' using errcode='22023'; end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'create_followups',params); if receipt is not null then return receipt; end if;
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  foreach id in array p_action_ids loop
    select value into action from jsonb_array_elements(d.content->'actions') where (value->>'action_id')::uuid=id;
    if action is null or action->>'commitment'<>'agreed' or action->>'owner' not in ('rm','client') then
      raise exception 'only explicitly selected, agreed actions with an identified owner can become follow-ups' using errcode='22023'; end if;
    computed_key:=meetings.hash(jsonb_build_array(d.source_id,action-'action_id'));
    select * into existing from meetings.followups f where f.meeting_id=p_meeting_id and f.item_key=computed_key;
    if found then result:=result||jsonb_build_array(jsonb_build_object('followup_id',existing.followup_id,'status',existing.status)); continue; end if;
    task_id:=null;
    if action->>'owner'='rm' then
      task:=public.rm_create_tasks(a.customer_id,d.note_id,jsonb_build_array(jsonb_build_object('request_key',id,'title',action->>'text','owner','RM',
        'details','Approved meeting follow-up','due_on',action->>'due_on')));
      if jsonb_array_length(task->'created')>0 then task_id:=(task#>>'{created,0,task_id}')::uuid;
      elsif jsonb_array_length(task->'duplicates')>0 then task_id:=(task#>>'{duplicates,0,existing_task_id}')::uuid;
      else raise exception 'approved RM task was not created or linked' using errcode='P1001'; end if;
    end if;
    insert into meetings.followups(followup_id,meeting_id,draft_id,source_id,item_key,action,owner_role,task_id,status)
      values(id,p_meeting_id,d.draft_id,d.source_id,computed_key,action,action->>'owner',task_id,'open');
    perform audit.record('action','action.meeting_followup_created','success','meetings.followups',id::text,
      jsonb_build_object('meeting_id',p_meeting_id,'draft_id',d.draft_id),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
    result:=result||jsonb_build_array(jsonb_build_object('followup_id',id,'status','open'));
  end loop;
  return meetings.remember(p_command_id,p_meeting_id,'create_followups',params,jsonb_build_object('followups',result));
end $$;

create function public.meeting_share_recap(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts; a rm.appointments; receipt jsonb; message uuid:=gen_random_uuid();
  params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
begin
  d:=meetings.require_draft(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
  if d.note_id is null then raise exception 'approve the exact minutes before sharing the recap' using errcode='P1001'; end if;
  receipt:=meetings.replay(p_command_id,p_meeting_id,'share_recap',params); if receipt is not null then return receipt; end if;
  select e.receipt into receipt from meetings.effects e where draft_id=d.draft_id and scope='recap' and target_key='client';
  if receipt is null then
    select * into a from rm.appointments where appointment_id=p_meeting_id;
    receipt:=public.rm_send_client_update(message,a.customer_id,null,d.content->>'client_recap',null,least(d.version,50),false);
    if receipt->>'denied'='true' then raise exception 'RM authorization changed before sharing' using errcode='42501'; end if;
    receipt:=jsonb_build_object('message_id',message,'draft_id',d.draft_id,'shared',true);
    insert into meetings.effects values(d.draft_id,'recap','client',receipt);
  end if;
  return meetings.remember(p_command_id,p_meeting_id,'share_recap',params,receipt);
end $$;

create function public.meeting_complete_followup(p_meeting_id uuid,p_followup_id uuid,p_evidence text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; f meetings.followups; a rm.appointments; receipt jsonb; params jsonb:=jsonb_build_array(p_followup_id,meetings.hash(to_jsonb(p_evidence)));
begin
  w:=meetings.require_participant(p_meeting_id); select * into a from rm.appointments where appointment_id=p_meeting_id;
  select * into f from meetings.followups where followup_id=p_followup_id and meeting_id=p_meeting_id for update;
  if not found then raise exception 'follow-up does not belong to this meeting' using errcode='42501'; end if;
  if f.owner_role='client' then
    if banking.my_customer_id() is distinct from a.customer_id then raise exception 'only the client can complete their own follow-up' using errcode='42501'; end if;
  else perform meetings.require_rm(p_meeting_id); end if;
  perform rm.require_text(p_evidence,500,'completion evidence');
  receipt:=meetings.replay(p_command_id,p_meeting_id,'complete_followup',params); if receipt is not null then return receipt; end if;
  if f.status='done' then raise exception 'follow-up already completed; use its original command receipt' using errcode='23505'; end if;
  if f.task_id is not null then perform public.rm_update_task(f.task_id,'done'); end if;
  update meetings.followups set status='done',completion_evidence=p_evidence,completed_at=clock_timestamp() where followup_id=p_followup_id;
  perform audit.record('action','action.meeting_followup_completed','success','meetings.followups',p_followup_id::text,jsonb_build_object('meeting_id',p_meeting_id),
    p_actor_type=>meetings.caller_actor(),p_actor_id=>auth.uid()::text,p_customer_id=>a.customer_id);
  return meetings.remember(p_command_id,p_meeting_id,'complete_followup',params,jsonb_build_object('followup_id',p_followup_id,'status','done'));
end $$;

create function public.meeting_view(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; a rm.appointments; own_client boolean; d meetings.drafts; result jsonb; source_kind text;
begin
  w:=meetings.require_participant(p_meeting_id); select * into a from rm.appointments where appointment_id=p_meeting_id;
  own_client:=coalesce(banking.my_customer_id()=a.customer_id,false);
  result:=jsonb_build_object('meeting_id',p_meeting_id,'revision',w.revision,'consent_epoch',w.consent_epoch,'state',w.state,
    'purpose',w.purpose,'outcome',w.outcome,'initiator',w.initiator,'notice_id',w.notice_id,'recording_allowed',meetings.recording_allowed(p_meeting_id),
    'open_followups',(select count(*) from meetings.followups where meeting_id=p_meeting_id and status='open' and (not own_client or owner_role='client')),
    'followups',(select coalesce(jsonb_agg(jsonb_build_object('followup_id',f.followup_id,'owner',f.owner_role,'text',f.action->>'text','status',f.status,'due_on',f.action->'due_on') order by f.followup_id),'[]')
      from meetings.followups f where f.meeting_id=p_meeting_id and (not own_client or f.owner_role='client')));
  result:=result||jsonb_build_object('shared_recap',(select m.body from meetings.effects e
    join meetings.drafts x on x.draft_id=e.draft_id join rm.client_messages m on m.message_id=(e.receipt->>'message_id')::uuid
    where x.meeting_id=p_meeting_id and e.scope='recap' and e.target_key='client' order by x.version desc limit 1));
  if not own_client and w.current_draft is not null then
    select * into d from meetings.drafts where draft_id=w.current_draft; select kind into source_kind from meetings.sources where source_id=d.source_id;
    if source_kind<>'transcript' or (d.consent_epoch=w.consent_epoch and meetings.recording_allowed(p_meeting_id)) then
      result:=result||jsonb_build_object('draft',jsonb_build_object('draft_id',d.draft_id,'version',d.version,'revision',d.revision,'consent_epoch',d.consent_epoch,
        'content_hash',d.content_hash,'source_kind',source_kind,'content',d.content,'approved',d.note_id is not null));
    else result:=result||jsonb_build_object('draft_withheld','recording_permission_changed'); end if;
  end if;
  if not own_client then
    result:=result||jsonb_build_object('jobs',(select coalesce(jsonb_agg(jsonb_build_object('event_id',event_id,'type',event_type,
      'status',status,'attempts',attempts,'error_code',last_error_code) order by event_id),'[]') from meetings.events where meeting_id=p_meeting_id));
  end if;
  return result;
end $$;

create function public.meeting_record_refusal(p_operation text,p_code text) returns void
language plpgsql volatile security definer set search_path='' as $$
begin
  if auth.uid() is null or auth.jwt()->>'role' is distinct from 'authenticated' then raise exception 'verified identity is required for an audit refusal' using errcode='42501'; end if;
  if p_operation is null or p_operation !~ '^meeting_[a-z_]{1,50}$' or p_code is null or p_code !~ '^[A-Z0-9]{5}$' then
    raise exception 'refusal audit accepts only an operation code and SQLSTATE' using errcode='22023'; end if;
  perform audit.record('policy','policy.meeting_command_refused','denied',p_details=>jsonb_build_object('operation',p_operation,'reason',p_code),
    p_actor_type=>meetings.caller_actor(),p_actor_id=>auth.uid()::text);
end $$;

-- All new internal functions are owner-only unless explicitly granted below.
revoke all on all functions in schema meetings from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
grant execute on function meetings.enqueue_v2(text,uuid,jsonb,text),meetings.claim_next(),meetings.renew(bigint,uuid),
  meetings.finish(bigint,uuid,boolean,text),meetings.reject(bigint,uuid,text),meetings.job_context(bigint,uuid),meetings.save_job_draft(bigint,uuid,text,int,int,jsonb) to demobank_meetings;
grant execute on function meetings.transcript_ingress_permit(uuid,int,uuid[]),meetings.register_transcript(uuid,int,int,uuid,uuid[],jsonb,text) to demobank_meeting_ingress;
do $$ declare f regprocedure; begin
  for f in select oid::regprocedure from pg_proc where pronamespace='public'::regnamespace and proname like 'meeting\_%' escape '\' loop
    execute format('revoke all on function %s from public,anon,demobank_meetings,demobank_meeting_ingress',f);
    execute format('grant execute on function %s to authenticated',f);
  end loop;
end $$;
