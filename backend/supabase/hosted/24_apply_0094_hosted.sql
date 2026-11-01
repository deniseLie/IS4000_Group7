-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0094$
declare previous supabase_migrations.schema_migrations; script text:=$source_0094$-- Released source SHA256: 6ea3c8bf2bedb72c6db65d8d045cc44723acfbe09a5f4d516ea5b09fe399790a
-- Fix for 0089: its closing "revoke all on all functions in schema rm" also removed two grants that existed before it.
-- * rm.is_my_advisor(uuid) is used by the customer RLS policies on rm.advisors, rm.working_hours and rm.meeting_options
--   (0003, 0062). Without EXECUTE, a client's own advisor reads (customer_read_assignment, customer_read_introduction,
--   meetings) fail with "permission denied for function is_my_advisor" (42501), shown as "Record access does not match
--   the signed-in customer". Restored exactly as in 0003.
-- * The constructor functions of the rm.advisor_range type (0034) had the default PUBLIC EXECUTE; restored.
-- The RM-only helpers added by 0089 stay revoked: only the security-definer RM functions call them.
revoke execute on function rm.is_my_advisor(uuid) from public, anon;
grant execute on function rm.is_my_advisor(uuid) to authenticated;
grant execute on function rm.advisor_range(uuid,uuid),rm.advisor_range(uuid,uuid,text) to public;
$source_0094$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0094';
  if found then
    if previous.name is distinct from 'restore_rm_policy_grant' or previous.statements is distinct from array[script] then
      raise exception 'migration 0094 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0094 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0094','restore_rm_policy_grant',array[script]);
  end if;
end $apply_0094$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
