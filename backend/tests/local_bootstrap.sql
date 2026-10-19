-- Test-only prerequisites normally owned by the hosted platform, plus the legacy ops schema.
-- These claims readers exercise SQL policies; they do not verify JWTs or implement an Auth API.
-- Supabase function semantics: https://github.com/supabase/auth/tree/master/migrations
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create schema ops;
create table auth.users (id uuid primary key, email text unique not null);
create function auth.jwt() returns jsonb language sql stable
as $$ select nullif(current_setting('request.jwt.claims',true),'')::jsonb $$;
create function auth.uid() returns uuid language sql stable
as $$ select (auth.jwt()->>'sub')::uuid $$;
grant usage on schema auth to anon,authenticated;
