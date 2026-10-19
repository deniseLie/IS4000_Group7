-- Customer read RPCs preserve the user's JWT/RLS and an explicit customer_id predicate in every query.
create function banking.require_read_customer(p_customer_id bigint)
returns bigint language plpgsql stable security invoker set search_path=''
as $$
declare cid bigint:=banking.my_customer_id();
begin
  if auth.uid() is null or cid is null or cid is distinct from p_customer_id then
    raise exception 'customer read scope must match the current linked login' using errcode='42501';
  end if;
  return cid;
end $$;

create function banking.customer_result(p_customer_id bigint,p_tool text,p_resource text,p_data jsonb)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); call_id uuid:=gen_random_uuid(); count int;
begin
  count:=case when p_data is null or p_data='null' then 0 when jsonb_typeof(p_data)='array' then jsonb_array_length(p_data) else 1 end;
  if count>200 then raise exception 'customer read exceeds its 200-record bound' using errcode='22023'; end if;
  perform public.audit_record_batch(jsonb_build_array(jsonb_build_object('category','data','action','data.read','outcome','success',
    'resource_type',p_resource,'resource_id',call_id::text,'details',jsonb_build_object('tool',p_tool,'result_count',count))));
  return jsonb_build_object('customer_id',cid,'call_id',call_id,'as_of',clock_timestamp(),'data',p_data);
end $$;
revoke all on function banking.require_read_customer(bigint),banking.customer_result(bigint,text,text,jsonb) from public,anon;
grant execute on function banking.require_read_customer(bigint),banking.customer_result(bigint,text,text,jsonb) to authenticated;

create function public.customer_read_profile(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select jsonb_build_object('name',full_name,'preferred_language',preferred_language,'employment_status',employment_status) into data
    from banking.customer_profiles where customer_id=cid and auth_user_id=auth.uid();
  if data is null then raise exception 'linked customer profile was not returned under RLS' using errcode='42501'; end if;
  return banking.customer_result(cid,'customer.get_profile','banking.customer_profiles',data);
end $$;

create function public.customer_read_accounts(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.account_id),'[]') into data from(
    select 'acc_'||account_id account_id,account_type type,'****'||right(masked_account_number,4) number,currency,balance,status
      from banking.bank_accounts where customer_id=cid order by account_id limit 200)r;
  return banking.customer_result(cid,'customer.get_accounts','banking.bank_accounts',data);
end $$;

create function public.customer_read_cards(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.card_id),'[]') into data from(
    select 'card_'||card_id card_id,card_name name,network,last4,status,credit_limit "limit",outstanding_balance outstanding,
      minimum_payment_due minimum_payment,payment_due_date due_date
      from banking.credit_cards where customer_id=cid order by card_id limit 200)r;
  return banking.customer_result(cid,'customer.get_credit_cards','banking.credit_cards',data);
end $$;

create function public.customer_read_loans(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.loan_id),'[]') into data from(
    select 'loan_'||loan_id loan_id,loan_type type,original_amount principal,outstanding_amount outstanding,
      next_due_date next_payment,monthly_repayment next_amount,status
      from banking.loans where customer_id=cid order by loan_id limit 200)r;
  return banking.customer_result(cid,'customer.get_loans','banking.loans',data);
end $$;

create function public.customer_read_transactions(p_customer_id bigint,p_from date,p_to date,p_filters jsonb)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  if (p_from is not null and p_to is not null and p_from>p_to) or jsonb_typeof(p_filters) is distinct from 'object' then
    raise exception 'transaction reads require ordered dates and an object of filters' using errcode='22023';
  end if;
  if exists(select 1 from banking.transactions t where t.customer_id=cid and (
      (t.card_id is not null and not exists(select 1 from banking.credit_cards c where c.customer_id=cid and c.card_id=t.card_id)) or
      (t.account_id is not null and not exists(select 1 from banking.bank_accounts a where a.customer_id=cid and a.account_id=t.account_id)))) then
    raise exception 'a customer transaction references an unavailable owned account or card' using errcode='23514';
  end if;
  select coalesce(jsonb_agg(r.data order by r.txn_date desc,r.transaction_id desc),'[]') into data from(
    select t.txn_date,t.transaction_id,jsonb_build_object('txn_id','txn_'||t.transaction_id,
      'date',(t.txn_date at time zone 'Asia/Singapore')::date,'description',t.merchant,'amount',t.amount,'currency',t.currency,
      'status',t.status,'card_id',case when c.card_id is not null then 'card_'||c.card_id end,'card_last4',c.last4,
      'is_overseas',t.is_overseas,'failure_reason',t.failure_reason) data
    from banking.transactions t left join banking.credit_cards c on c.card_id=t.card_id and c.customer_id=cid
    where t.customer_id=cid and (p_from is null or t.txn_date>=p_from::timestamp at time zone 'Asia/Singapore')
      and (p_to is null or t.txn_date<(p_to+1)::timestamp at time zone 'Asia/Singapore')
      and (not(p_filters?'status') or t.status=p_filters->>'status')
      and (not(p_filters?'txn_id') or 'txn_'||t.transaction_id=p_filters->>'txn_id')
      and (not(p_filters?'card_id') or 'card_'||t.card_id=p_filters->>'card_id')
      and (not(p_filters?'currency') or t.currency=p_filters->>'currency')
      and (not(p_filters?'is_overseas') or t.is_overseas=(p_filters->>'is_overseas')::boolean)
    order by t.txn_date desc,t.transaction_id desc limit 200)r;
  return banking.customer_result(cid,'customer.get_transactions','banking.transactions',data);
end $$;

revoke all on function public.customer_read_profile(bigint),public.customer_read_accounts(bigint),public.customer_read_cards(bigint),
  public.customer_read_loans(bigint),public.customer_read_transactions(bigint,date,date,jsonb) from public,anon;
grant execute on function public.customer_read_profile(bigint),public.customer_read_accounts(bigint),public.customer_read_cards(bigint),
  public.customer_read_loans(bigint),public.customer_read_transactions(bigint,date,date,jsonb) to authenticated;
