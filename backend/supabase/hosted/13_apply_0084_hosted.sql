-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0084$
declare previous supabase_migrations.schema_migrations; script text:=$source_0084$-- Released source SHA256: c715eeaa48720f35cc1447a75c31a33ff0116e106d84d5863f07b61e857612fa
-- Michelle's two sample fees (0082) were both recorded under the merchant "DemoBank fee", and the client reads show
-- only the merchant, so neither she nor the assistant could tell the annual card fee from the transfer fee (CR15).
-- Give each its own name. Synthetic data only; amounts and dates are unchanged.
update banking.transactions set merchant='Telegraphic transfer fee' where transaction_id=100239 and customer_id=1002;
update banking.transactions set merchant='Annual card fee' where transaction_id=100240 and customer_id=1002;
$source_0084$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0084';
  if found then
    if previous.name is distinct from 'clear_fee_names' or previous.statements is distinct from array[script] then
      raise exception 'migration 0084 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0084 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0084','clear_fee_names',array[script]);
  end if;
end $apply_0084$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
