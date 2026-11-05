-- Consent v2, part 4 (RM copilot build plan phase 6d): who looked at what she shares.
-- 0111 is reserved for build plan 6c (transcript removal, meeting side); it is not built yet and nothing here depends on it.
--
-- public.my_consent_access_log: the signed-in client's own log of relationship-manager reads of her records, built only
-- from the audit events the RM reads already write (0089 rm.result, 0106 rm.gated, 0108 client_shared talking points,
-- rm.deny). Nothing new is recorded on the RM side and no RM function changes. Every row is ids, codes and times:
--   call_id      the read's call id (the audit event's resource id)
--   at           when it happened
--   advisor_id   which relationship manager (resolved through rm.staff_logins); null for a login that is not an RM's
--   relation     current_rm, former_rm or not_your_rm (her assignments today)
--   category     the consent.categories code: from the event (gated reads), chat_topics for topics she shared herself,
--                otherwise the always-shared category the tool reads
--   purpose      meeting_prep or client_view for gated reads (set by code on the RM side), else null
--   meeting_id   for meeting_prep only: her first confirmed meeting with that RM at or after the read, within 14 days
--                (derived here; the RM read does not record a meeting id)
--   outcome      read, not_shared (the RM saw only that she has not shared it) or refused (outside the RM's book)
--   basis        consent (a grant), client_shared (topics she shared herself), always_shared, or null when not read
--   grant_id     the grant a gated read used
-- Reads of the whole book (Today, review status, book facts) are not per client and are not in this log.
-- The read itself is audited for her (data.read, tool consent.access_log) like her other reads.
create function consent.always_shared_category(p_tool text) returns text language sql immutable set search_path='' as $$
  select case
    when p_tool in ('rm.client_profile','rm.handover_brief','rm.shared_summary','rm.consent_status','rm.match_clients') then 'profile_basics'
    when p_tool in ('rm.book_facts') then 'products'
    when p_tool in ('rm.review_status') then 'compliance_status'
    when p_tool in ('rm.talking_points','rm.point_status') then 'chat_topics'
    else 'requests_and_meetings' end
$$;

create function public.my_consent_access_log(p_customer_id bigint,p_limit int default 50) returns jsonb
language plpgsql volatile security definer set search_path='' as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); n int:=coalesce(p_limit,50); data jsonb; today date:=(clock_timestamp() at time zone 'Asia/Singapore')::date;
begin
  if n not between 1 and 200 then raise exception 'choose 1 to 200 rows' using errcode='22023'; end if;
  with e as (
    select e.event_id,e.occurred_at,e.action,e.outcome,e.resource_id,e.details,l.advisor_id
    from audit.events e left join rm.staff_logins l on l.auth_user_id::text=e.actor_id
    where e.customer_id=cid and e.actor_type='staff' and e.action in ('data.read','policy.rm_consent_gap','policy.rm_client_denied')
    order by e.event_id desc limit n),
  rows as (
    select e.event_id,jsonb_build_object(
      'call_id',case when e.action='policy.rm_client_denied' then null else e.resource_id end,
      'at',e.occurred_at,
      'advisor_id',e.advisor_id,
      'relation',case when e.advisor_id is null then 'not_your_rm'
        when exists(select 1 from rm.assignments a where a.customer_id=cid and a.advisor_id=e.advisor_id and a.ended_on is null and a.started_on<=today) then 'current_rm'
        when exists(select 1 from rm.assignments a where a.customer_id=cid and a.advisor_id=e.advisor_id) then 'former_rm'
        else 'not_your_rm' end,
      'tool',e.details->>'tool',
      'category',case when e.details ? 'category' then e.details->>'category' when e.details->>'basis'='client_shared' then 'chat_topics'
        when e.action='policy.rm_client_denied' then null else consent.always_shared_category(e.details->>'tool') end,
      'purpose',e.details->>'purpose',
      'meeting_id',case when e.details->>'purpose'='meeting_prep' then (select m.appointment_id from rm.appointments m
        where m.customer_id=cid and m.advisor_id=e.advisor_id and m.status='confirmed' and m.starts_at>=e.occurred_at
          and m.starts_at<e.occurred_at+interval '14 days' order by m.starts_at limit 1) end,
      'outcome',case e.action when 'policy.rm_consent_gap' then 'not_shared' when 'policy.rm_client_denied' then 'refused' else 'read' end,
      'basis',case when e.action<>'data.read' then null when e.details ? 'grant_id' then 'consent' when e.details->>'basis'='client_shared' then 'client_shared'
        else 'always_shared' end,
      'grant_id',e.details->>'grant_id') row from e)
  select coalesce(jsonb_agg(row order by event_id desc),'[]') into data from rows;
  return banking.customer_result(cid,'consent.access_log','audit.events',data);
end $$;
revoke all on function consent.always_shared_category(text),public.my_consent_access_log(bigint,int) from public,anon;
grant execute on function public.my_consent_access_log(bigint,int) to authenticated;
