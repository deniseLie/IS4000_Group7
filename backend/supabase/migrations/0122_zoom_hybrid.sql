-- Zoom hybrid evidence intake, notice delivery and transcript retention.
-- Notice acknowledgements model delivery of the versioned notice, not a new broad data-sharing grant.
alter table meetings.sources add column revoked_at timestamptz, add column purge_after timestamptz, add column purged_at timestamptz;
alter table meetings.sources drop constraint sources_origin_check;
alter table meetings.sources add constraint sources_origin_check check(origin in
 ('uploaded_synthetic_transcript','rm_typed_notes','zoom_export_vtt','zoom_export_txt'));
create table meetings.notice_acknowledgements (
 meeting_id uuid not null references meetings.workflows, participant uuid not null references auth.users,
 revision int not null,notice_id uuid not null references meetings.notice_versions,seen_at timestamptz not null default clock_timestamp(),
 primary key(meeting_id,participant,revision,notice_id));
create table meetings.import_provenance (
 source_id uuid primary key references meetings.sources,file_hash text not null check(file_hash~'^[a-f0-9]{64}$'),
 coverage text not null check(coverage in ('full_rm_attested','partial','unknown')),imported_by uuid not null references auth.users,
 speaker_basis text not null check(speaker_basis='rm_asserted_mapping'));
alter table meetings.notice_acknowledgements enable row level security;
alter table meetings.import_provenance enable row level security;
revoke all on meetings.notice_acknowledgements,meetings.import_provenance from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create function public.meeting_acknowledge_notice(p_meeting_id uuid,p_notice_id uuid,p_revision int) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;
begin
 w:=meetings.require_participant(p_meeting_id);
 if p_notice_id is distinct from w.notice_id or p_revision is distinct from w.revision
  or not exists(select 1 from meetings.notice_versions where notice_id=w.notice_id and active)
 then raise exception 'the meeting or notice changed; read the current invitation' using errcode='P1001'; end if;
 insert into meetings.notice_acknowledgements(meeting_id,participant,revision,notice_id)
 values(p_meeting_id,auth.uid(),w.revision,w.notice_id) on conflict do nothing;
 return jsonb_build_object('seen',true,'notice_id',w.notice_id,'revision',w.revision);
end $$;
create or replace function meetings.recording_allowed(p_meeting uuid) returns boolean
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;people uuid[];
begin
 w:=meetings.require_current(p_meeting);people:=meetings.participants(p_meeting);
 return cardinality(people)=2 and array_position(people,null) is null
  and exists(select 1 from meetings.notice_versions where notice_id=w.notice_id and active)
  and not exists(select 1 from unnest(people) p where not exists(select 1 from meetings.notice_acknowledgements n
   where n.meeting_id=p_meeting and n.participant=p and n.revision=w.revision and n.notice_id=w.notice_id))
  and not exists(select 1 from meetings.commands where meeting_id=p_meeting and operation='revoke_recording');
end $$;


create or replace function meetings.validate_cues(p_cues jsonb,p_kind text) returns void language plpgsql set search_path='' as $$
declare cue jsonb; ids text[]:='{}';
begin
  if p_cues is null or jsonb_typeof(p_cues)<>'array' or jsonb_array_length(p_cues) not between 1 and 100 or octet_length(p_cues::text)>16000 then raise exception 'source requires 1-100 bounded redacted cues' using errcode='22023'; end if;
  for cue in select * from jsonb_array_elements(p_cues) loop
    if (select array_agg(key order by key) from jsonb_object_keys(cue) key) is distinct from array['cue_id','speaker','start_seconds','text']
        or jsonb_typeof(cue->'cue_id') is distinct from 'string' or cue->>'cue_id' !~ '^[a-z][a-z0-9_-]{0,39}$' or cue->>'cue_id'=any(ids)
        or jsonb_typeof(cue->'text') is distinct from 'string' or length(btrim(cue->>'text')) not between 1 and 2000
        or jsonb_typeof(cue->'speaker') is distinct from 'string' or cue->>'speaker' not in ('rm','client','unmapped') then raise exception 'invalid source cue identity, text or speaker' using errcode='22023'; end if;
    if p_kind='transcript' and cue->'start_seconds'<>'null'::jsonb and (jsonb_typeof(cue->'start_seconds') is distinct from 'number' or cue->>'start_seconds' !~ '^[0-9]{1,7}$') then raise exception 'transcript cues require a nonnegative integer position' using errcode='22023'; end if;
    if p_kind='rm_notes' and cue->'start_seconds'<>'null'::jsonb then raise exception 'typed notes must not claim transcript timestamps' using errcode='22023'; end if;
    perform conversation.require_redacted(cue->>'text'); ids:=array_append(ids,cue->>'cue_id');
  end loop;
end $$;

create function public.meeting_import_transcript(p_meeting_id uuid,p_revision int,p_cues jsonb,p_origin text,
 p_file_hash text,p_coverage text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;id uuid:=p_command_id;event bigint;people uuid[];receipt jsonb;
 params jsonb:=jsonb_build_array(p_revision,meetings.hash(p_cues),p_origin,p_file_hash,p_coverage);
begin
 w:=meetings.require_rm(p_meeting_id);
 if p_origin not in ('zoom_export_vtt','zoom_export_txt') or p_file_hash!~'^[a-f0-9]{64}$'
  or p_coverage not in ('full_rm_attested','partial','unknown') then raise exception 'invalid import provenance' using errcode='22023'; end if;
 receipt:=meetings.replay(p_command_id,p_meeting_id,'import_transcript',params);if receipt is not null then return receipt;end if;
 people:=meetings.participants(p_meeting_id);
 event:=meetings.register_transcript(p_meeting_id,p_revision,w.consent_epoch,id,people,p_cues,'import:'||p_command_id);
 update meetings.sources set origin=p_origin where source_id=id;
 insert into meetings.import_provenance values(id,p_file_hash,p_coverage,auth.uid(),'rm_asserted_mapping');
 return meetings.remember(p_command_id,p_meeting_id,'import_transcript',params,
  jsonb_build_object('source_id',id,'event_id',event,'origin',p_origin,'coverage',p_coverage));
end $$;
create function public.meeting_transcript_revoke(p_meeting_id uuid,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare receipt jsonb;
begin
 if not exists(select 1 from rm.appointments where appointment_id=p_meeting_id and customer_id=banking.my_customer_id())
 then raise exception 'only this client can withdraw this transcript' using errcode='42501'; end if;
 receipt:=public.meeting_revoke_recording(p_meeting_id,p_command_id);
 update meetings.sources set revoked_at=coalesce(revoked_at,clock_timestamp()),
  purge_after=least(coalesce(purge_after,clock_timestamp()+interval '7 days'),clock_timestamp()+interval '7 days')
 where meeting_id=p_meeting_id and kind='transcript';
 return receipt||jsonb_build_object('transcript_access','revoked','approved_minutes','retained');
end $$;


create or replace function meetings.require_source(p_meeting uuid,p_source uuid) returns meetings.sources
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; s meetings.sources;
begin
  w:=meetings.require_current(p_meeting);
  select * into s from meetings.sources where source_id=p_source and meeting_id=p_meeting;
  if not found then raise exception 'source does not belong to this meeting' using errcode='42501'; end if;
  if s.revoked_at is not null or s.purged_at is not null or s.source_id is distinct from w.current_source or s.revision<>w.revision or s.occurrence_id is distinct from w.occurrence_id
      or (s.kind='transcript' and (s.consent_epoch<>w.consent_epoch or not meetings.recording_allowed(p_meeting))) then
    raise exception 'source was replaced or its recording permission changed' using errcode='P1001';
  end if;
  return s;
end $$;

create or replace function meetings.require_draft(p_meeting uuid,p_draft uuid,p_version int,p_hash text,p_revision int,p_epoch int) returns meetings.drafts
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; d meetings.drafts; retired boolean;
begin
  w:=meetings.require_rm(p_meeting);
  select * into d from meetings.drafts where draft_id=p_draft and meeting_id=p_meeting for update;
  if not found then raise exception 'draft does not belong to this meeting' using errcode='42501'; end if;
  select (revoked_at is not null or purged_at is not null and d.note_id is not null) into retired from meetings.sources where source_id=d.source_id and meeting_id=p_meeting and source_hash=d.source_hash;
  if not coalesce(retired,false) then perform meetings.require_source(p_meeting,d.source_id); end if;
  if d.draft_id is distinct from w.current_draft or p_version is distinct from d.version or p_hash is distinct from d.content_hash
      or p_revision is distinct from w.revision or d.revision<>w.revision or p_epoch is distinct from w.consent_epoch or (not coalesce(retired,false) and d.consent_epoch<>w.consent_epoch) then
    raise exception 'approval references a replaced draft or stale meeting, source or permission version' using errcode='P1001'; end if;
  return d;
end $$;

create or replace function public.meeting_view(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; a rm.appointments; own_client boolean; d meetings.drafts; result jsonb; source_kind text; retired boolean;
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
    select revoked_at is not null or (purged_at is not null and d.note_id is not null) into retired from meetings.sources where source_id=d.source_id;
    if coalesce(retired,false) or source_kind<>'transcript' or (d.consent_epoch=w.consent_epoch and meetings.recording_allowed(p_meeting_id)) then
      result:=result||jsonb_build_object('draft',jsonb_build_object('draft_id',d.draft_id,'version',d.version,'revision',d.revision,'consent_epoch',case when retired then w.consent_epoch else d.consent_epoch end,'source_revoked',coalesce(retired,false),
        'content_hash',d.content_hash,'source_kind',source_kind,'content',d.content,'approved',d.note_id is not null));
    else result:=result||jsonb_build_object('draft_withheld','recording_permission_changed'); end if;
  end if;
  if not own_client then
    result:=result||jsonb_build_object('jobs',(select coalesce(jsonb_agg(jsonb_build_object('event_id',event_id,'type',event_type,
      'status',status,'attempts',attempts,'error_code',last_error_code) order by event_id),'[]') from meetings.events where meeting_id=p_meeting_id));
  end if;
  perform meetings.read_audit(a.customer_id,'rm.appointments',p_meeting_id::text,'meeting.workspace');
  return result||jsonb_build_object('notice',(select body from meetings.notice_versions where notice_id=w.notice_id),
 'notice_seen',exists(select 1 from meetings.notice_acknowledgements where meeting_id=p_meeting_id and participant=auth.uid() and revision=w.revision and notice_id=w.notice_id),
 'source_provenance',(select jsonb_build_object('origin',s.origin,'coverage',p.coverage,'speaker_basis',p.speaker_basis) from meetings.sources s left join meetings.import_provenance p using(source_id) where s.source_id=w.current_source));
end $$;

alter function public.meeting_approve_minutes(uuid,uuid,int,text,int,int,uuid) rename to meeting_approve_minutes_before_retention;
revoke all on function public.meeting_approve_minutes_before_retention(uuid,uuid,int,text,int,int,uuid) from public,anon,authenticated;
create function public.meeting_approve_minutes(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;
begin
 result:=public.meeting_approve_minutes_before_retention(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch,p_command_id);
 update meetings.sources set purge_after=coalesce(purge_after,clock_timestamp()+interval '30 days')
 where source_id=(select source_id from meetings.drafts where draft_id=p_draft_id) and kind='transcript';
 return result;
end $$;
create function meetings.purge_transcripts() returns int language plpgsql volatile security definer set search_path='' as $$
declare total int:=0;removed meetings.sources;
begin
 for removed in update meetings.sources set cues='[]',purged_at=clock_timestamp()
 where kind='transcript' and purged_at is null and purge_after<=clock_timestamp() returning * loop
 perform audit.record('document','document.meeting_transcript_purged','success','meetings.sources',removed.source_id::text,
  jsonb_build_object('meeting_id',removed.meeting_id),p_actor_type=>'system',p_actor_id=>'meeting_retention',
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=removed.meeting_id));
 total:=total+1;
 end loop;return total;
end $$;
revoke all on function public.meeting_acknowledge_notice(uuid,uuid,int),public.meeting_import_transcript(uuid,int,jsonb,text,text,text,uuid),
 public.meeting_transcript_revoke(uuid,uuid),public.meeting_approve_minutes(uuid,uuid,int,text,int,int,uuid),meetings.purge_transcripts() from public,anon;
grant execute on function public.meeting_acknowledge_notice(uuid,uuid,int),public.meeting_import_transcript(uuid,int,jsonb,text,text,text,uuid),
 public.meeting_transcript_revoke(uuid,uuid),public.meeting_approve_minutes(uuid,uuid,int,text,int,int,uuid) to authenticated;
grant execute on function meetings.purge_transcripts() to demobank_meetings;
