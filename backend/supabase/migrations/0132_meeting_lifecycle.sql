-- The parent graph's durable wait state. Only IDs, codes, versions and timestamps are checkpointed.
create table meetings.threads (
 meeting_id uuid primary key references rm.appointments(appointment_id) on delete cascade,
 stage text not null check(stage in ('scheduled','preparing','brief_review','brief_approved','awaiting_transcript','minutes_review','following_up','done','cancelled','failed')),
 waiting_for text check(waiting_for in ('rm','client','provider')),brief_version int,minutes_version int,error_code text,
 last_event_id bigint references meetings.events(event_id),updated_at timestamptz not null default clock_timestamp()
);
alter table meetings.threads enable row level security;
revoke all on meetings.threads from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;

create function meetings.brief_access_current(p_meeting uuid,p_brief uuid) returns boolean
language sql volatile security definer set search_path='' as $$
 select not exists(
  select 1 from meetings.briefs b join rm.appointments a on a.appointment_id=b.meeting_id,
  lateral jsonb_array_elements(b.source_manifest) item
  where b.brief_id=p_brief and b.meeting_id=p_meeting and (
   item->>'grant_id' is not null and (item->>'grant_id')::uuid is distinct from rm.grant_for(a.customer_id,item->>'category',a.advisor_id)
   or item->>'point_id' is not null and not exists(select 1 from insights.talking_points p
    where p.point_id=(item->>'point_id')::uuid and p.customer_id=a.customer_id and p.deleted_at is null
     and p.expires_at>clock_timestamp() and p.visible_from<=clock_timestamp())
  )
 )
$$;

create function meetings.sync_lifecycle(p_meeting uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;w meetings.workflows;b meetings.briefs;d meetings.drafts;t meetings.threads;r meetings.minutes_reviews;
 phase text:='scheduled';waiting text:='rm';error text;open_rm int;open_client int;shared boolean;failed_job meetings.events;
begin
 select * into a from rm.appointments where appointment_id=p_meeting for update;
 if not found then raise exception 'meeting appointment not found' using errcode='P0002';end if;
 select * into w from meetings.workflows where meeting_id=p_meeting;
 select * into b from meetings.briefs where meeting_id=p_meeting and revision=w.revision order by version desc limit 1;
 select * into d from meetings.drafts where draft_id=w.current_draft and source_id=w.current_source and revision=w.revision;
 r:=meetings.latest_minutes_review(p_meeting);
 if a.status='cancelled' or w.state='cancelled' then phase:='cancelled';waiting:=null;
 elsif w.state in ('in_progress','ended') then
  if d.draft_id is null then
   phase:='awaiting_transcript';waiting:=case
    when exists(select 1 from meetings.sources where source_id=w.current_source and kind='rm_notes')
     or w.current_source is null and not exists(select 1 from meetings.zoom_bindings where meeting_id=p_meeting and revision=w.revision)
    then 'rm' else 'provider' end;
  elsif d.note_id is null then
   phase:='minutes_review';waiting:='rm';
   if r.review_id is not null then error:=case when r.outcome='changes' then 'minutes_changes_requested' else 'minutes_rejected' end;end if;
  else
   select count(*) filter(where owner_role='rm'),count(*) filter(where owner_role='client') into open_rm,open_client
    from meetings.followups where meeting_id=p_meeting and status='open';
   shared:=exists(select 1 from meetings.effects e join meetings.drafts x using(draft_id) where x.meeting_id=p_meeting and e.scope='recap');
   if open_rm+open_client=0 and shared then phase:='done';waiting:=null;
   else phase:='following_up';waiting:=case when open_rm>0 or not shared then 'rm' else 'client' end;end if;
  end if;
 elsif b.brief_id is not null then
  if b.status='approved' and meetings.brief_access_current(p_meeting,b.brief_id) then phase:='brief_approved';waiting:='provider';
  else phase:='brief_review';waiting:='rm';if b.status='approved' then error:='brief_access_changed';end if;end if;
 elsif exists(select 1 from meetings.events where meeting_id=p_meeting and event_type='brief.requested') then phase:='preparing';
 end if;
 if phase in ('awaiting_transcript','minutes_review') and d.note_id is null then
  select * into failed_job from meetings.events where meeting_id=p_meeting and event_type in ('transcript.fetch','transcript.ready') and status='failed'
   and (payload->>'revision')::int=w.revision order by event_id desc limit 1;
  if failed_job.event_id is not null and (d.draft_id is null or failed_job.event_id>d.job_id) then
   phase:='failed';waiting:='rm';error:=failed_job.last_error_code;
  end if;
 end if;
 insert into meetings.threads(meeting_id,stage,waiting_for,brief_version,minutes_version,error_code)
 values(p_meeting,phase,waiting,b.version,d.version,error)
 on conflict(meeting_id) do update set stage=excluded.stage,waiting_for=excluded.waiting_for,brief_version=excluded.brief_version,
  minutes_version=excluded.minutes_version,error_code=excluded.error_code,updated_at=clock_timestamp()
 where (threads.stage,threads.waiting_for,threads.brief_version,threads.minutes_version,threads.error_code)
  is distinct from (excluded.stage,excluded.waiting_for,excluded.brief_version,excluded.minutes_version,excluded.error_code);
 select * into t from meetings.threads where meeting_id=p_meeting;
 return jsonb_build_object('meeting_id',t.meeting_id,'stage',t.stage,'waiting_for',t.waiting_for,'brief_version',t.brief_version,
  'minutes_version',t.minutes_version,'error_code',t.error_code,'updated_at',t.updated_at);
end $$;

create function meetings.project_lifecycle() returns trigger
language plpgsql security definer set search_path='' as $$
declare target uuid;
begin
 target:=(to_jsonb(new)->>'meeting_id')::uuid;
 if target is null and to_jsonb(new)->>'draft_id' is not null then select meeting_id into target from meetings.drafts where draft_id=(to_jsonb(new)->>'draft_id')::uuid;end if;
 if target is not null then perform meetings.sync_lifecycle(target);end if;
 return new;
end $$;
do $$ declare name text;begin
 foreach name in array array['workflows','briefs','packs','drafts','effects','followups','minutes_reviews','events'] loop
  execute format('create trigger meeting_lifecycle_projection after insert or update on meetings.%I for each row execute function meetings.project_lifecycle()',name);
 end loop;
end $$;

create function public.meeting_lifecycle_status(p_meeting_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;
begin
 select * into a from rm.appointments where appointment_id=p_meeting_id;
 if not found then raise exception 'meeting appointment not found' using errcode='P0002';end if;
 if not coalesce(banking.my_customer_id()=a.customer_id or rm.current_advisor()=a.advisor_id and rm.in_book(a.advisor_id,a.customer_id),false) then
  raise exception 'meeting lifecycle is restricted to its current participants' using errcode='42501';end if;
 perform meetings.read_audit(a.customer_id,'meetings.threads',p_meeting_id::text,'meeting.lifecycle_status');
 return meetings.sync_lifecycle(p_meeting_id);
end $$;

create function meetings.checkpoint_claim(p_event_id bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;result jsonb;
begin
 e:=meetings.require_claim(p_event_id,p_token);result:=meetings.sync_lifecycle(e.meeting_id);
 update meetings.threads set last_event_id=e.event_id where meeting_id=e.meeting_id;
 return result;
end $$;

alter function meetings.apply_lifecycle(bigint,uuid) rename to apply_lifecycle_before_parent;
create function meetings.apply_lifecycle(p_event_id bigint,p_token uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
 e:=meetings.require_claim(p_event_id,p_token);
 if e.event_type<>'brief.requested' then perform meetings.apply_lifecycle_before_parent(p_event_id,p_token);end if;
 perform meetings.checkpoint_claim(p_event_id,p_token);
end $$;
revoke all on function meetings.brief_access_current(uuid,uuid),meetings.sync_lifecycle(uuid),meetings.project_lifecycle(),meetings.apply_lifecycle_before_parent(bigint,uuid),meetings.checkpoint_claim(bigint,uuid),meetings.apply_lifecycle(bigint,uuid) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
grant execute on function meetings.checkpoint_claim(bigint,uuid),meetings.apply_lifecycle(bigint,uuid) to demobank_meetings;
revoke all on function public.meeting_lifecycle_status(uuid) from public,anon;
grant execute on function public.meeting_lifecycle_status(uuid) to authenticated;
