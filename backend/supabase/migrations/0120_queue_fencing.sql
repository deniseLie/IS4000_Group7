-- Meeting-side queue fencing. Requires migration 0105.
-- Queue ownership is fenced by an unguessable token; appointment locks serialize claims.
alter table meetings.events add column claim_token uuid, add column claimed_at timestamptz;
alter table meetings.events drop constraint events_event_type_check;
alter table meetings.events add constraint events_event_type_check check (event_type in
 ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','brief.requested','meeting.started','meeting.ended','transcript.ready'));

create or replace function meetings.enqueue_v2(p_type text,p_meeting_id uuid,p_payload jsonb,p_dedupe_key text)
returns bigint language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments; e meetings.events; id bigint; keys text[];
begin
  if p_type is null or p_type not in ('meeting.scheduled','meeting.rescheduled','meeting.cancelled','brief.requested',
      'meeting.started','meeting.ended','transcript.ready') then
    raise exception 'unsupported meeting event type' using errcode='22023';
  end if;
  if p_dedupe_key is null or length(p_dedupe_key) not between 1 and 200 then
    raise exception 'meeting event needs a nonempty dedupe key, at most 200 characters' using errcode='22023';
  end if;
  if p_payload is null or jsonb_typeof(p_payload)<>'object' or octet_length(p_payload::text)>4096 then
    raise exception 'meeting event payload must be a closed object, at most 4096 bytes' using errcode='22023';
  end if;
  select array_agg(key order by key) into keys from jsonb_object_keys(p_payload) key;
  if p_type='transcript.ready' then
    if keys is distinct from array['revision','source_id'] then raise exception 'transcript event requires only revision and source_id' using errcode='22023'; end if;
    if jsonb_typeof(p_payload->'source_id') is distinct from 'string' then raise exception 'source_id must be a UUID' using errcode='22023'; end if;
    perform (p_payload->>'source_id')::uuid;
  elsif p_type in ('meeting.started','meeting.ended') then
    if keys is distinct from array['occurrence_id','revision'] then raise exception 'call event requires only occurrence_id and revision' using errcode='22023'; end if;
    if jsonb_typeof(p_payload->'occurrence_id') is distinct from 'string' then raise exception 'occurrence_id must be a UUID' using errcode='22023'; end if;
    perform (p_payload->>'occurrence_id')::uuid;
  elsif keys is distinct from array['revision'] then
    raise exception 'meeting event requires only revision' using errcode='22023';
  end if;
  if jsonb_typeof(p_payload->'revision') is distinct from 'number' or (p_payload->>'revision') !~ '^[1-9][0-9]{0,8}$' then
    raise exception 'revision must be an integer between 1 and 999999999' using errcode='22023';
  end if;
  select * into a from rm.appointments where appointment_id=p_meeting_id for update;
  if not found then raise exception 'meeting not found' using errcode='P0002'; end if;
  select * into e from meetings.events where dedupe_key=p_dedupe_key;
  if found then
    if e.meeting_id is distinct from p_meeting_id or e.event_type is distinct from p_type or e.payload is distinct from p_payload then
      raise exception 'dedupe key already belongs to a different meeting event' using errcode='23505';
    end if;
    return e.event_id;
  end if;
  insert into meetings.events(event_type,meeting_id,customer_id,advisor_id,payload,dedupe_key)
    values(p_type,p_meeting_id,a.customer_id,a.advisor_id,p_payload,p_dedupe_key)
    on conflict(dedupe_key) do nothing returning event_id into id;
  if id is null then
    select * into e from meetings.events where dedupe_key=p_dedupe_key;
    if e.meeting_id is distinct from p_meeting_id or e.event_type is distinct from p_type or e.payload is distinct from p_payload then
      raise exception 'dedupe key already belongs to a different meeting event' using errcode='23505';
    end if;
    return e.event_id;
  end if;
  perform audit.record('action','action.meeting_event_queued','success','meetings.events',id::text,
    jsonb_build_object('event_type',p_type,'meeting_id',p_meeting_id),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>a.customer_id);
  return id;
end $$;

create or replace function meetings.claim_next() returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare target uuid; locked uuid; e meetings.events; dead meetings.events;
begin
  for target in select distinct meeting_id from meetings.events
      where status in ('pending','processing') and available_at<=clock_timestamp() order by meeting_id loop
    select appointment_id into locked from rm.appointments where appointment_id=target for update skip locked;
    if not found then continue; end if;
    -- Fresh statement after the meeting lock handles another claimant's recent commit.
    if exists(select 1 from meetings.events where meeting_id=target and status='processing' and available_at>clock_timestamp()) then continue; end if;
    for dead in update meetings.events set status='failed',claim_token=null,processed_at=clock_timestamp(),last_error_code='attempts_exhausted'
        where meeting_id=target and status in ('pending','processing') and available_at<=clock_timestamp() and attempts>=5 returning * loop
      perform audit.record('action','action.meeting_event_failed','failed','meetings.events',dead.event_id::text,
        jsonb_build_object('error_code','attempts_exhausted','attempts',dead.attempts),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>dead.customer_id);
    end loop;
    select * into e from meetings.events where meeting_id=target and status in ('pending','processing')
      and available_at<=clock_timestamp() and attempts<5 order by event_id for update limit 1;
    if not found then continue; end if;
    update meetings.events set status='processing',attempts=attempts+1,claim_token=gen_random_uuid(),claimed_at=clock_timestamp(),
      available_at=clock_timestamp()+interval '10 minutes' where event_id=e.event_id returning * into e;
    return e;
  end loop;
  return null;
end $$;

create function meetings.require_claim(p_event_id bigint,p_token uuid) returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events; target uuid;
begin
  select meeting_id into target from meetings.events where event_id=p_event_id;
  if not found then raise exception 'meeting event not found' using errcode='P0002'; end if;
  perform 1 from rm.appointments where appointment_id=target for update;
  select * into e from meetings.events where event_id=p_event_id for update;
  if p_token is null or e.claim_token is distinct from p_token or e.status<>'processing' or e.available_at<=clock_timestamp() then
    raise exception 'meeting claim is expired, replaced or no longer processing; no write is allowed' using errcode='P1002';
  end if;
  return e;
end $$;

create function meetings.reject(p_event_id bigint,p_token uuid,p_error_code text) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
  if p_error_code is null or p_error_code !~ '^[a-z][a-z0-9_]{0,39}$' then raise exception 'rejected event requires a bounded error code' using errcode='22023'; end if;
  e:=meetings.require_claim(p_event_id,p_token);
  update meetings.events set status='failed',claim_token=null,processed_at=clock_timestamp(),last_error_code=p_error_code where event_id=e.event_id;
  perform audit.record('action','action.meeting_event_failed','failed','meetings.events',e.event_id::text,
    jsonb_build_object('error_code',p_error_code,'attempts',e.attempts),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>e.customer_id);
end $$;

create function meetings.renew(p_event_id bigint,p_token uuid) returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
  e:=meetings.require_claim(p_event_id,p_token);
  if e.claimed_at+interval '30 minutes'<=clock_timestamp() then raise exception 'meeting job exceeded its 30-minute execution window' using errcode='P1001'; end if;
  update meetings.events set available_at=least(clock_timestamp()+interval '10 minutes',claimed_at+interval '30 minutes')
    where event_id=e.event_id returning * into e;
  return e;
end $$;

create function meetings.finish(p_event_id bigint,p_token uuid,p_ok boolean,p_error_code text) returns meetings.events
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
  if p_ok is null or (not p_ok and (p_error_code is null or p_error_code !~ '^[a-z][a-z0-9_]{0,39}$')) or (p_ok and p_error_code is not null) then
    raise exception 'success requires no error code; failure requires a bounded error code' using errcode='22023';
  end if;
  e:=meetings.require_claim(p_event_id,p_token);
  update meetings.events set status=case when p_ok then 'done' when attempts>=5 then 'failed' else 'pending' end,
    processed_at=case when p_ok or attempts>=5 then clock_timestamp() end,
    last_error_code=p_error_code,claim_token=null,
    available_at=case when p_ok or attempts>=5 then available_at else clock_timestamp()+make_interval(secs=>least(300,power(2,attempts)::int)) end
    where event_id=e.event_id returning * into e;
  if e.status='failed' then
    perform audit.record('action','action.meeting_event_failed','failed','meetings.events',e.event_id::text,
      jsonb_build_object('error_code',e.last_error_code,'attempts',e.attempts),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>e.customer_id);
  end if;
  return e;
end $$;

-- Keep the old function present for schema compatibility, but no worker may use its unfenced signature.
revoke all on function meetings.finish(bigint,boolean,text) from demobank_meetings;
revoke all on function meetings.enqueue(text,uuid,jsonb,text) from demobank_meetings;
revoke all on function meetings.enqueue_v2(text,uuid,jsonb,text) from public,anon,authenticated;
grant execute on function meetings.enqueue_v2(text,uuid,jsonb,text) to demobank_meetings;
revoke all on function meetings.require_claim(bigint,uuid) from public,anon,authenticated,demobank_meetings;
revoke all on function meetings.reject(bigint,uuid,text) from public,anon,authenticated;
grant execute on function meetings.reject(bigint,uuid,text) to demobank_meetings;
revoke all on function meetings.renew(bigint,uuid),meetings.finish(bigint,uuid,boolean,text) from public,anon,authenticated;
grant execute on function meetings.renew(bigint,uuid),meetings.finish(bigint,uuid,boolean,text) to demobank_meetings;
