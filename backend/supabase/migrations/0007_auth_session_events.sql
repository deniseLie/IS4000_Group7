-- auth.session_started (system-architecture.md §6.4, F4): the API Gateway Layer calls this on the first request it sees
-- for a signed-in session. The session id comes from the caller's JWT (session_id claim), never from a parameter, so a
-- customer cannot open or end another session's trail. Recorded at most once per session: the existence check runs
-- under the same advisory lock audit.record() takes, so concurrent first requests and several API processes cannot
-- record it twice. auth.session_ended is written through public.audit_record_batch by POST /auth/session-end.

create index if not exists audit_events_session_started_idx on audit.events (session_id) where action = 'auth.session_started';

create or replace function public.audit_session_started(p_request_id uuid default null)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  sid uuid := nullif(auth.jwt() ->> 'session_id', '')::uuid;
  aal text := auth.jwt() ->> 'aal';
begin
  if auth.uid() is null or sid is null then
    raise exception 'a signed-in session is required';
  end if;
  perform pg_advisory_xact_lock(hashtext('audit.events'));
  if exists (select 1 from audit.events where session_id = sid and action = 'auth.session_started') then
    return false;
  end if;
  perform audit.record('auth', 'auth.session_started', 'success', null, null,
    jsonb_build_object('aal', case when aal in ('aal1', 'aal2') then aal end), null, sid, p_request_id);
  return true;
end $$;

revoke all on function public.audit_session_started(uuid) from public, anon, authenticated;
grant execute on function public.audit_session_started(uuid) to authenticated;
