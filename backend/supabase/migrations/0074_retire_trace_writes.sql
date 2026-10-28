-- Request traces moved to LangSmith (README "Tracing"; app/core/observability.py). The backend no longer calls public.log_trace,
-- so drop its anon/authenticated write path (it also closes the public junk-row risk noted in 0001).
-- ops.traces and ops.spans stay as read-only history: RLS on, no policies, no grants. 0001 itself is unchanged.

drop function if exists public.log_trace(jsonb);
