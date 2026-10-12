-- Request traces for the chatbot (docs/tracing-plan.md, sections 2-3).
-- Stores ids, counts, flags, enums, timings and token numbers only. Never chat text or customer records.
-- Tables live in schema ops and are closed to anon/authenticated. The only write path is public.log_trace().
-- The existing ops.llm_traces and ops.test_results tables are not touched.

create table ops.traces (
  trace_id        text primary key check (length(trace_id) between 1 and 64),
  created_at      timestamptz not null default now(),
  mode            text not null check (mode in ('public','personal')),
  language        text not null check (length(language) <= 32),
  authenticated   boolean not null,
  history_len     int check (history_len between 0 and 1000),
  history_trimmed boolean,
  route           text not null check (route in ('secrets','lost_card','human_support','guest_balance','no_source','llm','error')),
  source_check    text check (source_check in ('fixed_guidance','rejected','basic_checks_passed')),
  needs_human     boolean,
  privacy_warning boolean,
  reject_reason   text check (reject_reason in ('bad_json','bad_answer','source_check','secret_leaked','url_in_answer','action_claim','unknown_source')),
  prompt_version  text check (length(prompt_version) <= 64),
  article_ids     text[] not null default '{}' check (cardinality(article_ids) <= 20 and length(array_to_string(article_ids,',')) <= 600),
  total_ms        int check (total_ms >= 0),
  input_tokens    int check (input_tokens >= 0),
  output_tokens   int check (output_tokens >= 0),
  cost_usd        numeric(10,6) check (cost_usd >= 0),
  outcome         text not null check (outcome in ('ok','error')),
  error_type      text check (length(error_type) <= 64),
  rating          smallint check (rating in (-1,1))
);

create table ops.spans (
  id          bigint generated always as identity primary key,
  trace_id    text not null references ops.traces(trace_id) on delete cascade,
  seq         int not null check (seq between 0 and 50),
  name        text not null check (name in ('route','retrieve','customer_context','llm','validate')),
  kind        text not null check (kind in ('agent','tool','llm','decision')),
  started_at  timestamptz not null,
  ms          int check (ms >= 0),
  status      text not null check (status in ('ok','error')),
  error_type  text check (length(error_type) <= 64),
  attrs       jsonb not null default '{}' check (jsonb_typeof(attrs) = 'object' and pg_column_size(attrs) < 2000)
);

create index traces_created_at_idx on ops.traces (created_at desc);
create index spans_trace_id_idx on ops.spans (trace_id);
create index spans_name_started_idx on ops.spans (name, started_at desc);

alter table ops.traces enable row level security;
alter table ops.spans enable row level security;
-- No policies on purpose: nobody can read or write these tables directly.
revoke all on ops.traces, ops.spans from anon, authenticated;

-- Single write path. Lives in public so it is reachable through the Data API without exposing the ops schema.
create or replace function public.log_trace(payload jsonb)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  s jsonb;
  allowed_trace text[] := array['trace_id','mode','language','authenticated','history_len','history_trimmed','route','source_check','needs_human','privacy_warning','reject_reason','prompt_version','article_ids','total_ms','input_tokens','output_tokens','cost_usd','outcome','error_type','spans'];
  allowed_span  text[] := array['seq','name','kind','started_at','ms','status','error_type','attrs'];
begin
  if jsonb_typeof(payload) <> 'object' then raise exception 'payload must be an object'; end if;
  if exists (select 1 from jsonb_object_keys(payload) k where k <> all (allowed_trace)) then raise exception 'unknown trace key'; end if;
  if jsonb_typeof(coalesce(payload->'spans','[]'::jsonb)) <> 'array' or jsonb_array_length(coalesce(payload->'spans','[]'::jsonb)) > 12 then raise exception 'bad spans'; end if;
  -- daily cap: the anon key is public, so bound how much junk can be written
  if (select count(*) from ops.traces where created_at > now() - interval '1 day') >= 10000 then raise exception 'daily trace cap reached'; end if;

  insert into ops.traces (trace_id, mode, language, authenticated, history_len, history_trimmed, route, source_check, needs_human, privacy_warning, reject_reason, prompt_version, article_ids, total_ms, input_tokens, output_tokens, cost_usd, outcome, error_type)
  values (
    payload->>'trace_id', payload->>'mode', payload->>'language', (payload->>'authenticated')::boolean,
    (payload->>'history_len')::int, (payload->>'history_trimmed')::boolean,
    payload->>'route', payload->>'source_check', (payload->>'needs_human')::boolean, (payload->>'privacy_warning')::boolean,
    payload->>'reject_reason', payload->>'prompt_version',
    coalesce(array(select jsonb_array_elements_text(payload->'article_ids')), '{}'),
    (payload->>'total_ms')::int, (payload->>'input_tokens')::int, (payload->>'output_tokens')::int, (payload->>'cost_usd')::numeric,
    payload->>'outcome', payload->>'error_type'
  )
  on conflict (trace_id) do nothing;

  if not found then return; end if;

  for s in select * from jsonb_array_elements(coalesce(payload->'spans','[]'::jsonb)) loop
    if exists (select 1 from jsonb_object_keys(s) k where k <> all (allowed_span)) then raise exception 'unknown span key'; end if;
    insert into ops.spans (trace_id, seq, name, kind, started_at, ms, status, error_type, attrs)
    values (payload->>'trace_id', (s->>'seq')::int, s->>'name', s->>'kind', (s->>'started_at')::timestamptz, (s->>'ms')::int, s->>'status', s->>'error_type', coalesce(s->'attrs','{}'::jsonb));
  end loop;
end;
$$;

revoke all on function public.log_trace(jsonb) from public;
grant execute on function public.log_trace(jsonb) to anon, authenticated;
