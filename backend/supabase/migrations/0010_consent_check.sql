-- Read-only active consent checks. Customer scope is derived from the verified JWT and checked again here.
-- The invoker's RLS still applies. Grant/revoke writes must go through the confirmed Action Service.
create function consent.check_grant(p_customer_id bigint,p_purpose text,p_recipient uuid,p_categories text[])
returns table(granted boolean,grant_id uuid,expires_at timestamptz,checked_at timestamptz)
language plpgsql volatile security invoker set search_path=''
as $$
declare own_customer bigint := banking.my_customer_id();
begin
  if auth.uid() is null or own_customer is null or p_customer_id is distinct from own_customer then
    raise exception 'consent customer scope does not match the signed-in user' using errcode='42501';
  end if;
  if p_purpose is null or p_purpose !~ '^[a-z][a-z0-9_]{0,39}$' or p_recipient is null then
    raise exception 'consent requires a scope code and recipient UUID' using errcode='22023';
  end if;
  if p_categories is null or cardinality(p_categories) not between 1 and 20
      or exists(select 1 from unnest(p_categories) c where c is null or c !~ '^[a-z][a-z0-9_]{0,39}$')
      or (select count(distinct c) from unnest(p_categories) c)<>cardinality(p_categories) then
    raise exception 'consent categories must contain 1-20 unique scope codes' using errcode='22023';
  end if;
  checked_at:=clock_timestamp();
  select g.grant_id,g.expires_at into grant_id,expires_at
  from consent.grants g
  where g.customer_id=p_customer_id and g.purpose=p_purpose and g.recipient=p_recipient::text
    and g.categories @> p_categories and g.granted_at<=checked_at
    and g.revoked_at is null and (g.expires_at is null or g.expires_at>checked_at)
  order by g.granted_at desc,g.grant_id limit 1;
  granted:=found;
  return next;
end $$;

create function public.consent_check(p_customer_id bigint,p_purpose text,p_recipient uuid,p_categories text[])
returns table(granted boolean,grant_id uuid,expires_at timestamptz,checked_at timestamptz)
language sql volatile security invoker set search_path=''
as $$ select * from consent.check_grant(p_customer_id,p_purpose,p_recipient,p_categories) $$;
revoke all on function consent.check_grant(bigint,text,uuid,text[]),public.consent_check(bigint,text,uuid,text[]) from public,anon;
grant execute on function consent.check_grant(bigint,text,uuid,text[]),public.consent_check(bigint,text,uuid,text[]) to authenticated;
