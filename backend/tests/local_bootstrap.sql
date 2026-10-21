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

-- Storage catalogue prerequisites for SQL/RLS tests only. This is not a Storage HTTP server
-- and these rows do not assert that blob bytes exist. Hosted tests exercise the real service.
create schema storage;
create table storage.buckets(id text primary key,name text not null,public boolean not null default false,
  file_size_limit bigint,allowed_mime_types text[]);
create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text not null references storage.buckets(id),
  name text not null,owner uuid,owner_id text,metadata jsonb,created_at timestamptz not null default now(),
  unique(bucket_id,name));
alter table storage.objects enable row level security;
grant usage on schema storage to authenticated;
grant select,insert,update,delete on storage.objects to authenticated;
