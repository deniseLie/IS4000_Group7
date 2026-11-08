-- Existing DemoBank project: apply only after reviewing hosted schema and RLS.
begin;

set local lock_timeout='10s';
set local statement_timeout='120s';

select pg_advisory_xact_lock(hashtext('demobank.hosted_migrations'));

do $apply_0148$
declare previous supabase_migrations.schema_migrations; script text:=$source_0148$-- Released source SHA256: f1d68c23416f0594fdcd7a6aec99fe3aaa74f808c607ce0be6d9ec7fe6481c21
-- The product is presented as UBS Connect (8 Nov 2026). Text clients can read that still said "DemoBank" is renamed:
-- knowledge articles and sections (titles, bodies, keywords, publisher), consent categories, request categories,
-- notification templates, meeting joining details and the bank-fee merchant name. Meaning, versions and ids are
-- unchanged. Meeting notices already acknowledged are left as they were (their text is what was agreed to).
-- After applying, re-run embed_knowledge so the section vectors match the renamed text.
do $$
declare target record; n bigint; total bigint:=0;
begin
  for target in select * from (values
      ('kb','articles','title'),('kb','articles','publisher'),
      ('kb','sections','heading'),('kb','sections','body'),('kb','sections','keywords'),
      ('consent','categories','label'),('consent','categories','description'),
      ('support','request_categories','label'),('support','request_categories','description'),
      ('notify','templates','title'),('notify','templates','body'),
      ('rm','meeting_options','joining_details'),
      ('banking','transactions','merchant')) t(s,tb,col)
  loop
    if to_regclass(format('%I.%I',target.s,target.tb)) is null then continue; end if;
    if not exists(select 1 from information_schema.columns where table_schema=target.s and table_name=target.tb and column_name=target.col) then continue; end if;
    execute format($q$update %I.%I set %I=replace(replace(replace(%I,'DemoBank coursework project','UBS Connect'),'DemoBank''s','UBS Connect''s'),'DemoBank','UBS Connect') where %I like '%%DemoBank%%'$q$,
      target.s,target.tb,target.col,target.col,target.col);
    get diagnostics n=row_count; total:=total+n;
  end loop;
  raise notice 'UBS Connect name: % rows updated',total;
end $$;
$source_0148$;
begin
  select * into previous from supabase_migrations.schema_migrations where version='0148';
  if found then
    if previous.name is distinct from 'ubs_connect_name' or previous.statements is distinct from array[script] then
      raise exception 'migration 0148 is recorded with different SQL; inspect history before proceeding';
    end if;
    raise notice 'migration 0148 already applied with identical SQL';
  else
    execute script;
    insert into supabase_migrations.schema_migrations(version,name,statements)
      values('0148','ubs_connect_name',array[script]);
  end if;
end $apply_0148$;

commit;
select version,name from supabase_migrations.schema_migrations order by version;
