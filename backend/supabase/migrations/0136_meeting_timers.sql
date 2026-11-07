-- Durable preparation reminders and a two-hour transcript discovery deadline. All stored payloads are IDs/codes/times.
create table meetings.timers (
 timer_id uuid primary key default gen_random_uuid(),meeting_id uuid not null references rm.appointments on delete cascade,
 kind text not null check(kind in ('preparation','missing_transcript')),schedule_hash text not null,due_at timestamptz not null,
 status text not null check(status in ('pending','queued','notified','resolved','cancelled')),
 event_id bigint references meetings.events,acknowledged_at timestamptz,created_at timestamptz not null default clock_timestamp(),
 unique(meeting_id,kind,schedule_hash)
);
alter table meetings.timers enable row level security;
revoke all on meetings.timers from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create index meeting_timers_due on meetings.timers(due_at) where status='pending';
alter table meetings.events drop constraint events_event_type_check;
alter table meetings.events add constraint events_event_type_check check(event_type in
 ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','brief.requested','meeting.started','meeting.ended','transcript.ready','transcript.fetch','transcript.discover'));

create function meetings.refresh_timers(p_meeting uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;w meetings.workflows;current_hash text;
begin
 select * into a from rm.appointments where appointment_id=p_meeting;
 if not found then return;end if;
 current_hash:=meetings.schedule_hash(p_meeting);
 update meetings.timers set status='cancelled' where meeting_id=p_meeting and status in ('pending','queued','notified')
  and (schedule_hash<>current_hash or a.status<>'confirmed');
 if a.status<>'confirmed' or not rm.in_book(a.advisor_id,a.customer_id) then return;end if;
 select * into w from meetings.workflows where meeting_id=p_meeting;
 if a.starts_at>clock_timestamp() then
  insert into meetings.timers(meeting_id,kind,schedule_hash,due_at,status)
  values(p_meeting,'preparation',current_hash,a.starts_at-interval '1 day','pending') on conflict do nothing;
 end if;
 if exists(select 1 from meetings.briefs b where b.meeting_id=p_meeting and b.revision=w.revision and b.status='approved'
  and meetings.brief_access_current(p_meeting,b.brief_id)) then
  update meetings.timers set status='resolved' where meeting_id=p_meeting and kind='preparation' and schedule_hash=current_hash and status in ('pending','notified');
 end if;
 if w.state='ended' and w.ended_at is not null and w.ended_at>clock_timestamp()-interval '30 days' then
  insert into meetings.timers(meeting_id,kind,schedule_hash,due_at,status)
  values(p_meeting,'missing_transcript',current_hash,w.ended_at+interval '2 hours','pending')
  on conflict(meeting_id,kind,schedule_hash) do update set due_at=excluded.due_at where timers.status='pending';
 end if;
 if w.current_source is not null then
  update meetings.timers set status='resolved' where meeting_id=p_meeting and kind='missing_transcript' and schedule_hash=current_hash and status in ('pending','queued','notified');
 end if;
end $$;

create function meetings.project_timers() returns trigger language plpgsql security definer set search_path='' as $$
begin
 perform meetings.refresh_timers(coalesce((to_jsonb(new)->>'meeting_id')::uuid,(to_jsonb(new)->>'appointment_id')::uuid));
 return new;
end $$;
create trigger meeting_schedule_timers after insert or update of starts_at,ends_at,status,advisor_id on rm.appointments
 for each row execute function meetings.project_timers();
create trigger meeting_workflow_timers after insert or update on meetings.workflows for each row execute function meetings.project_timers();
create trigger meeting_brief_timers after insert or update on meetings.briefs for each row execute function meetings.project_timers();

create function meetings.fire_timer(p_timer uuid) returns int
language plpgsql volatile security definer set search_path='' as $$
declare t meetings.timers;a rm.appointments;w meetings.workflows;job bigint;target uuid;
begin
 select meeting_id into target from meetings.timers where timer_id=p_timer;
 perform 1 from rm.appointments where appointment_id=target for update skip locked;
 if not found then return 0;end if;
 select * into t from meetings.timers where timer_id=p_timer for update skip locked;
 if not found or t.status<>'pending' or t.due_at>clock_timestamp() then return 0;end if;
 perform meetings.refresh_timers(t.meeting_id);
 select * into t from meetings.timers where timer_id=p_timer;
 select * into a from rm.appointments where appointment_id=t.meeting_id;
 if t.status<>'pending' or t.schedule_hash<>meetings.schedule_hash(t.meeting_id) or a.status<>'confirmed'
  or not rm.in_book(a.advisor_id,a.customer_id) then return 0;end if;
 select * into w from meetings.workflows where meeting_id=t.meeting_id;
 if t.kind='preparation' then
  if a.starts_at<=clock_timestamp() then update meetings.timers set status='cancelled' where timer_id=t.timer_id;return 0;end if;
  update meetings.timers set status='notified' where timer_id=t.timer_id;
 elsif w.state='ended' and w.current_source is null then
  if meetings.recording_allowed(t.meeting_id)
   and exists(select 1 from meetings.zoom_bindings b where b.meeting_id=t.meeting_id and b.revision=w.revision and b.schedule_hash=w.schedule_hash)
   and not exists(select 1 from meetings.zoom_fetch_jobs j join meetings.zoom_bindings b using(binding_id) where b.meeting_id=t.meeting_id and b.revision=w.revision)
  then
   insert into meetings.events(event_type,meeting_id,customer_id,advisor_id,payload,dedupe_key)
   values('transcript.discover',t.meeting_id,a.customer_id,a.advisor_id,jsonb_build_object('revision',w.revision),'timer:'||t.timer_id)
   on conflict(dedupe_key) do nothing returning event_id into job;
   if job is null then select event_id into job from meetings.events where dedupe_key='timer:'||t.timer_id;end if;
   update meetings.timers set status='queued',event_id=job where timer_id=t.timer_id;
  else update meetings.timers set status='notified' where timer_id=t.timer_id;end if;
 else update meetings.timers set status='resolved' where timer_id=t.timer_id;return 0;
 end if;
 perform audit.record('notification','notification.meeting_reminder','success','meetings.timers',t.timer_id::text,
  jsonb_build_object('meeting_id',t.meeting_id,'kind',t.kind),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>a.customer_id);
 return 1;
end $$;

create function meetings.schedule_due() returns int language plpgsql volatile security definer set search_path='' as $$
declare id uuid;counted int:=0;
begin
 for id in select timer_id from meetings.timers where status='pending' and due_at<=clock_timestamp() order by due_at,timer_id limit 100
 loop counted:=counted+meetings.fire_timer(id);end loop;
 return counted;
end $$;
create function meetings.schedule_registered_timers(p_meetings uuid[]) returns int
language plpgsql volatile security definer set search_path='' as $$
declare id uuid;counted int:=0;
begin
 if cardinality(p_meetings) not between 1 and 20 or array_position(p_meetings,null) is not null then raise exception 'register one to twenty explicit meeting IDs' using errcode='22023';end if;
 for id in select timer_id from meetings.timers where meeting_id=any(p_meetings) and status='pending' and due_at<=clock_timestamp()
  order by due_at,timer_id limit 100
 loop counted:=counted+meetings.fire_timer(id);end loop;
 return counted;
end $$;

create function meetings.discovery_permit(p_event bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;w meetings.workflows;b meetings.zoom_bindings;
begin
 e:=meetings.require_claim(p_event,p_token);w:=meetings.require_current(e.meeting_id);
 if e.event_type<>'transcript.discover' or (e.payload->>'revision')::int<>w.revision or w.state<>'ended'
  or not meetings.recording_allowed(e.meeting_id) then raise exception 'transcript discovery requires the current ended meeting and both notices' using errcode='42501';end if;
 select * into b from meetings.zoom_bindings where meeting_id=e.meeting_id and revision=w.revision and schedule_hash=w.schedule_hash;
 if not found then raise exception 'the current meeting has no verified provider binding' using errcode='P1001';end if;
 return jsonb_build_object('meeting_id',e.meeting_id,'provider_id',b.provider_id,'account_id',b.account_id,'host_id',b.host_id,
  'original_uuid',b.original_uuid,'revision',w.revision,'consent_epoch',w.consent_epoch,'source_exists',w.current_source is not null);
end $$;
create function meetings.queue_discovered_note(p_event bigint,p_token uuid,p_epoch int,p_note text) returns bigint
language plpgsql volatile security definer set search_path='' as $$
declare permit jsonb;job bigint;
begin
 permit:=meetings.discovery_permit(p_event,p_token);
 if p_epoch is distinct from (permit->>'consent_epoch')::int then raise exception 'recording permission changed during discovery' using errcode='P1001';end if;
 job:=meetings.accept_zoom_note(permit->>'account_id',permit->>'host_id',permit->>'original_uuid',p_note,'my_notes.transcript_generated');
 update meetings.timers set status='resolved' where event_id=p_event;
 return job;
end $$;
create function meetings.report_missing_transcript(p_event bigint,p_token uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
 e:=meetings.require_claim(p_event,p_token);
 if e.event_type<>'transcript.discover' then raise exception 'only a claimed transcript discovery may report missing capture' using errcode='42501';end if;
 update meetings.timers set status='notified' where event_id=e.event_id;
end $$;

alter function meetings.retry_problem(meetings.events,meetings.workflows) rename to retry_problem_before_discovery;
create function meetings.retry_problem(e meetings.events,w meetings.workflows) returns text
language plpgsql volatile security definer set search_path='' as $$
declare checked meetings.events:=e;
begin
 if e.event_type='transcript.discover' then checked.event_type:='transcript.fetch';end if;
 return meetings.retry_problem_before_discovery(checked,w);
end $$;
revoke all on function meetings.retry_problem_before_discovery(meetings.events,meetings.workflows),meetings.retry_problem(meetings.events,meetings.workflows)
 from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function public.meeting_reminders() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=rm.current_advisor();result jsonb;
begin
 if advisor is null then raise exception 'meeting reminders require the assigned RM login' using errcode='42501';end if;
 select coalesce(jsonb_agg(row order by row->>'due_at',row->>'reminder_id'),'[]') into result from (
  select jsonb_build_object('reminder_id',t.timer_id,'meeting_id',a.appointment_id,'kind',t.kind,'due_at',t.due_at,
   'client',rm.display_name(p.full_name),'starts_at',a.starts_at,'error_code',e.last_error_code) row
  from meetings.timers t join rm.appointments a on a.appointment_id=t.meeting_id join banking.customer_profiles p using(customer_id)
  left join meetings.events e on e.event_id=t.event_id
  where a.advisor_id=advisor and rm.in_book(advisor,a.customer_id) and a.status='confirmed' and t.schedule_hash=meetings.schedule_hash(t.meeting_id)
   and (t.status='notified' or t.status='queued' and e.status='failed') and t.acknowledged_at is null
   and (t.kind='preparation' and a.starts_at>clock_timestamp() or t.kind='missing_transcript' and a.starts_at>clock_timestamp()-interval '30 days')
  order by t.due_at,t.timer_id limit 100
 ) reminders;
 perform meetings.read_audit(null,'meetings.timers',advisor::text,'meeting.reminders');
 return result;
end $$;

create function public.meeting_request_discovery(p_meeting_id uuid,p_revision int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;a rm.appointments;job bigint;receipt jsonb;params jsonb:=jsonb_build_array(p_revision);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'request_discovery',params);if receipt is not null then return receipt;end if;
 if w.revision is distinct from p_revision or w.state<>'ended' then raise exception 'check transcripts only for the current ended call' using errcode='P1001';end if;
 if not meetings.recording_allowed(p_meeting_id) then raise exception 'both participants must have read the current notice and transcript access must remain available' using errcode='42501';end if;
 if not exists(select 1 from meetings.zoom_bindings where meeting_id=p_meeting_id and revision=w.revision and schedule_hash=w.schedule_hash) then
  raise exception 'this meeting has no verified current Zoom binding' using errcode='P1001';end if;
 if w.current_source is not null then return meetings.remember(p_command_id,p_meeting_id,'request_discovery',params,jsonb_build_object('status','source_available','event_id',null));end if;
 select event_id into job from meetings.events where meeting_id=p_meeting_id and event_type in ('transcript.discover','transcript.fetch')
  and status in ('pending','waiting','processing') and (payload->>'revision')::int=w.revision order by event_id limit 1;
 if job is null then
  select * into a from rm.appointments where appointment_id=p_meeting_id;
  insert into meetings.events(event_type,meeting_id,customer_id,advisor_id,payload,dedupe_key)
  values('transcript.discover',p_meeting_id,a.customer_id,a.advisor_id,jsonb_build_object('revision',w.revision),'discovery:'||p_command_id) returning event_id into job;
  update meetings.timers set event_id=job,status='queued' where meeting_id=p_meeting_id and kind='missing_transcript' and schedule_hash=w.schedule_hash;
 end if;
 return meetings.remember(p_command_id,p_meeting_id,'request_discovery',params,jsonb_build_object('status','queued','event_id',job));
end $$;
create function public.meeting_acknowledge_reminder(p_reminder uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare t meetings.timers;advisor uuid:=rm.current_advisor();
begin
 select timer.* into t from meetings.timers timer join rm.appointments a on a.appointment_id=timer.meeting_id
 where timer.timer_id=p_reminder and a.advisor_id=advisor and rm.in_book(advisor,a.customer_id) for update of timer;
 if not found then raise exception 'this reminder is not owned by your current RM book' using errcode='42501';end if;
 if t.acknowledged_at is null then
  update meetings.timers set acknowledged_at=clock_timestamp() where timer_id=p_reminder;
  perform audit.record('notification','notification.meeting_reminder_acknowledged','success','meetings.timers',t.timer_id::text,
   jsonb_build_object('meeting_id',t.meeting_id),p_actor_type=>'staff',p_actor_id=>auth.uid()::text,
   p_customer_id=>(select customer_id from rm.appointments where appointment_id=t.meeting_id));
 end if;
end $$;

revoke all on function meetings.refresh_timers(uuid),meetings.project_timers(),meetings.fire_timer(uuid),meetings.schedule_due(),
 meetings.schedule_registered_timers(uuid[]),meetings.discovery_permit(bigint,uuid),meetings.queue_discovered_note(bigint,uuid,int,text),
 meetings.report_missing_transcript(bigint,uuid) from public,anon,authenticated,demobank_meeting_ingress,demobank_meetings;
grant execute on function meetings.schedule_due(),meetings.schedule_registered_timers(uuid[]),meetings.discovery_permit(bigint,uuid),
 meetings.queue_discovered_note(bigint,uuid,int,text),meetings.report_missing_transcript(bigint,uuid) to demobank_meetings;
revoke all on function public.meeting_reminders(),public.meeting_acknowledge_reminder(uuid),public.meeting_request_discovery(uuid,int,uuid) from public,anon;
grant execute on function public.meeting_reminders(),public.meeting_acknowledge_reminder(uuid),public.meeting_request_discovery(uuid,int,uuid) to authenticated;
do $$ declare id uuid;begin
 for id in select appointment_id from rm.appointments where status='confirmed' and starts_at>clock_timestamp()-interval '30 days'
 loop perform meetings.refresh_timers(id);end loop;
end $$;
