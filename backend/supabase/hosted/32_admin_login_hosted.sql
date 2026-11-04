-- Admin dashboard login (docs/planning/admin-dashboard.md §1.2). Run in the SQL editor as the owner, after creating the
-- Auth user demo.admin@example.com with a password (Authentication -> Users). The password stays in the git-ignored
-- persona fixture, never in git.
--
-- The role is administrator-owned app_metadata (the browser cannot change it); the backend reads it from the verified JWT
-- (services/quality.is_admin) and never trusts user_metadata. The login has no customer record, no rm_id and no
-- rm.staff_logins row, so every customer and RM function returns nothing or refuses for it. It reads LangSmith only,
-- through the backend.
begin;

update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data,'{}'::jsonb) - 'rm_id' || '{"demobank_role":"admin"}'::jsonb
where lower(email) = 'demo.admin@example.com';

do $check$
begin
  if not exists (select 1 from auth.users where lower(email) = 'demo.admin@example.com' and raw_app_meta_data->>'demobank_role' = 'admin') then
    raise exception 'Create the Auth user demo.admin@example.com first (Authentication -> Users), then run this again';
  end if;
end $check$;

commit;

-- The admin signs out and in again so the new token carries the role.
select email, raw_app_meta_data->>'demobank_role' as role from auth.users where lower(email) = 'demo.admin@example.com';
