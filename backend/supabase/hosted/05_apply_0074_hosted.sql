-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0074$
declare previous supabase_migrations.schema_migrations; script text:=$source_0074$-- Released source SHA256: e942f5f8ec6223188d110e8f4ace6aeb8cd05f24bab3771abba92ccda875278d
-- Request traces moved to LangSmith (README "Tracing"; app/core/observability.py). The backend no longer calls public.log_trace,
-- so drop its anon/authenticated write path (it also closes the public junk-row risk noted in 0001).
-- ops.traces and ops.spans stay as read-only history: RLS on, no policies, no grants. 0001 itself is unchanged.

drop function if exists public.log_trace(jsonb);
$source_0074$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0074';
  if found then
    if previous.name is distinct from 'retire_trace_writes' or previous.statements is distinct from array[script] then
      raise exception 'migration 0074 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0074 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0074','retire_trace_writes',array[script]);
  end if;
end $apply_0074$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
