# Admin dashboard (AI engineering view)

Plan written 4 Nov 2026; built 5 Nov 2026 in the six slices under *Build order*. Where the build differs from this plan:

- The source filter is `source=live|eval|all` (live is the app's tracing project, eval the requests inside the latest experiments); there is no separate surface filter. Latency is split by surface on the page, and agents by client and RM.
- Responses are plain dicts built by `services/insights.py`; no `schemas/admin.py`. The root-run summary lists agent names and each agent's worst status, not one dict per agent run.
- Experiment case rows come from one `/datasets/{id}/runs` call per dataset (fast and not rate-limited); requests and steps come from LangSmith's v2 run query (`/api/v2/runs/query`, up to 1000 runs a page), one read for the live project per window shared by every page and source, and one per recent experiment project. The older `/runs/query` (`list_runs`) returns 100 runs a page and answers 429 after about three pages, which made the SDK wait up to 9 s each time (6 Nov 2026: 30–60 s cold pages); it is kept only for single-trace reads (Traces, the failure card).
- LangGraph node runs inherit their ancestors' metadata, so the app's own span of a node's name (planner, agent, gather, act) is told apart by its parent or its own facts.
- Not built: the 'turn this failure into an eval case' button and the annotation queue (both listed under *Later*).

## Goal

One workspace for the AI engineer, with a new login `demo.admin@example.com`. It answers four questions:

1. **Is anything failing right now?** Requests that errored, answers held back by checks, agents that timed out, and eval cases that stopped passing.
2. **How well do the agents handle requests?** Accuracy for each agent, each prompt version and each eval dataset, over time.
3. **Where does the time and the token spend go?** Latency for each node, agent, tool, model and prompt version.
4. **When something fails, why?** For each failure: the request, the plan, which step failed, what the user saw, and the prompt and model that were in use.

The page only reads data and uses synthetic data. All of it comes from LangSmith, which the app already writes to. It needs no new database tables.

The Quality page is removed from the relationship manager workspace. Its two views (Evaluations, Traces) move into the admin workspace and are extended there.

---

## Part 1: What to change

### 1.1 Remove Quality from the RM workspace (and the client view)

| File | Change |
|---|---|
| `frontend/src/RmWorkspace.jsx` | Drop the `QualityPage` import, `'quality'` from `PAGES`, the `rm-open-quality` sidebar entry and the `view==='quality'` render. |
| `frontend/src/Chat.jsx` | Drop the `isTeam` link, `'quality'` from `PAGES` and the render (lines 17–18, 48, 523, 584). Only support staff saw it here. |
| `frontend/src/components/QualityPage.jsx`, `lib/quality.js`, `lib/quality.test.js` | Delete. The useful parts (`useLoad`, the experiment table, the trace tree) move into `components/admin/*` and `lib/admin.js`. |
| `backend/app/api/quality.py` | Replaced by `api/admin.py` (below). The `/quality/*` routes are removed. |
| `backend/app/services/quality.py` | `TEAM_ROLES={'rm','support'}` becomes `ADMIN_ROLE='admin'` and `is_team` becomes `is_admin`. The LangSmith read functions stay. |
| `backend/tests/test_quality.py` | Becomes `test_admin.py`. The role gate now expects `rm` and `support` to get **403**. |

### 1.2 The admin login

- **Role.** Supabase Auth `app_metadata.demobank_role = 'admin'`. An administrator sets it, and the backend never trusts `user_metadata`. This is the same pattern as Sophie's `rm` role.
- **Provisioning (owner steps; I cannot create accounts).**
  1. Authentication → Users → create `demo.admin@example.com` with a password. Keep the password in the git-ignored persona fixture (`personas.json`), never in git.
  2. Run the new `backend/supabase/hosted/32_admin_login_hosted.sql` in the SQL editor:
     ```sql
     update auth.users set raw_app_meta_data = raw_app_meta_data || '{"demobank_role":"admin"}'
     where email = 'demo.admin@example.com';
     ```
  3. Sign out and in again so the token carries the role.
- **No banking data access.** The admin has no customer record, no `rm_id` and no `rm.staff_logins` row. Every customer and RM SQL function therefore returns nothing or refuses, on top of the API gates. Tests check that an admin token gets 403 on `/rm/*` and no rows from `/customer/*`.
- **Routing.** `App.jsx` adds `if (session && isAdmin(session)) return <AdminWorkspace session={session}/>` before the `isRm` check, so an admin never lands in the client chat.

### 1.3 Backend

**Routes.** New `api/admin.py` (prefix `/admin`). Every route depends on `admin()`, which verifies the JWT and checks `app_metadata.demobank_role == 'admin'`. LangSmith failures return `503 admin_unavailable` and are reported, never replaced with sample data.

| Route | Returns | Main LangSmith reads | Cache |
|---|---|---|---|
| `GET /admin/overview?window=24h\|7d\|30d&surface=client\|rm\|all` | KPI tiles, sparklines, the "needs attention" list | `get_run_stats` + `list_runs(is_root, select=[metadata, latency, error, tokens])` on `chat`/`rm_assistant` roots; latest experiments | 60 s |
| `GET /admin/evaluations` | Every `demobank-*` dataset with its timeline points | `list_datasets(name_contains='demobank-')`, `list_projects(reference_dataset_id, include_stats=True)`, root runs of each experiment | 60 s for the list; 1 h for a finished experiment |
| `GET /admin/evaluations/{name}` | Case rows, as today, plus expected vs actual | as today | 1 h |
| `GET /admin/evaluations/compare?a=&b=` | Case matrix: fixed, regressed, still failing, still passing | from the cached case rows | 1 h |
| `GET /admin/agents?window=` | Agent scorecards, planner, gather, offers, tools, knowledge search | root metadata (§1.4), plus tool and `kb.search` spans from a sample of traces | 60 s |
| `GET /admin/prompts?window=` | Prompt catalogue, plus live and eval metrics for each version | `prompts.released()`, `docs/prompt-log/*`, `llm` spans, experiment metadata | 60 s |
| `GET /admin/latency?window=` | Percentiles, time breakdown, slowest requests | roots, plus the child spans of a sample (≤ 50 traces) | 60 s |
| `GET /admin/failures?window=&source=prod\|eval\|all` | Failures grouped by signature | roots with an error or held-back answer, child spans with `error=True`, failed or errored eval cases | 60 s |
| `GET /admin/failures/{run_id}` | One failure card (§2.6) | `read_run`, plus the trace's child runs (≤ 500) | 60 s |
| `GET /admin/traces`, `/admin/traces/{run_id}` | As today, plus filters (status, surface, minimum latency) | as today | 60 s |

**Code layout.**
- `services/quality.py` keeps every LangSmith read and the cache. One fix: it currently hard-codes five datasets and misses `demobank-graph` and `demobank-planner`, so it should list `demobank-*` datasets from LangSmith instead.
- New `services/insights.py` holds only pure functions over plain run dicts: `percentiles`, `kpis`, `attention`, `timeline`, `regressions`, `flaky`, `agent_scorecard`, `prompt_stats`, `breakdown` and `signature`. Unit tests feed them sample run dicts, so no LangSmith call is needed.
- New `schemas/admin.py` holds the response models.

**Bounds** (so the page scales and never hammers LangSmith):
- The window is at most 30 days.
- At most 2,000 root runs per read. When the cap is hit, the page says "sampled".
- Child spans are fetched only for one trace or a sample of ≤ 50.
- `select=` fields keep the payloads small.

### 1.4 Instrumentation to add first (so the numbers exist)

Today the per-agent and per-prompt facts sit only in the child spans. Fetching every child span of every request to build a 7-day scorecard does not scale. Each request should therefore carry a **summary on its root run**. The Overview, Agents and Prompts pages then need one root-run read, and child spans are fetched only for drill-down.

| Where | Add | From |
|---|---|---|
| `core/observability.Trace` | `agents: list[dict]`: one entry per agent run with `{agent, intent, path, status, outcome, ms, tool_calls}` | `runner.run_agent` and `rm/graph.agent` append to it in their `finally` |
| `api/chat.py` root `chat` run | `agents`, `agent_status`, `plan_route`, `tasks`, `model_calls`, `tokens`, `prompt_ids` (distinct `llm` prompt ids), `model`, `first_section_ms`, `app_version` | `trace.llm_calls`, `trace.agents`, the planner span attrs, time of the first `section` event; `APP_VERSION` env (git sha) |
| `api/rm.py` root `rm_assistant` run | The same summary, plus `outcome`, `route` and `error_type`. Today it carries only `request_id`/`session_id`. | same |
| `services/prompts.py` | `in_use()`: `{family: version}` for **every** prompt actually configured: the `VERSIONS` table (planner, runner agents, `runner_common`, the v4 domain prompts, `rm_planner`, `rm_assistant`, `answer`, …) plus the meeting side's own pins. | `prompts.VERSIONS` and the meeting modules |
| `evaluation/langsmith_run.py` experiment metadata | `prompts_in_use` (from `in_use()`: every agent's pinned version), `dataset_tag` (`sha-…`), `case_count`, `case_ids_hash`, `partial` (when `--case` was used), `app_version` | the runner |
| `evaluation/targets.py` outputs | `prompt_ids` that ran in each case | the span sink it already reads |

Older runs do not have these fields. The dashboard shows "not recorded" for them, never zero.

### 1.5 Frontend

- `AdminWorkspace.jsx` uses the same shell as `RmWorkspace` (`Sidebar`, `TabBar`, `#admin-…` hashes). Pages: **Overview, Evaluations, Agents, Prompts, Latency, Failures, Traces**. The header shows the window picker (24 h / 7 d / 30 d), the surface filter (Client / RM / All) and a Refresh button.
- `components/admin/*Page.jsx`: one component per page.
- `components/admin/charts.jsx`: small inline-SVG `Sparkline`, `LineChart`, `Bars` and `Heatmap`. This adds no new dependency and matches the local-SVG approach in `Icon.jsx`.
- `lib/admin.js`: fetchers plus pure helpers (formatting, deltas, status colours), with `admin.test.js`.
- CSS: the `.quality-*` block in `index.css` becomes `.admin-*` inside the "UI refresh" section.

### 1.6 Tests and docs

- **Backend** `test_admin.py`:
  - Role gate: no token → 401; a customer, `rm`, `support`, or `user_metadata` admin → 403; `app_metadata` admin → 200.
  - Bounded parameters, and a LangSmith failure → 503.
  - Every `insights` function on sample runs.
  - An admin token is refused on `/rm/*`.
  - Opt-in live read-back: `TEST_LANGSMITH_ADMIN_LIVE=1`.
- **Frontend** `admin.test.js`, and `rm.test.js` asserts the RM pages no longer include Quality.
- **Browser (opt-in, hosted personas):** admin login → dashboard loads; Sophie's sidebar has no Quality link.
- **Docs:**
  - `AGENTS.md`: the "Quality page" row becomes "Admin dashboard".
  - `README.md` persona table: add the admin row.
  - `docs/evaluation/README.md` (the Quality paragraph in step 4).
  - `docs/architecture.md` (Observability row).
  - `docs/planning/status.md`.

---

## Part 2: Analytics on the admin dashboard

### Definitions (one meaning per number)

| Term | Definition | Source |
|---|---|---|
| Request | One root run named `chat` or `rm_assistant` in `LANGSMITH_PROJECT`. Eval traces sit under an `eval <case>` root, so they are excluded automatically. | root runs |
| Failed request | Root run with `error`, or `outcome='error'` | root metadata |
| Held back | The answer was not shown because the source/figure checks failed (`response_origin='validation'` or `source_check` not passed) | root outputs/metadata |
| Handed to a person | `needs_human=true` for any reason | root outputs |
| Degraded | The reply was sent, but at least one agent returned `withheld`, `timeout` or `error` | `agent_status` (§1.4) |
| Case pass rate | pass ÷ (pass + fail + error). Skipped cases are excluded and shown separately. | experiment runs |
| Comparable runs | Same dataset tag (`sha-…`), same case set, not `partial` | experiment metadata |
| Latency | Root run latency (server time for the whole turn). `first_section_ms` is time to the first streamed section. | root runs |

Judge scores are always labelled **model-graded**. They never decide pass or fail; that rule already applies in `langsmith_run.status`.

### 2.1 Overview: "is anything wrong right now?"

**KPI tiles.** Each tile shows its value for the window, the change versus the previous window, and a sparkline.

| Tile | Why an AI engineer cares |
|---|---|
| Requests (client chat / RM assistant) | Load and context for every other number |
| Success rate (1 − failed) | Hard failures users saw |
| Held-back rate | Grounding quality: the model said something the checks could not support |
| Handed-to-person rate | How often the AI could not finish the job |
| p50 / p95 latency | Responsiveness, plus tail latency |
| Model calls per request, tokens per request | Cost and scale per turn |
| Latest eval pass rate per dataset (client QA1, RM QA1, graph, planner, safety, focused) | Accuracy, with the change versus the previous comparable run |

**Needs attention.** A ranked list; each row links to the filtered Failures page. The starting thresholds are constants in `insights.py`, and the team tunes them.
- A new failure signature appeared in the window (for example `LLMTimeout` in `security`, 4 times).
- **Regression:** a case passed in the previous comparable run and fails in the latest one.
- Held-back rate above 15% for any agent.
- p95 latency above 30 s.
- Error rate above 2%.
- Knowledge search fell back to keyword-only, meaning the embedding provider is failing (`kb.search` `mode=keyword_only`).
- **Invariant broken:** a fixed safety route (lost card, secrets, human support) made a model call. This must always be 0.
- A prompt version is live but has no eval run ("untested in eval").
- A dataset has not been run for more than 7 days ("stale").

### 2.2 Evaluations: accuracy over time

**Layout (5 Nov 2026).** The page opens on **All datasets**: one row per dataset with the latest full run's pass rate, the change since the full run before it, the judge mean (below), pass/fail/error/skipped, the cases to look at (regressed, flaky, always failing, failing) and the last run in SGT. Pass rates are not added across datasets, because each dataset tests something different. Picking a dataset shows its tiles and four views. **Judge mean (11 Nov 2026):** the AI judge's average `judge_rubric` score (0 to 1) on the latest full run made with `--judge`, with the change since the judged run before it by the same judge model (another judge grades differently, so no change is shown); a later run without `--judge` keeps the last judged mean and names its date. It is a column in All datasets and a tile beside the rule pass rate, and never decides pass or fail (`lib/admin.js` `datasetRow`).

- **Run detail** (the default): one run's cases with errors and failures first. It shows the latest run until another is picked.
- **Trends:** the timeline and the check-level trend.
- **Cases × runs:** the heatmap, with a colour legend, problem cases first and a *Hide passing cases* switch.
- **Compare:** two runs side by side.

A dot on the timeline, a date in the list of changes or a heatmap column date opens that run in Run detail. A heatmap cell opens that case, expanded, in that run. A regression link from the Overview opens Cases × runs with the case highlighted.

- **Timeline per dataset.**
  - The x-axis is experiment time; the y-axis is case pass rate.
  - Markers show where `prompts_in_use`, `model` or `pipeline` changed from the previous point (for example "retail v10 → v11").
  - Partial runs are drawn hollow and are left out of the line by default.
  - Hovering shows: n cases, pass/fail/error/skipped, model, transport and judge model.
- **Check-level trend.** The pass rate of each check over time, grouped:
  - **S (answer):** `reply_has`, `reply_lacks`, `cites`, `record_sources`, `origin`, `fixed_route`, `needs_human`, `ticket`.
  - **T (trajectory):** `nodes`, `agents`, `tools`, `model`, `service_reads`, `waves`, `exports`, `skipped`, `rerouted`.
  - **Planner:** `route`, `agents`, `dependencies`, `priority`.
  - **Judge:** average `judge_rubric`, labelled model-graded.

  This shows whether failures come from wording, citations, routing or tools.
- **Case heatmap.** Rows are cases and columns are the last 10 comparable runs; each cell shows the status. It makes these patterns visible at a glance:
  - **Flaky cases:** status changed two or more times with the same prompts. The cause is model non-determinism or a brittle check.
  - **Always failing:** a known gap.
  - **Fixed:** green after red.
- **Compare two runs.** A case matrix of fixed, regressed, still failing and still passing. For each regressed case, the failing check comments sit side by side.
- **Experiment detail.** Today's table: case, status, failed checks, judge, agents, model calls and time. Opening a row also shows:
  - **Expected:** `reply_has`/`reply_lacks`, trajectory, ticket.
  - **Actual:** the answer, agents, tools, record reads.
  - The judge's reason.
  - **Repro:** `python -m evaluation.langsmith_run --dataset … --case …`
- **Coverage.** For each dataset: cases, runnable versus `manual`/`spec`, last run date and dataset tag.

### 2.3 Agents: how well each agent handles requests

**One scorecard per agent**: `planner`, `guide`, `accounts`, `security`, `compliance`, `relationship`, `rm_client`, `rm_requests`, `rm_calendar` (build plan 3a; previously `rm_client_insight`, `rm_service_desk`), plus the fixed safety routes as their own row.

| Column | Meaning |
|---|---|
| Runs, % of requests | How much traffic it takes |
| Outcome mix | ok / withheld / needs clarification / timeout / error |
| Path mix | rule (no model) / default reads / tool loop, which shows how much is deterministic |
| Tool calls per loop, refused tool calls | Tool-use efficiency, and attempts outside the agent's allow-list |
| Evidence items per answer, exports passed | Grounding depth; how well agents cooperate (v5 dependencies) |
| p50 / p95 agent time | Which agent is slow |
| Prompt version live | Links to the Prompts page |
| Eval pass rate of the cases that run this agent | Offline accuracy next to the live outcomes |

**Orchestration panels:**
- **Planner:**
  - Route mix: rules / model / act / ask-which / expired.
  - Tasks per plan, the share of multi-agent turns and the share of dependent tasks.
  - Planner-dataset accuracy (route, agents, dependencies, priority).
- **Gather:** waves, dependants skipped (and why), hand-offs (`hands_to`), and exports passed by name.
- **Offers:** offered → confirmed / cancelled / expired / failed (`earlier_offer_failed`). This shows whether people accept what the agents propose.
- **Tools:** for each tool, calls, error rate, p50/p95, and denials (`agent_tool_forbidden`).
- **Knowledge search:**
  - Hybrid versus keyword-only share, and embedding errors.
  - Empty-result rate (no evidence).
  - `embed_ms` versus `sql_ms`.

### 2.4 Prompts: versions and how each one performs

- **Catalogue.** For each prompt family (`planner`, `retail`, `fraud`, `security`, `rm_planner`, `rm_assistant` and the rest), show:
  - Every version on disk (`prompts.released()`) and which one is live.
  - Which version the latest eval used, with an "untested in eval" badge when they differ.
  - From `docs/prompt-log/<agent>/vN.md`: the version's **Goal**, **Change from previous** and **Decision**.
- **Live metrics per version** (from `llm` spans):
  - Calls and OK rate.
  - Failure reasons: `timeout`, `bad_output` (invalid JSON or schema), budget exceeded, provider error.
  - Retries.
  - p50/p95 latency and average input/output tokens.
- **Eval metrics per version:**
  - Pass rate of the cases in which that prompt ran.
  - The change versus the previous version on the same dataset.
  - Average judge score.
- **Compare two versions.** Pick a family and two versions to see accuracy, latency, tokens and failure rate side by side, with sample sizes. Below n = 10 the page shows "too few runs to compare".

### 2.5 Latency and cost: what limits scale

- **End to end:** p50/p90/p95/p99 and a histogram, split by surface (public chat, signed-in chat, RM assistant). Also time to first section.
- **Where the time goes** (from a sample of traces):
  - The share of time in model calls, database/tool calls, embedding, and everything else.
  - A waterfall for the 10 slowest requests.

  `database-speed.md` measured ~190 ms per hosted Data API call, so this view shows how much a move to the database's region would save.
- **Per node** (planner / agent / gather / act / compose), **per agent**, **per tool**, **per model** and **per prompt version**: p50/p95 for each.
- **Fan-out:** agents per request and waves, and latency bucketed by agent count. This shows whether parallel agents help or hurt.
- **Throughput:** requests per hour and the peak hour.
- **Tokens:**
  - Per request (average and p95) and per agent.
  - Token-budget hits (`LLMBudgetExceeded`) and retries.
  - Cost only where LangSmith prices the model; otherwise tokens only, never an estimate shown as fact.
- **Slowest requests table**, each linking to its trace.

### 2.6 Failures: a debug inbox

**List.** Failures are grouped by **signature** (error type + node + agent + prompt version), with count, first seen and last seen. The filters are window, surface, agent, error type, prompt version and source (live or eval).

What counts as a failure:
- **Live:**
  - A failed request.
  - A held-back answer.
  - An agent `withheld`, `timeout` or `error`.
  - An `llm` outcome other than `ok`.
  - A tool error or denial.
  - A planner refusal.
  - A failed offer step.
  - The RM book check refusing a client outside the book is correct behaviour. It is listed separately as "refusals", not as failures.
- **Eval:** a case with status `fail` or `error`.

**Failure card** (opening one item):

| Section | Content |
|---|---|
| What was asked | The redacted last user message, the number of earlier turns, the surface (guest / signed-in client / RM) and the language |
| What the system did | The plan (agents, intents, dependencies, priority), the route, and the step path down to the failing step (for example `planner → agent:security → llm security/v1+hash`), with each step's status and time; tools called and records read (names only) |
| Where it failed | The failing step, error type and truncated message, cause, retries, outcome, and the validation reason (for example `unknown_source`, unsupported figure) |
| What the user saw | The final answer or the held-back text, origin, `needs_human`, and any ticket or handoff opened |
| Configuration at the time | Model, transport, pipeline, prompt ids, app version |
| For eval cases | Expected versus actual, each failing check with its comment, the judge's reason |
| Next steps | Open in LangSmith, copy the run id, copy the repro command (eval) or the redacted question (live), and similar failures with the same signature |

All text shown here already passed the redacting LangSmith client (`core/langsmith_client.py`). The dashboard adds no new copy of user text anywhere else.

### 2.7 Traces

This is today's trace list and step tree, plus filters (status, surface, minimum latency) and a highlight on the step that failed.

---

## Build order (one commit per slice, on this branch)

1. **Access:**
   - Remove Quality from the RM workspace and the client view.
   - The `admin` role gate.
   - The empty `AdminWorkspace` shell with Traces and Evaluations moved over unchanged.
   - Hosted SQL file `32_admin_login_hosted.sql`.
   - `test_admin.py` gate tests.
2. **Instrumentation** (§1.4): root-run summaries, `prompts.in_use()`, and the experiment metadata. Then one eval run so the new fields exist.
3. **Overview + Failures.** These give the most value for "is anything failing, and why".
4. **Evaluations timeline, heatmap and run comparison; Prompts.**
5. **Agents + Latency.**
6. **Docs** (§1.6), the full backend suite, `npm test`, `npm run build`, and an opt-in hosted browser check with the admin persona.

## Decisions to confirm

- **Support role:** default **admin only**. Support staff lose the Quality link they had in the client view. Keeping it is a one-line change.
- **Charts:** default **inline SVG** (no new dependency). The alternative is adding `recharts`.
- **MFA for admin:** default **not required** for the demo. Support requires `aal2`; admin could too.
- **Later, not in this plan:**
  - "Turn this failure into an eval case", which writes to the git case file plus a sync.
  - The LangSmith annotation queue already planned in `docs/evaluation/README.md` step 5.
