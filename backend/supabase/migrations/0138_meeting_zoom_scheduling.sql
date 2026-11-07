-- Automatic own-user Zoom provisioning consumes only a fenced scheduling event and that meeting's agreed agenda.
alter table meetings.zoom_creation_intents add column revision int not null default 1;
alter table meetings.zoom_creation_intents drop constraint zoom_creation_intents_pkey;
alter table meetings.zoom_creation_intents add primary key(meeting_id,revision);
alter table meetings.zoom_links add column cancelled_at timestamptz;

create or replace function public.meeting_zoom_reserve(p_meeting_id uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;
begin
 w:=meetings.require_rm(p_meeting_id);
 insert into meetings.zoom_creation_intents(meeting_id,revision) values(p_meeting_id,w.revision) on conflict do nothing;
 if not found then raise exception 'Zoom creation was already reserved for this meeting version; reconcile the provider result before retrying' using errcode='23505';end if;
end $$;

create function meetings.zoom_schedule_context(p_event bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;a rm.appointments;w meetings.workflows;b meetings.zoom_bindings;l meetings.zoom_links;notice uuid;by_role text;
begin
 e:=meetings.require_claim(p_event,p_token);
 if e.event_type not in ('meeting.scheduled','meeting.rescheduled') then raise exception 'claim is not a scheduling event' using errcode='42501';end if;
 select * into a from rm.appointments where appointment_id=e.meeting_id;
 if a.status<>'confirmed' or a.format<>'video' then return null;end if;
 if a.starts_at<=clock_timestamp() then
  if exists(select 1 from meetings.workflows where meeting_id=e.meeting_id and state in ('in_progress','ended')) then return null;end if;
  raise exception 'the Zoom scheduling deadline was missed; agree a new future meeting time before creating a link' using errcode='P1001';
 end if;
 if not rm.in_book(a.advisor_id,a.customer_id) then raise exception 'meeting is no longer in the assigned RM book' using errcode='42501';end if;
 select * into w from meetings.workflows where meeting_id=e.meeting_id;
 if not found then
  by_role:=e.payload->>'by';
  if by_role is null or by_role not in ('rm','client') then raise exception 'open this confirmed meeting workspace and agree its intended outcome before automatic provisioning' using errcode='P1001';end if;
  perform rm.require_text(a.purpose,200,'agreed meeting purpose');perform conversation.require_redacted(a.purpose);
  select notice_id into notice from meetings.notice_versions where active order by version desc limit 1;
  if notice is null then raise exception 'activate a recording notice before opening a meeting workflow' using errcode='P1001';end if;
  -- The requested purpose is the initial intended outcome; no claim that the RM approved a new outcome or read the notice.
  insert into meetings.workflows(meeting_id,revision,consent_epoch,schedule_hash,notice_id,initiator,purpose,outcome,state)
  values(e.meeting_id,1,1,meetings.schedule_hash(e.meeting_id),notice,by_role,a.purpose,a.purpose,'scheduled') returning * into w;
 end if;
 w:=meetings.require_current(e.meeting_id);
 if w.state<>'scheduled' then raise exception 'do not provision or move a call that has already started' using errcode='P1001';end if;
 select * into l from meetings.zoom_links where meeting_id=e.meeting_id;
 select * into b from meetings.zoom_bindings where meeting_id=e.meeting_id order by revision desc limit 1;
 if b.binding_id is not null and b.revision<>w.revision and
  (exists(select 1 from meetings.zoom_fetch_jobs where binding_id=b.binding_id) or exists(select 1 from meetings.provider_call_times where binding_id=b.binding_id)) then
  raise exception 'this Zoom occurrence already has call evidence; preserve it and schedule a new meeting instead of reusing the occurrence' using errcode='P1001';end if;
 return jsonb_build_object('meeting_id',e.meeting_id,'starts_at',a.starts_at,'ends_at',a.ends_at,'purpose',w.purpose,'topics',a.topics,
  'provider_id',l.provider_id,'account_id',b.account_id,'host_id',b.host_id,'original_uuid',b.original_uuid,
  'linked_current',coalesce(b.revision=w.revision and b.schedule_hash=w.schedule_hash and l.starts_at=a.starts_at and l.cancelled_at is null,false));
end $$;

create function meetings.reserve_zoom_job(p_event bigint,p_token uuid) returns boolean
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;w meetings.workflows;
begin
 e:=meetings.require_claim(p_event,p_token);perform meetings.zoom_schedule_context(p_event,p_token);w:=meetings.require_current(e.meeting_id);
 insert into meetings.zoom_creation_intents(meeting_id,revision) values(e.meeting_id,w.revision) on conflict do nothing;
 return found;
end $$;

create function meetings.release_refused_zoom(p_event bigint,p_token uuid) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;w meetings.workflows;
begin
 e:=meetings.require_claim(p_event,p_token);perform meetings.zoom_schedule_context(p_event,p_token);w:=meetings.require_current(e.meeting_id);
 if exists(select 1 from meetings.zoom_bindings where meeting_id=e.meeting_id and revision=w.revision) then
  raise exception 'a verified Zoom binding already exists; its creation reservation cannot be removed' using errcode='P1001';end if;
 delete from meetings.zoom_creation_intents where meeting_id=e.meeting_id and revision=w.revision;
 if not found then raise exception 'there is no current refused Zoom reservation to release' using errcode='P1001';end if;
end $$;

create function public.meeting_request_zoom(p_meeting_id uuid,p_revision int,p_command_id uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;job bigint;receipt jsonb;params jsonb:=jsonb_build_array(p_revision);
begin
 w:=meetings.require_rm(p_meeting_id);
 receipt:=meetings.replay(p_command_id,p_meeting_id,'request_zoom_link',params);if receipt is not null then return receipt;end if;
 if p_revision is distinct from w.revision or w.state<>'scheduled' or not exists(select 1 from rm.appointments
  where appointment_id=p_meeting_id and starts_at>clock_timestamp() and format='video') then
  raise exception 'request a Zoom link only for the current future video meeting' using errcode='P1001';end if;
 select event_id into job from meetings.events where meeting_id=p_meeting_id and event_type in ('meeting.scheduled','meeting.rescheduled')
  and status in ('pending','processing') order by event_id limit 1;
 if job is null then job:=meetings.enqueue_v2('meeting.scheduled',p_meeting_id,jsonb_build_object('revision',w.revision),'zoom_request:'||p_command_id);end if;
 return meetings.remember(p_command_id,p_meeting_id,'request_zoom_link',params,jsonb_build_object('status','queued','event_id',job));
end $$;

create function meetings.commit_scheduled_zoom(p_event bigint,p_token uuid,p_provider bigint,p_url text,p_start timestamptz,p_duration int,
 p_account text,p_host text,p_uuid text) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;a rm.appointments;w meetings.workflows;b meetings.zoom_bindings;l meetings.zoom_links;
begin
 e:=meetings.require_claim(p_event,p_token);perform meetings.zoom_schedule_context(p_event,p_token);
 w:=meetings.require_current(e.meeting_id);select * into a from rm.appointments where appointment_id=e.meeting_id;
 if p_provider is null or p_provider<=0 or p_start is distinct from a.starts_at or p_duration is distinct from (extract(epoch from a.ends_at-a.starts_at)/60)::int
  or p_url is null or p_url!~'^https://([a-zA-Z0-9-]+\.)?zoom\.us/j/[0-9]+'
  or p_account is null or length(p_account) not between 1 and 128 or p_host is null or length(p_host) not between 1 and 128
  or p_uuid is null or length(p_uuid) not between 1 and 200 then raise exception 'verified Zoom outcome does not match the current scheduled meeting' using errcode='22023';end if;
 select * into l from meetings.zoom_links where meeting_id=e.meeting_id;
 if found and l.provider_id<>p_provider then raise exception 'another Zoom meeting is already linked; reconcile it before replacing the link' using errcode='23505';end if;
 select * into b from meetings.zoom_bindings where meeting_id=e.meeting_id order by revision desc limit 1;
 if b.binding_id is not null then
  if b.provider_id<>p_provider or b.account_id<>p_account or b.host_id<>p_host then raise exception 'the verified Zoom owner or meeting changed during scheduling' using errcode='23505';end if;
  update meetings.zoom_bindings set revision=w.revision,schedule_hash=w.schedule_hash,original_uuid=p_uuid where binding_id=b.binding_id;
 else insert into meetings.zoom_bindings values(gen_random_uuid(),e.meeting_id,w.revision,w.schedule_hash,p_provider,p_account,p_host,p_uuid);end if;
 insert into meetings.zoom_links(meeting_id,provider_id,join_url,starts_at) values(e.meeting_id,p_provider,p_url,p_start)
 on conflict(meeting_id) do update set join_url=excluded.join_url,starts_at=excluded.starts_at,cancelled_at=null;
 perform rm.notify_meeting(a.customer_id,'meeting.zoom_ready',jsonb_build_object('when',rm.sgt(a.starts_at),'join_url',p_url),
  'zoom_link:'||e.meeting_id||':'||w.revision);
 perform audit.record('action','action.meeting_zoom_linked','success','rm.appointments',e.meeting_id::text,
  jsonb_build_object('revision',w.revision),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>a.customer_id);
end $$;

create function meetings.zoom_cancel_context(p_event bigint,p_token uuid) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;a rm.appointments;b meetings.zoom_bindings;l meetings.zoom_links;
begin
 e:=meetings.require_claim(p_event,p_token);
 if e.event_type<>'meeting.cancelled' then raise exception 'claim is not an approved cancellation event' using errcode='42501';end if;
 select * into a from rm.appointments where appointment_id=e.meeting_id;
 if a.status<>'cancelled' then raise exception 'the appointment is not cancelled' using errcode='P1001';end if;
 select * into l from meetings.zoom_links where meeting_id=e.meeting_id and cancelled_at is null;
 if not found then return null;end if;
 select * into b from meetings.zoom_bindings where meeting_id=e.meeting_id order by revision desc limit 1;
 return jsonb_build_object('meeting_id',e.meeting_id,'provider_id',l.provider_id,'account_id',b.account_id,'host_id',b.host_id,'original_uuid',b.original_uuid);
end $$;
create function meetings.commit_zoom_cancellation(p_event bigint,p_token uuid,p_provider bigint,p_outcome text) returns void
language plpgsql volatile security definer set search_path='' as $$
declare e meetings.events;
begin
 e:=meetings.require_claim(p_event,p_token);perform meetings.zoom_cancel_context(p_event,p_token);
 if p_outcome is null or p_outcome not in ('deleted','already_absent') then raise exception 'unsupported Zoom cancellation outcome' using errcode='22023';end if;
 update meetings.zoom_links set cancelled_at=coalesce(cancelled_at,clock_timestamp()) where meeting_id=e.meeting_id and provider_id=p_provider;
 if not found then raise exception 'the cancelled provider meeting no longer matches its saved link' using errcode='P1001';end if;
 perform audit.record('action','action.meeting_zoom_cancelled','success','rm.appointments',e.meeting_id::text,
  jsonb_build_object('provider_outcome',p_outcome),p_actor_type=>'system',p_actor_id=>'meetings',p_customer_id=>e.customer_id);
end $$;

insert into notify.templates(template_key,version,locale,channel,title,body,is_service) values
 ('meeting.zoom_ready',1,'en','in_app','Your Zoom meeting link is ready',
  'Your meeting is scheduled for {when}. Join Zoom: {join_url}. Open My requests - Meetings to review the current agenda and recording notice.',true);

alter function public.meeting_link_save(uuid,bigint,text,timestamptz) set schema meetings;
alter function meetings.meeting_link_save(uuid,bigint,text,timestamptz) rename to link_save_before_notice;
revoke all on function meetings.link_save_before_notice(uuid,bigint,text,timestamptz) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
create function public.meeting_link_save(p_meeting_id uuid,p_provider_id bigint,p_join_url text,p_starts_at timestamptz) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare result jsonb;w meetings.workflows;a rm.appointments;
begin
 result:=meetings.link_save_before_notice(p_meeting_id,p_provider_id,p_join_url,p_starts_at);
 w:=meetings.require_rm(p_meeting_id);select * into a from rm.appointments where appointment_id=p_meeting_id;
 perform rm.notify_meeting(a.customer_id,'meeting.zoom_ready',jsonb_build_object('when',rm.sgt(a.starts_at),'join_url',p_join_url),
  'zoom_link:'||p_meeting_id||':'||w.revision);
 return result;
end $$;
revoke all on function public.meeting_link_save(uuid,bigint,text,timestamptz) from public,anon;
grant execute on function public.meeting_link_save(uuid,bigint,text,timestamptz) to authenticated;
revoke all on function meetings.zoom_schedule_context(bigint,uuid),meetings.reserve_zoom_job(bigint,uuid),
 meetings.release_refused_zoom(bigint,uuid),
 meetings.commit_scheduled_zoom(bigint,uuid,bigint,text,timestamptz,int,text,text,text),meetings.zoom_cancel_context(bigint,uuid),
 meetings.commit_zoom_cancellation(bigint,uuid,bigint,text) from public,anon,authenticated,demobank_meeting_ingress,demobank_meetings;
grant execute on function meetings.zoom_schedule_context(bigint,uuid),meetings.reserve_zoom_job(bigint,uuid),
 meetings.release_refused_zoom(bigint,uuid),
 meetings.commit_scheduled_zoom(bigint,uuid,bigint,text,timestamptz,int,text,text,text),meetings.zoom_cancel_context(bigint,uuid),
 meetings.commit_zoom_cancellation(bigint,uuid,bigint,text) to demobank_meetings;
revoke all on function public.meeting_request_zoom(uuid,int,uuid) from public,anon;
grant execute on function public.meeting_request_zoom(uuid,int,uuid) to authenticated;
