-- Explicit exact-version correction decisions. Reviews never mutate an approved document or execute its actions.
create table meetings.minutes_reviews (
 review_id uuid primary key,draft_id uuid not null references meetings.drafts(draft_id),meeting_id uuid not null references meetings.workflows(meeting_id),
 outcome text not null check(outcome in ('changes','reject')),note text not null,actor uuid not null references auth.users(id),
 created_at timestamptz not null default clock_timestamp()
);
alter table meetings.minutes_reviews enable row level security;
revoke all on meetings.minutes_reviews from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create table meetings.review_jobs (
 event_id bigint primary key references meetings.events(event_id),review_id uuid not null references meetings.minutes_reviews(review_id)
);
alter table meetings.review_jobs enable row level security;
revoke all on meetings.review_jobs from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

alter function meetings.require_draft(uuid,uuid,int,text,int,int) rename to require_draft_before_review;
create function meetings.require_draft(p_meeting uuid,p_draft uuid,p_version int,p_hash text,p_revision int,p_epoch int) returns meetings.drafts
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts;
begin
 d:=meetings.require_draft_before_review(p_meeting,p_draft,p_version,p_hash,p_revision,p_epoch);
 if exists(select 1 from meetings.minutes_reviews where draft_id=d.draft_id) then
  raise exception 'this minutes version was returned or rejected; review a regenerated version before approval' using errcode='P1001';end if;
 return d;
end $$;

create function public.meeting_review_minutes(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_outcome text,p_note text,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts;receipt jsonb;review meetings.minutes_reviews;
 params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch,p_outcome,p_note);
begin
 perform meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'review_minutes',params);if receipt is not null then return receipt;end if;
 d:=meetings.require_draft_before_review(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
 if d.note_id is not null then raise exception 'approved minutes are immutable; record a separate correction instead' using errcode='P1001';end if;
 if p_outcome is null or p_outcome not in ('changes','reject') then raise exception 'choose changes or reject' using errcode='22023';end if;
 perform rm.require_text(p_note,1000,'minutes review reason');perform conversation.require_redacted(p_note);
 insert into meetings.minutes_reviews(review_id,draft_id,meeting_id,outcome,note,actor)
 values(p_command_id,d.draft_id,p_meeting_id,p_outcome,p_note,auth.uid()) returning * into review;
 -- A new review must not resume model chunks produced before that feedback.
 delete from meetings.source_chunks where source_id=d.source_id;
 perform audit.record('document','document.meeting_minutes_reviewed','success','meetings.drafts',d.draft_id::text,
  jsonb_build_object('meeting_id',p_meeting_id,'version',d.version,'outcome',p_outcome),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=p_meeting_id));
 return meetings.remember(p_command_id,p_meeting_id,'review_minutes',params,to_jsonb(review));
end $$;

create function meetings.latest_minutes_review(p_meeting uuid) returns meetings.minutes_reviews
language sql volatile security definer set search_path='' as $$
 select r from meetings.minutes_reviews r join meetings.workflows w on w.current_draft=r.draft_id
 join meetings.drafts d on d.draft_id=r.draft_id and d.source_id=w.current_source and d.revision=w.revision
 where w.meeting_id=p_meeting order by r.created_at desc,r.review_id desc limit 1
$$;

alter function meetings.job_context(bigint,uuid) rename to job_context_before_review;
create function meetings.job_context(p_event_id bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare context jsonb;e meetings.events;r meetings.minutes_reviews;job_review uuid;
begin
 context:=meetings.job_context_before_review(p_event_id,p_token);e:=meetings.require_claim(p_event_id,p_token);
 -- A committed draft is durable even if the process died before acknowledging its job.
 if context->>'draft_id' is not null then return context;end if;
 r:=meetings.latest_minutes_review(e.meeting_id);
 select review_id into job_review from meetings.review_jobs where event_id=p_event_id;
 if job_review is distinct from r.review_id then raise exception 'minutes review changed after this job was queued; regenerate the current reviewed version' using errcode='P1001';end if;
 if r.review_id is null then return context;end if;
 return context||jsonb_build_object('review_id',r.review_id,'review_note',r.note);
end $$;

alter function public.meeting_notes_context(uuid) rename to meeting_notes_context_before_review;
create function public.meeting_notes_context(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare context jsonb;r meetings.minutes_reviews;
begin
 context:=public.meeting_notes_context_before_review(p_meeting_id);r:=meetings.latest_minutes_review(p_meeting_id);
 if r.review_id is null then return context;end if;
 return context||jsonb_build_object('review_id',r.review_id,'review_note',r.note);
end $$;

-- Every write path, including the older explicit-note and generation RPCs, obeys the same review fence.
alter function meetings.store_draft(uuid,uuid,text,int,int,jsonb,bigint) rename to store_draft_before_review;
create function meetings.store_reviewed_draft(p_meeting uuid,p_source uuid,p_source_hash text,p_revision int,p_epoch int,p_content jsonb,p_job bigint,p_review uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare r meetings.minutes_reviews;
begin
 perform meetings.require_current(p_meeting);r:=meetings.latest_minutes_review(p_meeting);
 if r.review_id is distinct from p_review or r.outcome='reject' then
  raise exception 'minutes feedback changed or the source was rejected; use the current reviewed regeneration' using errcode='P1001';end if;
 if exists(select 1 from meetings.drafts where source_id=p_source and note_id is not null) then
  raise exception 'an approved source cannot be regenerated; save a separately reviewed correction source' using errcode='P1001';end if;
 return meetings.store_draft_before_review(p_meeting,p_source,p_source_hash,p_revision,p_epoch,p_content,p_job);
end $$;

create function meetings.store_draft(p_meeting uuid,p_source uuid,p_source_hash text,p_revision int,p_epoch int,p_content jsonb,p_job bigint) returns jsonb
language sql volatile security definer set search_path='' as $$
 select meetings.store_reviewed_draft(p_meeting,p_source,p_source_hash,p_revision,p_epoch,p_content,p_job,null)
$$;

create or replace function meetings.save_job_draft(p_event_id bigint,p_token uuid,p_source_hash text,p_revision int,p_consent_epoch int,p_content jsonb) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare context jsonb;
begin
 context:=meetings.job_context(p_event_id,p_token);
 if context->>'draft_id' is not null then
  return meetings.store_draft_before_review((context->>'meeting_id')::uuid,(context->>'source_id')::uuid,p_source_hash,p_revision,p_consent_epoch,p_content,p_event_id);
 end if;
 return meetings.store_reviewed_draft((context->>'meeting_id')::uuid,(context->>'source_id')::uuid,p_source_hash,p_revision,p_consent_epoch,p_content,p_event_id,(context->>'review_id')::uuid);
end $$;

create function public.meeting_regenerate_minutes(p_meeting_id uuid,p_draft_id uuid,p_version int,p_content_hash text,p_revision int,p_consent_epoch int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare d meetings.drafts;r meetings.minutes_reviews;s meetings.sources;receipt jsonb;job bigint;
 params jsonb:=jsonb_build_array(p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
begin
 perform meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'regenerate_minutes',params);if receipt is not null then return receipt;end if;
 d:=meetings.require_draft_before_review(p_meeting_id,p_draft_id,p_version,p_content_hash,p_revision,p_consent_epoch);
 r:=meetings.latest_minutes_review(p_meeting_id);
 if d.note_id is not null or r.outcome is distinct from 'changes' then raise exception 'request changes to the current unapproved minutes before regenerating' using errcode='P1001';end if;
 s:=meetings.require_source(p_meeting_id,d.source_id);
 if s.kind='transcript' then
  job:=meetings.enqueue_v2('transcript.ready',p_meeting_id,jsonb_build_object('revision',d.revision,'source_id',s.source_id),'review:'||p_command_id);
  insert into meetings.review_jobs values(job,r.review_id);
 end if;
 receipt:=jsonb_build_object('source_kind',s.kind,'event_id',job,'review_id',r.review_id);
 return meetings.remember(p_command_id,p_meeting_id,'regenerate_minutes',params,receipt);
end $$;

create function public.meeting_save_reviewed_draft(p_meeting_id uuid,p_source_id uuid,p_source_hash text,p_revision int,p_consent_epoch int,p_content jsonb,p_command_id uuid,p_review_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare r meetings.minutes_reviews;receipt jsonb;
begin
 perform meetings.require_rm(p_meeting_id);r:=meetings.latest_minutes_review(p_meeting_id);
 receipt:=public.meeting_generation_receipt(p_meeting_id,p_source_id,p_source_hash,p_revision,p_consent_epoch,p_command_id);
 if receipt is not null then return receipt;end if;
 if r.review_id is distinct from p_review_id or r.outcome='reject' then raise exception 'minutes feedback changed during generation; refresh and request changes before regenerating' using errcode='P1001';end if;
 if exists(select 1 from meetings.drafts where source_id=p_source_id and note_id is not null) then raise exception 'an approved source cannot be regenerated; save a separately reviewed correction source' using errcode='P1001';end if;
 receipt:=meetings.store_reviewed_draft(p_meeting_id,p_source_id,p_source_hash,p_revision,p_consent_epoch,p_content,null,p_review_id);
 return meetings.remember(p_command_id,p_meeting_id,'generate_minutes',jsonb_build_array(p_source_id,p_source_hash,p_revision,p_consent_epoch),receipt);
end $$;

create function public.meeting_minutes_review(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
begin
 perform meetings.require_rm(p_meeting_id);
 return to_jsonb(meetings.latest_minutes_review(p_meeting_id));
end $$;

revoke all on function meetings.require_draft_before_review(uuid,uuid,int,text,int,int),meetings.require_draft(uuid,uuid,int,text,int,int),
 meetings.latest_minutes_review(uuid),meetings.job_context_before_review(bigint,uuid),meetings.job_context(bigint,uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
revoke all on function meetings.store_draft_before_review(uuid,uuid,text,int,int,jsonb,bigint),meetings.store_reviewed_draft(uuid,uuid,text,int,int,jsonb,bigint,uuid),
 meetings.store_draft(uuid,uuid,text,int,int,jsonb,bigint) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
grant execute on function meetings.job_context(bigint,uuid) to demobank_meetings;
revoke all on function public.meeting_notes_context_before_review(uuid) from public,anon,authenticated;
revoke all on function public.meeting_notes_context(uuid),public.meeting_review_minutes(uuid,uuid,int,text,int,int,text,text,uuid),
 public.meeting_regenerate_minutes(uuid,uuid,int,text,int,int,uuid),public.meeting_save_reviewed_draft(uuid,uuid,text,int,int,jsonb,uuid,uuid),public.meeting_minutes_review(uuid) from public,anon;
grant execute on function public.meeting_notes_context(uuid),public.meeting_review_minutes(uuid,uuid,int,text,int,int,text,text,uuid),
 public.meeting_regenerate_minutes(uuid,uuid,int,text,int,int,uuid),public.meeting_save_reviewed_draft(uuid,uuid,text,int,int,jsonb,uuid,uuid),public.meeting_minutes_review(uuid) to authenticated;
