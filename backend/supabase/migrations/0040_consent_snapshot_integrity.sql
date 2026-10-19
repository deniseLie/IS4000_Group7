-- Requests, grants and their bounds use the same statement snapshot during concurrent decisions.
create or replace function public.customer_read_consent_records(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); requests jsonb; grants jsonb;
  checked_at timestamptz:=clock_timestamp(); request_count bigint; grant_count bigint;
begin
  with request_rows as materialized(
    select r.request_id,r.purpose,r.recipient,r.categories,r.grant_expires_at,r.created_at,a.full_name recipient_name,
      case when r.status='pending' and r.grant_expires_at<=checked_at then 'expired' else r.status end status,
      case when c.type='consent.request' then c.case_id end case_id
    from consent.requests r left join rm.advisors a on a.advisor_id::text=r.recipient
      left join actions.commands c on c.command_id=r.command_id and c.customer_id=cid where r.customer_id=cid),
  grant_rows as materialized(
    select g.grant_id,g.purpose,g.recipient,g.categories,g.request_id,g.granted_at,g.expires_at,g.revoked_at,a.full_name recipient_name,
      g.revoked_at is null and g.granted_at<=checked_at and (g.expires_at is null or g.expires_at>checked_at) active
    from consent.grants g left join rm.advisors a on a.advisor_id::text=g.recipient where g.customer_id=cid)
  select (select count(*) from request_rows),(select count(*) from grant_rows),
    (select coalesce(jsonb_agg(to_jsonb(r) order by (r.status='pending') desc,r.created_at desc,r.request_id),'[]') from(
      select * from request_rows order by (status='pending') desc,created_at desc,request_id limit 200)r),
    (select coalesce(jsonb_agg(to_jsonb(g) order by g.active desc,(g.revoked_at is null) desc,g.granted_at desc,g.grant_id),'[]') from(
      select * from grant_rows order by active desc,(revoked_at is null) desc,granted_at desc,grant_id limit 200)g)
    into request_count,grant_count,requests,grants;
  return banking.customer_result(cid,'consent.records','consent.grants',jsonb_build_object('requests',requests,'grants',grants,
    'requests_total',request_count,'grants_total',grant_count));
end $$;

-- A legacy grant may name an arbitrary recipient; it cannot reveal an unassigned advisor's metadata.
create or replace function actions.consent_revoke_target(p_target jsonb,p_customer_id bigint)
returns jsonb language plpgsql volatile security definer set search_path='' set timezone='UTC'
as $$
declare target jsonb:=actions.normalise_consent_revoke(p_target); v_grant consent.grants; recipient_name text;
begin
  select * into v_grant from consent.grants where grant_id=(target->>'grant_id')::uuid and customer_id=p_customer_id;
  if not found then raise exception 'consent grant is outside the customer scope' using errcode='42501'; end if;
  select full_name into recipient_name from rm.advisors a where a.advisor_id::text=v_grant.recipient
    and exists(select 1 from rm.assignments r where r.customer_id=p_customer_id and r.advisor_id=a.advisor_id);
  return target||jsonb_build_object('purpose',v_grant.purpose,'recipient',v_grant.recipient,'recipient_name',recipient_name,
    'categories',(select jsonb_agg(c order by c) from unnest(v_grant.categories)c),'expires_at',v_grant.expires_at);
end $$;
