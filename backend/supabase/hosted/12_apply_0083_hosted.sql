-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0083$
declare previous supabase_migrations.schema_migrations; script text:=$source_0083$-- Released source SHA256: 8f790054f1562e04bdf1deb7ac9f62a5a9de5be92b25d3beb15e9d000473f0bc
-- Card and loan amounts get a recorded currency (code review follow-up, CR14). Until now cards and loans stored only the
-- numbers, so the assistant had to say their currency was unrecorded while the Overview showed SGD by default. Every
-- existing synthetic card and loan is in SGD. The reads below add the field; nothing else about them changes.

alter table banking.credit_cards add column currency char(3) not null default 'SGD' check (currency ~ '^[A-Z]{3}$');
alter table banking.loans add column currency char(3) not null default 'SGD' check (currency ~ '^[A-Z]{3}$');

create or replace function public.customer_read_cards(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.card_id),'[]') into data from(
    select 'card_'||card_id card_id,card_name name,network,last4,status,currency,credit_limit "limit",outstanding_balance outstanding,
      minimum_payment_due minimum_payment,payment_due_date due_date
      from banking.credit_cards where customer_id=cid order by card_id limit 200)r;
  return banking.customer_result(cid,'customer.get_credit_cards','banking.credit_cards',data);
end $$;

create or replace function public.customer_read_loans(p_customer_id bigint)
returns jsonb language plpgsql volatile security invoker set search_path=''
as $$
declare cid bigint:=banking.require_read_customer(p_customer_id); data jsonb;
begin
  select coalesce(jsonb_agg(to_jsonb(r) order by r.loan_id),'[]') into data from(
    select 'loan_'||loan_id loan_id,loan_type type,currency,original_amount principal,outstanding_amount outstanding,
      next_due_date next_payment,monthly_repayment next_amount,status
      from banking.loans where customer_id=cid order by loan_id limit 200)r;
  return banking.customer_result(cid,'customer.get_loans','banking.loans',data);
end $$;
$source_0083$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0083';
  if found then
    if previous.name is distinct from 'card_loan_currency' or previous.statements is distinct from array[script] then
      raise exception 'migration 0083 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0083 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0083','card_loan_currency',array[script]);
  end if;
end $apply_0083$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
