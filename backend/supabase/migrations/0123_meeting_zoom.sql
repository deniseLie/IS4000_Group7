
create function meetings.read_audit(p_customer bigint,p_resource text,p_id text,p_tool text) returns void
language plpgsql volatile security definer set search_path='' as $$
begin
 if auth.uid() is null then raise exception 'a verified caller is required for meeting reads' using errcode='42501';end if;
 perform audit.record('data','data.read','success',p_resource,p_id,jsonb_build_object('tool',p_tool),
  p_actor_type=>case when auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then 'staff' else 'customer' end,
  p_actor_id=>auth.uid()::text,p_customer_id=>p_customer);
end $$;
revoke all on function meetings.read_audit(bigint,text,text,text) from public,anon,authenticated,demobank_meetings,demobank_meeting_ingress;
-- Meeting-owned scheduling projections and Zoom links over the existing scheduling ledger.
create function public.meeting_identity() returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=case when auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then rm.current_advisor() end;customer bigint:=banking.my_customer_id();
begin
 if auth.uid() is null or (customer is null and advisor is null) then raise exception 'a linked client or RM login is required' using errcode='42501';end if;
 perform meetings.read_audit(customer,'meetings',auth.uid()::text,'meeting.session');
 return jsonb_build_object('customer_id',customer,'advisor_id',advisor,
 'current_advisor',(select advisor_id from rm.assignments where customer_id=customer and ended_on is null),
 'clients',(select coalesce(jsonb_agg(jsonb_build_object('customer_id',p.customer_id,'name',rm.display_name(p.full_name))),'[]')
 from banking.customer_profiles p where advisor is not null and rm.in_book(advisor,p.customer_id)),
 'topics',rm.meeting_topics(),'notice',(select jsonb_build_object('notice_id',notice_id,'version',version,'body',body) from meetings.notice_versions where active order by version desc limit 1));
end $$;
create function public.meeting_slots(p_customer_id bigint) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=case when auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then rm.current_advisor() end;assigned uuid;
begin
 select advisor_id into assigned from rm.assignments where customer_id=p_customer_id and ended_on is null;
 if p_customer_id is distinct from banking.my_customer_id() and (advisor is null or not rm.in_book(advisor,p_customer_id)) then
 raise exception 'client is outside this login scope' using errcode='42501';end if;
 perform meetings.read_audit(p_customer_id,'meetings',auth.uid()::text,'meeting.slots');
 return jsonb_build_object('slots',(select coalesce(jsonb_agg(jsonb_build_object('starts_at',starts_at,'label',rm.sgt(starts_at))),'[]') from
 (select s.starts_at from rm.free_slots(assigned,rm.today()+1,rm.today()+40,400) s where rm.slot_problem(assigned,p_customer_id,s.starts_at,null) is null order by s.starts_at limit 20) x));
end $$;
create function public.meeting_rm_propose(p_customer_id bigint,p_starts_at timestamptz,p_purpose text,p_topics text[],p_command_id uuid)
returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare advisor uuid:=case when auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then rm.current_advisor() end;a rm.appointments;r rm.meeting_requests;problem text;
begin
 if advisor is null or not rm.in_book(advisor,p_customer_id) then raise exception 'client is outside your current RM book' using errcode='42501';end if;
 if p_command_id is null then raise exception 'command id is required' using errcode='22023';end if;
 perform rm.require_text(p_purpose,200,'meeting purpose');
 if not rm.valid_topics(p_topics) or cardinality(p_topics) not between 1 and 5 then raise exception 'choose 1-5 valid topics' using errcode='22023';end if;
 perform pg_advisory_xact_lock(hashtext('rm.appointments'),hashtext(advisor::text));
 select * into r from rm.meeting_requests where request_id=p_command_id;
 if found then
  if r.customer_id<>p_customer_id or r.advisor_id<>advisor or r.purpose<>p_purpose or r.topics<>p_topics or r.preferred_times<>array[p_starts_at]
  then raise exception 'command id was used for a different proposal' using errcode='23505';end if;
  return rm.meeting_request_json(r);
 end if;
 problem:=rm.slot_problem(advisor,p_customer_id,p_starts_at,null);
 if problem is not null then raise exception 'time cannot be proposed: %',rm.slot_message(problem) using errcode='P1001';end if;
 insert into rm.appointments(customer_id,advisor_id,starts_at,ends_at,format,status,hold_expires_at,purpose,topics)
 values(p_customer_id,advisor,p_starts_at,p_starts_at+interval '30 minutes','video','held',
 least(p_starts_at-interval '1 hour',rm.next_business_day(rm.next_business_day(clock_timestamp()))),p_purpose,p_topics) returning * into a;
 insert into rm.meeting_requests(request_id,customer_id,advisor_id,purpose,topics,format,preferred_times,status,respond_by,appointment_id,decision_id,decided_at)
 values(p_command_id,p_customer_id,advisor,p_purpose,p_topics,'video',array[p_starts_at],'countered',a.hold_expires_at,a.appointment_id,p_command_id,clock_timestamp()) returning * into r;
 perform rm.notify_meeting(p_customer_id,'meeting.countered',jsonb_build_object('when',rm.sgt(a.starts_at),'until',rm.sgt(a.hold_expires_at)),'hybrid_proposal:'||p_command_id);
 return rm.meeting_request_json(r);
end $$;
create function public.meeting_appointment(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare a rm.appointments;advisor uuid:=case when auth.jwt()#>>'{app_metadata,demobank_role}'='rm' then rm.current_advisor() end;
begin
 select * into a from rm.appointments where appointment_id=p_meeting_id;
 if not found or (a.customer_id is distinct from banking.my_customer_id() and (advisor is null or advisor<>a.advisor_id or not rm.in_book(advisor,a.customer_id)))
 then raise exception 'appointment is outside your scope' using errcode='42501';end if;
 perform meetings.read_audit(a.customer_id,'rm.appointments',p_meeting_id::text,'meeting.appointment');
 return jsonb_build_object('meeting_id',a.appointment_id,'customer_id',a.customer_id,'starts_at',a.starts_at,'ends_at',a.ends_at,
 'status',a.status,'format',a.format,'purpose',a.purpose,'topics',a.topics,'advisor_id',a.advisor_id);
end $$;
create table meetings.zoom_links(meeting_id uuid primary key references rm.appointments,provider_id bigint unique not null,join_url text not null,
 starts_at timestamptz not null,created_at timestamptz not null default clock_timestamp());
alter table meetings.zoom_links enable row level security;
revoke all on meetings.zoom_links from public,anon,authenticated,demobank_meetings;
create function public.meeting_link_save(p_meeting_id uuid,p_provider_id bigint,p_join_url text,p_starts_at timestamptz) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;a rm.appointments;l meetings.zoom_links;
begin
 w:=meetings.require_rm(p_meeting_id);select * into a from rm.appointments where appointment_id=p_meeting_id;
 if a.starts_at is distinct from p_starts_at or p_provider_id is null or p_join_url!~'^https://([a-zA-Z0-9-]+\.)?zoom\.us/j/[0-9]+' then
 raise exception 'Zoom link must match the current scheduled meeting and official join domain' using errcode='22023';end if;
 select * into l from meetings.zoom_links where meeting_id=p_meeting_id;
 if found and (l.provider_id<>p_provider_id or l.join_url<>p_join_url or l.starts_at<>p_starts_at) then raise exception 'Zoom link already exists; reconcile provider reschedule explicitly' using errcode='23505';end if;
 insert into meetings.zoom_links values(p_meeting_id,p_provider_id,p_join_url,p_starts_at,clock_timestamp()) on conflict do nothing;
 return jsonb_build_object('linked',true,'meeting_id',p_meeting_id);
end $$;
create function public.meeting_link(p_meeting_id uuid) returns jsonb language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows;l meetings.zoom_links;
begin
 w:=meetings.require_participant(p_meeting_id);
 perform meetings.read_audit((select customer_id from rm.appointments where appointment_id=p_meeting_id),'rm.appointments',p_meeting_id::text,'meeting.zoom_link');
 select * into l from meetings.zoom_links where meeting_id=p_meeting_id;
 if not found then return null;end if;
 if l.starts_at is distinct from(select starts_at from rm.appointments where appointment_id=p_meeting_id) then raise exception 'Zoom link is stale after reschedule; reconcile before joining' using errcode='P1001';end if;
 return jsonb_build_object('join_url',l.join_url,'provider_id',l.provider_id);
end $$;
revoke all on function public.meeting_identity(),public.meeting_slots(bigint),
 public.meeting_rm_propose(bigint,timestamptz,text,text[],uuid),public.meeting_appointment(uuid),
 public.meeting_link_save(uuid,bigint,text,timestamptz),public.meeting_link(uuid) from public,anon;
grant execute on function public.meeting_identity(),public.meeting_slots(bigint),
 public.meeting_rm_propose(bigint,timestamptz,text,text[],uuid),public.meeting_appointment(uuid),
 public.meeting_link_save(uuid,bigint,text,timestamptz),public.meeting_link(uuid) to authenticated;
create table meetings.zoom_creation_intents(meeting_id uuid primary key references rm.appointments,reserved_at timestamptz not null default clock_timestamp());
alter table meetings.zoom_creation_intents enable row level security;
revoke all on meetings.zoom_creation_intents from public,anon,authenticated,demobank_meetings;
create function public.meeting_zoom_reserve(p_meeting_id uuid) returns void language plpgsql volatile security definer set search_path='' as $$
begin
 perform meetings.require_rm(p_meeting_id);
 if exists(select 1 from meetings.zoom_creation_intents where meeting_id=p_meeting_id) then
 raise exception 'a Zoom creation was already attempted; inspect/reconcile its provider outcome before retrying to avoid duplicate meetings' using errcode='23505';end if;
 insert into meetings.zoom_creation_intents(meeting_id) values(p_meeting_id);
end $$;
revoke all on function public.meeting_zoom_reserve(uuid) from public,anon;
grant execute on function public.meeting_zoom_reserve(uuid) to authenticated;

insert into meetings.notice_versions(notice_id,version,body,maximum_days,active)
values(gen_random_uuid(),'v1','DemoBank synthetic meeting notice: a transcript may be used to draft minutes and follow-up actions. Both participants must read this notice before transcript intake. You may withdraw transcript access at any time; raw transcripts are then purged within seven days. Approved minutes are retained. Otherwise raw transcripts are purged thirty days after minutes approval. Reading this notice does not start Zoom recording.',30,true);
