-- Meeting transcripts: one standing yes from the client, and no acknowledgement from the RM (8 Nov 2026).
--   1. meetings.standing_transcript_consent: the client agrees once, on Data sharing › Meeting recording, that a transcript
--      of her meetings with her relationship manager may be used to write the minutes. She can switch it off there at any
--      time; until then she is not asked again on each meeting. She can still withdraw it on one meeting (revoke_recording).
--   2. meetings.recording_allowed no longer waits for the RM to read the notice: the bank records the call, so only the
--      client's agreement counts (this meeting's "I agree", or the standing yes).
-- Audit: consent.transcript_standing_on / consent.transcript_standing_off; ids and codes only.

create table meetings.standing_transcript_consent (
  customer_id bigint primary key references banking.customer_profiles(customer_id) on delete cascade,
  agreed_by   uuid not null references auth.users,
  notice_id   uuid not null references meetings.notice_versions,
  agreed_at   timestamptz not null default clock_timestamp()
);
alter table meetings.standing_transcript_consent enable row level security;
revoke all on meetings.standing_transcript_consent from public,anon,authenticated;

create function meetings.caller_customer() returns bigint language sql stable security definer set search_path='' as $$
  select customer_id from banking.customer_profiles where auth_user_id=auth.uid()
$$;
revoke all on function meetings.caller_customer() from public,anon,authenticated;

create function public.meeting_transcript_standing() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare c bigint:=meetings.caller_customer(); s meetings.standing_transcript_consent;
begin
  if auth.uid() is null or c is null then raise exception 'sign in as a client' using errcode='42501'; end if;
  select * into s from meetings.standing_transcript_consent where customer_id=c;
  return jsonb_build_object('on',found,'agreed_at',s.agreed_at,
    'notice',(select body from meetings.notice_versions where active order by version desc limit 1));
end $$;

create function public.meeting_set_transcript_standing(p_on boolean) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare c bigint:=meetings.caller_customer(); n uuid;
begin
  if auth.uid() is null or auth.jwt()->>'role' is distinct from 'authenticated' or c is null then raise exception 'sign in as a client' using errcode='42501'; end if;
  if p_on is null then raise exception 'choose on or off' using errcode='22023'; end if;
  if p_on then
    select notice_id into n from meetings.notice_versions where active order by version desc limit 1;
    if n is null then raise exception 'the meeting notice is not available' using errcode='P1001'; end if;
    insert into meetings.standing_transcript_consent(customer_id,agreed_by,notice_id) values(c,auth.uid(),n)
      on conflict (customer_id) do update set agreed_by=excluded.agreed_by,notice_id=excluded.notice_id,agreed_at=clock_timestamp();
    perform audit.record('consent','consent.transcript_standing_on','success','meetings.standing_transcript_consent',c::text,
      jsonb_build_object('notice_id',n),p_customer_id=>c);
  else
    delete from meetings.standing_transcript_consent where customer_id=c;
    if found then perform audit.record('consent','consent.transcript_standing_off','success','meetings.standing_transcript_consent',c::text,
      '{}'::jsonb,p_customer_id=>c); end if;
  end if;
  return public.meeting_transcript_standing();
end $$;
revoke all on function public.meeting_transcript_standing(),public.meeting_set_transcript_standing(boolean) from public,anon;
grant execute on function public.meeting_transcript_standing(),public.meeting_set_transcript_standing(boolean) to authenticated;

create or replace function meetings.recording_allowed(p_meeting uuid) returns boolean
language plpgsql volatile security definer set search_path='' as $$
declare w meetings.workflows; people uuid[]; client uuid; customer bigint;
begin
  w:=meetings.require_current(p_meeting); people:=meetings.participants(p_meeting);
  select c.auth_user_id,a.customer_id into client,customer from rm.appointments a join banking.customer_profiles c using(customer_id)
    where a.appointment_id=p_meeting;
  return cardinality(people)=2 and array_position(people,null) is null and client is not null
    and exists(select 1 from meetings.notice_versions where notice_id=w.notice_id and active)
    and (exists(select 1 from meetings.notice_acknowledgements n where n.meeting_id=p_meeting and n.participant=client
               and n.revision=w.revision and n.notice_id=w.notice_id)
         or exists(select 1 from meetings.standing_transcript_consent s where s.customer_id=customer))
    and not exists(select 1 from meetings.commands where meeting_id=p_meeting and operation='revoke_recording');
end $$;
