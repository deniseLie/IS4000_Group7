-- Active/unrevoked grants and pending requests remain discoverable as earlier ones are settled.
-- Counts make the bounded history explicit instead of implying that every record was returned.
create or replace function public.customer_read_consent_records(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); requests jsonb; grants jsonb;
  checked_at timestamptz:=clock_timestamp(); request_count bigint; grant_count bigint;
begin
  with owned as materialized(
    select r.request_id,r.purpose,r.recipient,r.categories,r.grant_expires_at,r.created_at,a.full_name recipient_name,
      case when r.status='pending' and r.grant_expires_at<=checked_at then 'expired' else r.status end status,
      case when c.type='consent.request' then c.case_id end case_id
    from consent.requests r left join rm.advisors a on a.advisor_id::text=r.recipient
      left join actions.commands c on c.command_id=r.command_id and c.customer_id=cid where r.customer_id=cid)
  select (select count(*) from owned),(select coalesce(jsonb_agg(to_jsonb(r) order by (r.status='pending') desc,r.created_at desc,r.request_id),'[]')
    from(select * from owned order by (status='pending') desc,created_at desc,request_id limit 200)r) into request_count,requests;
  with owned as materialized(
    select g.grant_id,g.purpose,g.recipient,g.categories,g.request_id,g.granted_at,g.expires_at,g.revoked_at,a.full_name recipient_name,
      g.revoked_at is null and g.granted_at<=checked_at and (g.expires_at is null or g.expires_at>checked_at) active
    from consent.grants g left join rm.advisors a on a.advisor_id::text=g.recipient where g.customer_id=cid)
  select (select count(*) from owned),(select coalesce(jsonb_agg(to_jsonb(g) order by g.active desc,(g.revoked_at is null) desc,g.granted_at desc,g.grant_id),'[]')
    from(select * from owned order by active desc,(revoked_at is null) desc,granted_at desc,grant_id limit 200)g) into grant_count,grants;
  return banking.customer_result(cid,'consent.records','consent.grants',jsonb_build_object('requests',requests,'grants',grants,
    'requests_total',request_count,'grants_total',grant_count));
end $$;
