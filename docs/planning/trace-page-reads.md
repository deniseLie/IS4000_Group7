# Page-load reads: audited, not traced

Plan and build, 10 Nov 2026 (see *What was built* at the end). One commit.

## What was measured (9 Nov 2026)

The LangSmith account (APAC, plan `free_10_2026`, no usage limits of our own) stopped taking traces on 7 Nov at 08:31 and took none on 8 and 9 Nov. The billing period resets on 1 Dec. Root runs (traces) since 1 Nov, from `POST /api/v1/runs/stats` with `is_root`:

| What | Where | Traces |
|---|---|---|
| Chat and RM assistant turns (`chat`, `rm_assistant`) | `demobank-dev` 104, `default` 75 | 179 |
| A single data read with no run above it (`tickets.list`, `rm.tickets`, `appointments.find_slots`, `customer.get_profile`, …) | `demobank-dev`, `default` | 2,907 |
| A single embedding with no run above it (`embed`) | `demobank-dev`, `default` | 385 |
| Other steps with no run above it (`talking_points`, `LangGraph`, `route`, …) | `demobank-dev`, `default` | 67 |
| Evaluator calls (`deterministic`, `judge`) | `evaluators` | 856 |
| Evaluation cases | 111 experiment projects | 626 |
| **Total** | | **5,021** |

Two thirds of the month went on page loads. Opening My requests, Overview, Inbox, RM Today or Schedule costs 2–6 traces, with one step each and no AI in them.

## Why it happens

- Agents and pages share the same readers. `customer_data._read` (`tickets.list`, `customer.get_profile`, …) and `rm/records.rpc` (`rm.tickets`, `rm.appointments`, …) serve both an agent's tool call in a chat and a page's own `GET`. Both wrap the read in `span(tool,'tool')`.
- Before 28 Oct, `span()` only added the step to the current request trace and did nothing without one ("no-op recording when none", `6b6f7a1`).
- `be5e801` (28 Oct, tracing moved to LangSmith) made `span()` call `langsmith.trace`. With no run above it, that starts a new root run, so every page read became a trace of its own.
- The reads are already in the audit log. `_read` runs `require_permission` first, the SQL readers write `data.read` with the returned `call_id` (0028/0029), and the RM readers return their `call_id` the same way. Tracing them adds nothing the audit log lacks.

## The rule

- **Audit log**: every data access (page loads, agent reads, refusals), ids and counts only. Unchanged.
- **Traces**: AI work, meaning a chat turn, an RM assistant turn, an evaluation case, and any model call.
- So a `tool` or `embedding` span with no LangSmith run above it sends nothing. The eval span sink and the request facts still see it.
- A model step with no run above it stays its own trace: the chat-insights classifier after the reply, the meeting worker's model calls and the judge.
- Inside a turn nothing changes. `get_current_run_tree()` is the same parent lookup `langsmith.trace` uses, and inside LangGraph nodes `langchain_core` sets that parent (`set_config_context`), so reads in agent nodes keep nesting.

## Change

### 1. `backend/app/core/observability.py`

```python
from contextlib import contextmanager,nullcontext
from langsmith.run_helpers import get_current_run_tree

RUN_TYPES={'tool':'tool','agent':'chain','decision':'chain','llm':'chain','embedding':'embedding'}
ROOTLESS=('tool','embedding')  # a page load's own read: a step inside a turn, never a trace of its own (the audit log records it)

@contextmanager
def span(name,kind,**attrs):
    """One step as a LangSmith run nested under the current run (nothing is sent while tracing is off). A tool or
    embedding step with no run above it, such as a page load's read, sends nothing: the audit log already records the
    read, and each would be a trace of its own against the plan's monthly cap. Yields the attrs dict so callers can add
    results; they become the run's metadata."""
    codex=kind=='llm' and attrs.get('transport')=='codex'
    inputs=attrs.pop('model_inputs',None)
    start=time.perf_counter()
    traced=kind not in ROOTLESS or get_current_run_tree() is not None
    with (langsmith.trace(name,'llm' if codex else RUN_TYPES[kind],inputs=inputs,metadata={'kind':kind}) if traced else nullcontext()) as run:
        try:
            yield attrs
        except BaseException as e:
            attrs['error_type']=type(e).__name__[:64]
            raise
        finally:
            outputs=attrs.pop('model_outputs',None)
            if run is not None:
                if outputs is not None:run.set(outputs=outputs)
                run.add_metadata(attrs)
                if codex:...  # unchanged (codex is an llm span, so always traced)
            # span_sink, current_trace agents/planner and tool_call bookkeeping: unchanged, outside the `if`
```

### 2. Tests

| File | Change |
|---|---|
| `tests/test_trace.py` | `test_failed_step_ends_its_run_with_the_error` opens `span('documents.inspect','tool')` with nothing above it. Wrap it in `langsmith.trace('chat','chain')` and also assert that the step's `parent_run_id` is that root. |
| `tests/test_trace.py` | New `test_a_read_outside_a_turn_is_audited_not_traced`. At the top level, `span('tickets.list','tool')` and `span('embed','embedding')` send nothing, and `span('talking_points','llm')` is still sent. The same `tool` span inside `langsmith.trace('chat','chain')` is sent as that root's child. |
| `tests/test_customer_data.py:60` (native, `TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1`) | Its seven readers run with nothing above them, so `{run['name'] for run in langsmith_runs.created}=={row[1] for row in reads}` becomes `langsmith_runs.created==[]`: seven audited `data.read` rows, no trace. |
| Unchanged, must pass | `tests/test_rm_graph.py` trace test (reads inside LangGraph agent nodes still nest: the proof the parent check sees LangGraph's parent), `test_llm_gateway.py` and `test_langsmith_client.py` (top-level `llm` spans still sent), `test_langsmith_eval.py`, `test_meeting_agent_boundaries.py` (span sink) |

### 3. Docs

- `AGENTS.md`, *Logging / tracing* row: add that a `tool` or `embedding` span with no run above it, such as a page load's read, sends nothing, and the audit log records it (`observability.ROOTLESS`).
- `README.md`, *Tracing and audit*: add that data reads are traced only as steps of a chat, assistant or evaluation run, and that a page load's reads are in the audit log only.

## What stays traced and what stops

| Step | Before | After |
|---|---|---|
| A chat or RM assistant turn with its nodes, agents, tool calls, reads and model calls | one trace | one trace (unchanged) |
| An evaluation case (including the runner's `GET /tickets` before and after, which run inside the case's run) | one trace | one trace (unchanged) |
| A page load's reads (Overview, My requests, Inbox, RM Today, Schedule) | one trace per read | none; audit log only |
| An embedding outside a turn | one trace | none |
| A model call outside a turn (chat-insights classifier, meeting worker) | one trace | one trace (unchanged) |

The admin dashboard is unaffected: Traces lists only `chat` and `rm_assistant` roots, and Latency's step timings come from steps inside them.

## Verify

1. From `backend/` with `PYTHONUTF8=1`: `../.venv/Scripts/python.exe -m pytest -q tests/test_trace.py tests/test_rm_graph.py tests/test_llm_gateway.py tests/test_langsmith_client.py tests/test_langsmith_eval.py tests/test_meeting_agent_boundaries.py`, then the full backend suite.
2. Native: `TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 ../.venv/Scripts/python.exe -m pytest -q tests/test_customer_data.py` (needs the PostgREST install).
3. Live (possible since the 10 Nov plan change; it uses a few traces of the month's limit):
   - open My requests and RM Today; no new root `tickets.list` or `rm.tickets` run appears in `demobank-dev`;
   - one chat turn still shows its `tool_call` and read steps inside `chat`;
   - the day's root count from `runs/stats` is about one per chat or assistant turn.

## Expected effect

About 3,290 of this month's 5,021 traces (66%) would not have been sent, and a page load costs 0 traces instead of 2–6. What is left is turns, evaluation cases and evaluator calls.

## Related, not in this change

- **Evaluator traces (856, 17%).** Pass `disable_evaluator_tracing=True` to `aevaluate` in `evaluation/langsmith_run.py`. Scores and judge comments still arrive as feedback on the case, but the judge's own trace is lost. Its own small change.
- **`default` project.** `.claude/worktrees/canva-connect-edit-ecf08b/.env` traces with no `LANGSMITH_PROJECT`, so a backend run from there writes to `default` (1,736 on 7 Nov). Set `LANGSMITH_PROJECT=demobank-dev` or `LANGSMITH_TRACING=false` there.
- **The cap itself.** The account moved to the Developer plan on 10 Nov, with a workspace limit of 6,863 traces a month (about 1,500 left until the 1 Dec reset; `GET /api/v1/usage-limits`).
- **Page-load timings (later, if wanted).** One trace per page request with its reads as steps (a middleware root, say), at one trace per page load. Not needed for the demo.

## Risk

If a future `langchain_core` stopped setting the parent inside LangGraph nodes, reads in agent turns would silently drop out of chat traces. The `test_rm_graph.py` trace test would fail on it, because it expects `tool_call` steps from agent nodes.

## What was built (10 Nov 2026)

- `observability.span` as above (`ROOTLESS`, `get_current_run_tree()`, `nullcontext()` when nothing is sent); the span sink and request facts are unchanged.
- `test_trace.py`: the failed-step test runs under a `chat` root and checks the parent; new `test_a_read_outside_a_turn_is_audited_not_traced` (top-level read and embedding send nothing and still reach the span sink, a top-level model call is still sent, the same read under `chat` is its child).
- `test_customer_data.py:60`: seven audited `data.read` rows and `langsmith_runs.created==[]`.
- `AGENTS.md` *Logging / tracing* row and `README.md` *Tracing and audit*: one clause each.

Results on Windows (`PYTHONUTF8=1`):
- the tracing tests (`test_trace`, `test_rm_graph`, `test_llm_gateway`, `test_langsmith_client`, `test_langsmith_eval`, `test_meeting_agent_boundaries`): 117 passed, 2 skipped (native-only);
- the RM trace test passes, so reads in LangGraph agent nodes still nest;
- full backend suite: 1,140 passed, 769 skipped (opt-in native, browser and live suites), 0 failed;
- native `test_customer_data.py` snapshot test with PostgreSQL and PostgREST (`TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1`): 6 passed.

Not checked live yet (step 3 of *Verify*).
