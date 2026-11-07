-- A hosted rehearsal can process explicitly registered synthetic meetings without consuming the team's queue.
create function meetings.claim_meeting(p_meeting uuid) returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;dead meetings.events;
begin
 perform 1 from rm.appointments where appointment_id=p_meeting for update skip locked;
 if not found then return null;end if;
 if exists(select 1 from meetings.events where meeting_id=p_meeting and status='processing' and available_at>clock_timestamp()) then return null;end if;
 for dead in update meetings.events set status='failed',claim_token=null,processed_at=clock_timestamp(),last_error_code='attempts_exhausted'
  where meeting_id=p_meeting and status in ('pending','processing') and available_at<=clock_timestamp() and attempts>=5 returning * loop
  perform audit.record('action','action.meeting_event_failed','failed','meetings.events',dead.event_id::text,
   jsonb_build_object('error_code','attempts_exhausted','attempts',dead.attempts),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>dead.customer_id);
 end loop;
 select * into e from meetings.events where meeting_id=p_meeting and status in ('pending','processing')
  and available_at<=clock_timestamp() and attempts<5 order by event_id for update limit 1;
 if not found then return null;end if;
 update meetings.events set status='processing',attempts=attempts+1,claim_token=gen_random_uuid(),claimed_at=clock_timestamp(),
  available_at=clock_timestamp()+interval '10 minutes' where event_id=e.event_id returning * into e;
 return e;
end $$;

create or replace function meetings.claim_next() returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare target uuid;e meetings.events;
begin
 for target in select distinct meeting_id from meetings.events where status in ('pending','processing') and available_at<=clock_timestamp() order by meeting_id loop
  e:=meetings.claim_meeting(target);if e.event_id is not null then return e;end if;
 end loop;
 return null;
end $$;

create function meetings.claim_registered(p_meetings uuid[]) returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare target uuid;e meetings.events;
begin
 if p_meetings is null or cardinality(p_meetings) not between 1 and 20 or array_position(p_meetings,null) is not null
  then raise exception 'register one to twenty explicit synthetic meeting IDs before starting the scoped worker' using errcode='22023';end if;
 for target in select distinct unnest(p_meetings) order by 1 loop
  e:=meetings.claim_meeting(target);if e.event_id is not null then return e;end if;
 end loop;
 return null;
end $$;

create function meetings.purge_source(p_source uuid) returns int
language plpgsql volatile security definer set search_path='' as $$
declare removed meetings.sources;
begin
 update meetings.sources set cues='[]',purged_at=clock_timestamp() where source_id=p_source
  and kind='transcript' and purged_at is null and purge_after<=clock_timestamp() returning * into removed;
 if not found then return 0;end if;
 perform audit.record('document','document.meeting_transcript_purged','success','meetings.sources',removed.source_id::text,
  jsonb_build_object('meeting_id',removed.meeting_id),p_actor_type=>'system',p_actor_id=>'meeting_retention',
  p_customer_id=>(select customer_id from rm.appointments where appointment_id=removed.meeting_id));
 return 1;
end $$;

create or replace function meetings.purge_transcripts() returns int
language plpgsql volatile security definer set search_path='' as $$
declare source uuid;total int:=0;
begin
 for source in select source_id from meetings.sources where kind='transcript' and purged_at is null and purge_after<=clock_timestamp() loop
  total:=total+meetings.purge_source(source);
 end loop;return total;
end $$;

create function meetings.purge_registered(p_meetings uuid[]) returns int
language plpgsql volatile security definer set search_path='' as $$
declare source uuid;total int:=0;
begin
 if p_meetings is null or cardinality(p_meetings) not between 1 and 20 or array_position(p_meetings,null) is not null
  then raise exception 'register one to twenty explicit synthetic meeting IDs before running scoped retention' using errcode='22023';end if;
 for source in select source_id from meetings.sources where meeting_id=any(p_meetings) and kind='transcript' and purged_at is null and purge_after<=clock_timestamp() loop
  total:=total+meetings.purge_source(source);
 end loop;return total;
end $$;
revoke all on function meetings.claim_meeting(uuid),meetings.purge_source(uuid),meetings.claim_registered(uuid[]),meetings.purge_registered(uuid[]) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
grant execute on function meetings.claim_registered(uuid[]),meetings.purge_registered(uuid[]) to demobank_meetings;
