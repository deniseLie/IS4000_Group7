-- Reject malformed preference records rather than treating NULL channel membership as an opt-in.
alter table notify.preferences add constraint notification_channels_valid check(
  channels <@ array['in_app','email']::text[] and array_position(channels,null) is null);
alter table notify.preferences add constraint notification_quiet_hours_valid check(
  (quiet_start is null and quiet_end is null) or
  (quiet_start is not null and quiet_end is not null and quiet_start<>quiet_end));

-- Clear a previous deferral before a new delivery attempt. A failed attempt must not report an old disposition.
create or replace function events.process_next_notification()
returns jsonb language plpgsql volatile security definer set search_path=''
as $$
declare event events.outbox; result jsonb; failure text;
begin
  select * into event from events.outbox where status='pending' and available_at<=clock_timestamp()
    order by available_at,event_id for update skip locked limit 1;
  if not found then return null; end if;
  update events.outbox set status='processing',attempts=attempts+1,last_error_code=null,disposition=null where event_id=event.event_id returning * into event;
  begin
    result:=notify.deliver(event);
    update events.outbox set status=case when result->>'disposition'='deferred' then 'pending' else 'done' end,
      disposition=result->>'disposition',available_at=coalesce((result->>'available_at')::timestamptz,available_at),
      attempts=case when result->>'disposition'='deferred' then attempts-1 else attempts end,
      processed_at=case when result->>'disposition'='deferred' then null else clock_timestamp() end where event_id=event.event_id;
    if result->>'disposition'<>'delivered' then
      perform audit.record('notification','notification.'||(result->>'disposition'),'success','events.outbox',event.event_id::text,
        jsonb_build_object('event_id',event.event_id),p_customer_id=>event.customer_id,p_actor_type=>'system',p_actor_id=>'notification_worker');
    end if;
  exception when sqlstate 'P2001' or sqlstate 'P2002' or sqlstate 'P2003' or sqlstate 'P2004' or sqlstate '22023' then
    get stacked diagnostics failure=returned_sqlstate;
    update events.outbox set status=case when attempts>=5 then 'failed' else 'pending' end,last_error_code=failure,
      available_at=clock_timestamp()+make_interval(secs=>least(300,power(2,event.attempts)::int)),
      processed_at=case when attempts>=5 then clock_timestamp() else null end where event_id=event.event_id;
    perform audit.record('notification','notification.failed','failed','events.outbox',event.event_id::text,
      jsonb_build_object('event_id',event.event_id,'error_code',failure,'attempts',event.attempts),
      p_customer_id=>event.customer_id,p_actor_type=>'system',p_actor_id=>'notification_worker');
    result:=jsonb_build_object('notification_id',null);
  end;
  return (select jsonb_build_object('event_id',e.event_id,'status',e.status,'attempts',e.attempts,
    'disposition',e.disposition,'error_code',e.last_error_code,'notification_id',result->'notification_id') from events.outbox e where e.event_id=event.event_id);
end $$;
