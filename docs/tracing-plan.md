# Tracing plan: store traces in Supabase

> **Superseded.** Request tracing moved to LangSmith (README "Tracing", `docs/langsmith-evals-plan.md`). The Supabase trace store described below is retired: `services/trace_store.py` is gone and migration `0071` drops `public.log_trace`; `ops.traces`/`ops.spans` remain as read-only history. Kept for the module 6.1 requirements mapping.

Source: *IS4000 Module 6.1 – Traces and Evals* (slides 4, 5, 9, 10, 11, 12).
Goal: record one complete trace per `/chat` request in our own Supabase project, so teammates can see analytics on a stats page without a LangSmith seat. LangSmith has been removed; the Supabase table is the only trace store (plus the local JSON `answer` log line).

## 1. What the slides require, and where it lands

| Slide requirement | What we record | Where |
|---|---|---|
| **Session level** (slide 5): user query to final response | One row per request: `trace_id`, mode, language, authenticated, history length before/after trimming, final `source_check`, `needs_human`, `privacy_warning`, total ms, outcome (`ok`/`error`) | `ops.traces` |
| **Agent level**: planning, tool selection, execution | `route` span. Which fixed or LLM branch fired (`secrets`, `lost_card`, `human_support`, `guest_balance`, `no_source`, `llm`) and the rule that triggered it | `ops.spans` (`kind='agent'`) |
| **Tool level**: API calls, DB queries | `retrieve` span (article IDs, count, history-fallback used, non-English "all articles" used) and `customer_context` span (row count per table, ms) | `ops.spans` (`kind='tool'`) |
| **LLM level**: prompts, tokens, responses | `llm` span (one per LLM Gateway call): `agent`, model, `transport`, `prompt_version`, input/output/total tokens, cost, ms, error class. Prompt and response text are **not** stored by default (see §6) | `ops.spans` (`kind='llm'`) |
| Decision points and reasoning (slide 4) | `validate` span with a pass/fail per check and one named `reject_reason` (`bad_json`, `bad_answer`, `source_check`, `secret_leaked`, `url_in_answer`, `action_claim`, `unknown_source`) | `ops.spans` (`kind='decision'`) |
| Time stamps and latency (slide 4) | `started_at` and `ms` on every span and trace | both tables |
| Errors and retries (slide 4) | `status`, `error_type` on spans; `retries` on the llm span (gateway-side retries; the client's own retries are off) | `ops.spans` |
| Common pitfall: traces too sparse, missing decision points (slide 12) | Every branch in `make_answer` emits a `route` and every rejection a named reason | code, §4 |
| Eval metrics: operational, safety, user experience (slide 10) | Latency percentiles, cost per query, throughput; secrets/reject/needs-human rates; task completion proxy and optional thumbs rating | stats page, §5 |
| Continuous improvement loop (slide 11) | A "review queue" of rejected or escalated traces to turn into new eval cases | §7 |

Note on "reasoning": the model returns only `answer` and `source_ids`, so we cannot capture model reasoning. The decision points we do capture are rule-based (routing, retrieval, validation), which is where this pipeline's behaviour is decided.

## 2. Data model (new schema `ops`)

Keep it out of `banking`. Add `backend/supabase/migrations/0001_ops_traces.sql` (new folder; the repo has no migrations yet).

```sql
create schema if not exists ops;

create table ops.traces (
  trace_id        text primary key,          -- request_id from RequestIdMiddleware
  created_at      timestamptz not null default now(),
  mode            text not null check (mode in ('public','personal')),
  language        text not null check (length(language) <= 32),
  authenticated   boolean not null,
  history_len     int,
  history_trimmed boolean,
  route           text not null,
  source_check    text,
  needs_human     boolean,
  privacy_warning boolean,
  reject_reason   text,
  prompt_version  text,
  article_ids     text[] default '{}',
  total_ms        int,
  input_tokens    int, output_tokens int, cost_usd numeric(10,6),
  outcome         text not null check (outcome in ('ok','error')),
  error_type      text,
  rating          smallint check (rating in (-1,1))   -- optional thumbs, set later
);

create table ops.spans (
  id          bigint generated always as identity primary key,
  trace_id    text not null references ops.traces(trace_id) on delete cascade,
  seq         int  not null,
  name        text not null,               -- route | retrieve | customer_context | llm | validate
  kind        text not null check (kind in ('agent','tool','llm','decision')),
  started_at  timestamptz not null,
  ms          int,
  status      text not null check (status in ('ok','error')),
  error_type  text,
  attrs       jsonb not null default '{}'  -- small, ids/counts/flags only
);
create index on ops.traces (created_at desc);
create index on ops.spans (trace_id);
create index on ops.spans (name, started_at desc);
```

Rules for what may be stored: IDs, counts, flags, enums, timings and token numbers only. No chat text, no customer records, no account numbers. Add `check (pg_column_size(attrs) < 2000)` on `attrs` to stop misuse.

## 3. Security (matches CLAUDE.md)

Constraints: never use a service-role key in the app, never disable RLS, only the public anon key and the user's JWT are available to the backend.

**Writing.** Guests have no JWT, so the backend writes with the anon key through one `security definer` function, `ops.log_trace(payload jsonb)`:
- Validates shape and enums, caps array and string sizes, rejects unknown keys, inserts the trace and its spans in one call.
- `revoke all on ops.traces, ops.spans from anon, authenticated;` so nobody can insert or read the tables directly. Only `grant execute on function ops.log_trace to anon, authenticated;`.
- RLS is enabled on both tables with **no** insert/update/delete policy.

Known limit to state in the README: the anon key is public, so anyone can call `log_trace` and add junk rows. Mitigations: strict validation, row-size limits, a daily row-count cap inside the function, and 30-day retention. This is acceptable for a demo and not for production.

**Reading.** Create `ops.analytics_viewers(user_id uuid primary key)` (an allowlist, managed in the Supabase dashboard). Expose reads only through `security definer` functions that check `exists (select 1 from ops.analytics_viewers where user_id = auth.uid())`:
- `ops.trace_summary(days int)`: aggregates for the dashboard.
- `ops.recent_traces(limit int, filter ...)`: rows plus spans for the review queue.
- `ops.rate_trace(trace_id, rating)`: optional thumbs. Callable by anyone who holds the `trace_id` from the response (ratings are low-stakes), and it may only set `rating` on that one row.

Add `ops` to the Data API exposed schemas, or call the functions via `supabase.schema('ops').rpc(...)`.

**Retention.** A scheduled `delete from ops.traces where created_at < now() - interval '30 days'` (pg_cron) so the table stays small and stale data isn't kept.

## 4. Backend changes

1. **`app/core/observability.py`**: add a lightweight in-process trace.
   - `ContextVar` holding the current trace (`trace_id`, `started_at`, spans list, attrs).
   - `span(name, kind, **attrs)` context manager (builds on the existing `timer`); records `started_at`, `ms`, `status`, `error_type`; `span.set(**attrs)` for results.
   - `start_trace()` / `finish_trace()`; `finish_trace()` also writes the local JSON `answer` log line (already exists).
2. **`app/services/trace_store.py`** (new): `save_trace(trace)` builds the payload and calls `rpc('log_trace', ...)` with the anon-key client. Rules: runs off the request path (`run_in_threadpool` in a background task, reference kept so it isn't garbage collected), hard timeout ~2 s, **catches everything and only logs a warning**. Disabled when `SUPABASE_URL`/`ANON_KEY` are missing or `TRACE_STORE=0`, so tests and offline eval don't write.
3. **`app/api/chat.py`**: create the trace at the start of `events()`; in `finally`, set `outcome`/`error_type` (this captures the `except Exception` path that currently logs a generic warning) and call `save_trace`. Also add the `customer_context` span around `get_customer_context`.
4. **`app/services/answers.py`**: replace the `info` dict with spans.
   - `route` span on each branch (include the trigger).
   - `retrieve` span (IDs, count, `used_history_fallback`, `used_all_articles`).
   - `validate` span with one boolean per check plus `reject_reason`.
   5. **`app/services/llm.py`**: capture tokens without changing `ainvoke`'s signature by wrapping the call in `langchain_core.callbacks.get_usage_metadata_callback()`; record input/output tokens and model name on the `llm` span. Cost = tokens × a small price table in `config.py` (OpenRouter free models will be `$0`). **Retries:** `max_retries=1` is handled inside the OpenAI client and may not reach LangChain callbacks, so verify with a forced timeout; if not visible, record `llm_ms` against the 30 s timeout and the error class instead of a retry count, and say so in the README.
6. **Config**: `TRACE_STORE` (default on when Supabase is configured), `TRACE_CONTENT` (default off), price table.
7. **Frontend feedback (optional)**: thumbs up/down in `Chat.jsx` calling `rate_trace`; needs `trace_id` returned in the SSE `answer` event (add `trace_id` to `result`, harmless to the existing UI).

## 5. Stats page (frontend)

New `frontend/src/Analytics.jsx`. When RBAC is added, the check belongs in the database functions, not the UI. Panels, mapped to slide 10:

- **Operational:** requests per day (throughput), p50/p95 total ms, p50/p95 per span (**bottleneck view**, slide 9), tokens and cost per query.
- **Safety:** share of requests per route, `secrets` and `lost_card` counts, rejection rate by `reject_reason` (**failure patterns**), `needs_human` rate.
- **User experience:** task completion proxy (answered without `needs_human` or rejection), thumbs ratio if §4.7 is built.
- **Quality of the pipeline:** most common `reject_reason` by `prompt_version`, so prompt changes can be compared (ties into the existing prompt versioning).
- **Review queue:** list of recent rejected, escalated or errored traces with their span timeline.

Open to everyone for now (no login gate); add the gate when RBAC lands. Use the existing frontend stack; one chart library only if needed (otherwise plain tables and bars).

## 6. Privacy and content

- Default: no text stored. This is stricter than the slide's "input and output for every step". We store IDs, counts and flags instead, because redaction is best-effort and the viewers are not bank staff.
- If we later want content for debugging, add `ops.trace_content(trace_id, question_redacted, answer_redacted)` behind `TRACE_CONTENT=1`, with its own stricter read policy (only the owner), a 7-day retention, and text passed through `redact` first. Not part of the first release.
- Document the decision in the README "Known limits".

## 7. Link to evals (slide 11)

- Add the same fields to `evaluation/run.py` output (route, article IDs, reject reason, ms, tokens, cost) so each eval case reports efficiency, safety and cost, and gives a **baseline** (slide 12: "no baseline" pitfall). Use the same span helper, writing to the report and not to Supabase.
- Review queue to eval: for each rejected or wrongly routed trace the team finds, add a case to `evaluation/cases.json` (the trace holds the route and IDs; the question text must be re-created by hand since we don't store it).
- Run the offline eval on every change, as slide 7 recommends.

## 8. Tests

- `observability`: span records ms, status and error type; nested spans keep order; failure inside a span is recorded and re-raised.
- `answers`: each route sets the expected `route`; each validation failure yields its named `reject_reason`; behaviour and answers unchanged (existing tests stay green).
- `trace_store`: payload contains no text fields; when the RPC raises, `make_answer`/`/chat` still succeed; disabled mode makes no network call.
- **Database (manual SQL checks, recorded in the PR):** `anon` cannot `select` or `insert` on `ops.*`; `log_trace` rejects bad enums and oversized payloads; a non-allowlisted user gets nothing from `trace_summary`; an allowlisted user does.
- Frontend: unit test for the summary-to-chart mapping; e2e with the deterministic model that the stats page is blocked for a non-viewer.

## 9. Build order

1. Migration, `log_trace` RPC and RLS; run the manual SQL checks.
2. Span helper plus `trace_store` (off the request path, fail-safe), wired into `chat.py` and `answers.py`; tests.
3. Token and cost capture in `llm.py`.
4. `trace_summary` / `recent_traces` functions and the stats page.
5. Eval report fields and the review-queue workflow.
6. Optional: thumbs rating, content table, retention job.

## 10. Decisions

Decided:
1. **Stats readable by everyone** for now; RBAC later (see §3).
2. **LangSmith removed** (decorators, `setup_tracing`, env vars, README section).

Still to confirm (defaults assumed unless you say otherwise):
3. **Write path:** `security definer` RPC with the anon key, accepting the junk-row risk in §3.
4. **Thumbs rating and content capture:** left for later (§9 step 6).
