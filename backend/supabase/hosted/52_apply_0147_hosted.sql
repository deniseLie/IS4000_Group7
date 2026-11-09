-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0147$
declare previous supabase_migrations.schema_migrations; script text:=$source_0147$-- Released source SHA256: 44b15b623c80b0a156e87d42dd5a90b747983f5582c4685bc85cf930986699d4
-- Consent changes are instant and in the app (8 Nov 2026). Synthetic data only.
-- Removes the two requests that went to the Data protection team, which had no screen and worked them from the SQL
-- editor: Stop using my data (consent_withdrawal, 0146) and Delete all my data (data_deletion, 0113). What she shares is
-- now only what she changes herself, at once: the Data sharing switches for transaction and chat insights, and I agree /
-- Withdraw on each meeting for its transcript (0122). docs/pdpa-baseline.md has the rules this follows.
--   1. Open tickets of the two categories are closed with a note. A category no ticket uses is deleted; one that old
--      tickets still use stops being raisable, so the form and the RM's service request (both require customer_raisable)
--      refuse it and the old tickets still show.
--   2. Dropped: both target triggers, support.data_deletion_target, support.business_days_after (nothing else uses it),
--      support.add_internal_note, consent.withdraw_by_request, consent.recording_opted_out and consent.recording_opt_outs.
--   3. consent.after_appointment is 0109's again: every confirmed meeting is under the recording notice.
--   4. end_reason 'withdrawn' (0146) is refused again, unless a grant already ended with it.
-- Kept: consent.revoked v2, the plain-words notice the switches send.

-- 1. Requests ------------------------------------------------------------------------------------------------------------
do $$
declare t support.tickets;
begin
  for t in select * from support.tickets where category in ('consent_withdrawal','data_deletion') and status not in ('resolved','closed') loop
    perform support.move_ticket(t.ticket_id,'closed',case t.category
      when 'consent_withdrawal' then 'We no longer take this request. Change what your relationship manager sees on Data sharing, and whether a meeting''s transcript is used on that meeting. This request changed nothing.'
      else 'We no longer take this request. Nothing was deleted by it. Change what your relationship manager sees on Data sharing.' end);
  end loop;
end $$;
delete from support.request_categories c where c.category in ('consent_withdrawal','data_deletion')
  and not exists(select 1 from support.tickets t where t.category=c.category);
update support.request_categories set customer_raisable=false where category in ('consent_withdrawal','data_deletion');

drop trigger consent_withdrawal_target on support.tickets;
drop trigger data_deletion_target on support.tickets;
drop function support.data_deletion_target();
drop function support.business_days_after(timestamptz,int);
drop function support.add_internal_note(text,text);

-- 2-3. Recording is under the notice at every confirmed meeting again ---------------------------------------------------
create or replace function consent.after_appointment() returns trigger language plpgsql security definer set search_path='' as $$
declare g uuid;
begin
  if new.status='confirmed' and (tg_op='INSERT' or old.status is distinct from 'confirmed') and not exists(select 1 from consent.grants x
      where x.customer_id=new.customer_id and x.purpose='rm_brief' and x.recipient=new.advisor_id::text and 'meeting_recording'=any(x.categories)
        and x.revoked_at is null and (x.expires_at is null or x.expires_at>clock_timestamp())) then
    insert into consent.grants(customer_id,purpose,recipient,categories,granted_at,scope,basis,notice_version)
      values(new.customer_id,'rm_brief',new.advisor_id::text,array['meeting_recording'],clock_timestamp(),'{}','notice',new.recording_notice_version)
      returning grant_id into g;
    perform audit.record('consent','consent.noticed','success','consent.grants',g::text,
      jsonb_build_object('category','meeting_recording','notice_version',new.recording_notice_version,'meeting_id',new.appointment_id),p_customer_id=>new.customer_id);
  end if;
  if tg_op='UPDATE' and new.status='confirmed' and new.ends_at is distinct from old.ends_at then
    update consent.grants set expires_at=new.ends_at+interval '30 days' where until_meeting_id=new.appointment_id and revoked_at is null;
  end if;
  if tg_op='UPDATE' and new.status='cancelled' and old.status<>'cancelled' then
    perform consent.close_meeting_grants(new.appointment_id,'meeting_cancelled');
  end if;
  return null;
end $$;

drop function consent.withdraw_by_request(text,text);
drop function consent.recording_opted_out(bigint);
drop table consent.recording_opt_outs;

-- 4. End reasons ----------------------------------------------------------------------------------------------------------
do $$
begin
  if not exists(select 1 from consent.grants where end_reason='withdrawn') then
    alter table consent.grants drop constraint grants_end_reason_check;
    alter table consent.grants add constraint grants_end_reason_check
      check (end_reason in ('revoked','replaced','meeting_closed','meeting_cancelled','request_closed'));
  end if;
end $$;
$source_0147$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0147';
  if found then
    if previous.name is distinct from 'instant_consent_only' or previous.statements is distinct from array[script] then
      raise exception 'migration 0147 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0147 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0147','instant_consent_only',array[script]);
  end if;
end $apply_0147$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
