# v5 orchestrator build plan

The target design is [architecture.md §14](../architecture.md#14-target-architecture-v5-redesign-agreed-3-nov-2026-not-built-yet), and the target agents are in §5.6.1. This plan says **in what order to build it**, and **when each phase counts as done**. All the design decisions are settled (table at the end). If something here disagrees with §14, follow this plan and fix §14.

- **Branch:** `claude/orchestrator-architecture-28c920`, on top of `6b8c39d`.
- **Commits:** one per phase, never a PR per phase.
- **Push:** only when asked. Then merge into `main` and push `main` only.

## Ground rules for every phase

- **Replace in place.** There is no feature flag. Phases 1–4 add parts nobody uses yet, and phase 5 switches over. After every commit, `/chat` must still answer, and the urgent paths must still work: lost card, fraud wording and pasted secrets.
- **The invariants in AGENTS.md still hold:**
  - caller JWT + RLS, and no service-role key
  - fixed safety replies never call the model
  - logs and audit record ids and counts only
  - no fake model: browser checks use the real provider
  - never edit a released prompt version or an applied migration
- **The failing baseline:** about 28 backend tests already fail on `6b8c39d`. Examples are lost-card wording, prompt versions and `test_rm_handover`. Record that list in phase 0. After that, a phase may only fix those tests, never add new failures.
- **Run tests with:** `PYTHONUTF8=1` and the repo-root `.venv` Python.
- **Next free migration:** `0097`.

---

## Phase 0: Lock the baseline (docs only)

1. Commit the §5.6.1 and §14 changes to `docs/architecture.md`, together with this plan.
2. Run `pytest -q` and save the failing test ids to `docs/planning/v5-baseline-failures.txt`.
3. Measure the current speed: time to the first answer and total time, on the QA1 client cases, using LangSmith `chat` runs. Record the median and p90 in the same file.
4. The uncommitted evaluation work belongs in phase 8. Check it in, or park it, with the user:
   - `graph_cases.json`, `planner_cases.json`, `test_graph_cases.py`
   - the edited `qa1_client_cases.json`, `targets.py`, `langsmith_sync.py`

**Done when:** the baseline list and the timings are committed.

---

## Phase 1: Agent catalogue and tool field views (not visible to clients)

**Goal:** one `AgentSpec` per target agent becomes the single source for policy, tools and the planner's agent list.

**Files**
- `schemas/contracts.py`:
  - Add `AgentSpec`, `PlanTask`, `AgentOutput`, `Offer` and `Export` (a value plus its `source_ids`).
  - Add the new agent names `guide`, `accounts`, `security`, `compliance` and `relationship`. Keep the old names until phase 5.
- `services/agents/registry.py`:
  - Add `SPECS: dict[AgentName, AgentSpec]` for the 5 client agents, using the fields, tools, external hosts and offer types from §5.6.1.
  - Add `TOOL_PERMISSION`, which maps each model-facing tool to its permission name. For example, `my_cards` maps to `customer.get_credit_cards`.
  - Fix the `docs.precheck` drift: the permission is `documents.precheck`.
- `services/authorization.py`: `decide` reads a new agent's allow-list from `SPECS`. During phases 1–4 the old agents still use `registry.TOOLS`.
- `services/tools.py`: `call(name, args, ctx)` does two things:
  - It projects the result to the calling agent's **field view**. For example, Security sees only `card_id`, `last4` and `status`.
  - It enforces per-call limits: transactions from the last 90 days or less, slots within 14 days or less, and a row cap.
- `AGENTS.md`: replace "change only by PR reviewed by all developers" with the new rule (no separate review). Add rows for the catalogue and the field views.

**Tests:** `tests/test_agent_specs.py` checks that:
- every spec tool exists in `tools.TOOLS` and in `TOOL_PERMISSION`
- every permission is in `authorization.KNOWN_TOOLS`
- the field views drop the excluded fields (PAN, internal ids, emails)
- the limits are enforced
- `decide` denies a tool that is missing from the agent's spec

**Done when:** the new tests pass and there are no new failures against the baseline.

---

## Phase 2: External tool gateway (not visible to clients)

> Removed on 8 Nov 2026: `services/external.py`, both tools, their settings and the `external_down` fault are gone; trip due dates shift for weekends only. This section is kept as history.

**Goal:** two read-only public data tools that never receive customer data.

**Files**
- `services/external.py`, with two tools:
  - `public_holidays(country, year)` uses Nager.Date.
  - `fx_rate(base, quote)` uses Frankfurter (ECB). Check its current host before you hard-code it.
- What `external.py` does:
  - **Inputs:** checks them with regexes. Country is ISO 3166 alpha-2, currency is ISO 4217, and year is this year ±1.
  - **Hosts and network:** allows only the hosts named in settings, uses `httpx` with a 5-second timeout, and caches results in memory (holidays for 24 h, FX for 60 s).
  - **Audit:** writes `external.called {tool, host, status, ms}`.
  - **Failures:** returns a typed `ExternalError`, never a made-up value.
- `services/tools.py` and `app/mcp_server.py`: register both tools. The external hosts each agent may use come from its spec, either `guide` or `accounts`.
- `core/config.py` and `.env.example`: `EXTERNAL_HOSTS` and `EXTERNAL_TIMEOUT_S`.

**Tests**
- Unit (`test_external.py`):
  - parse saved sample responses
  - reject a bad code
  - reject a host that is not allowed
  - check that a timeout gives `ExternalError`
- Live (`TEST_EXTERNAL_LIVE=1`): one real call to each API.

**Display rule, for phase 5:** show FX as "Indicative ECB reference rate, *date*". Never present it as the client's card rate; the overseas fee comes from the knowledge base.

---

## Phase 3: Tool loop runner (not visible to clients)

**Goal:** one bounded loop that every agent shares.

**Files**
- `services/agents/runner.py`, with `run_agent(spec, task, inputs, ctx) -> AgentOutput`. The steps:
  1. Run the agent's rules first (a hook per spec). If a rule answers, return without calling the model.
  2. Make a strict-JSON **step** call through `llm_gateway.call`. A step is one of:
     - `{"calls": [{"tool": …, "args": {…}}]}`, which may include several independent calls that run in parallel
     - `{"answer": {…}}`
  3. Stop at these limits:
     - at most 4 tool calls in total
     - 30 s per agent
     - the per-request token budget that already exists
  4. If the model asks for a tool that is not in its spec, record it as a refused step; it never runs.
  5. Tool results go back to the model marked as untrusted evidence.
  6. Check the final answer with the existing `source_checks` validator against **all** the evidence gathered. If it fails, make one repair attempt, then withhold the answer.
- Keep the loop's state machine as a pure function, `advance(state, step_json)`, so tests can feed it sample step strings. This is the allowed kind of validator test; it is not a fake model.
- Prompts:
  - `prompts/accounts/v1/` holds `system.md` and `step.md`, plus `docs/prompt-log/accounts/v1.md`.
  - `prompts/runner_common/v1/` holds the step format, which every agent shares.
- `core/config.py`:
  - `AGENT_TIMEOUT_S=30` (today it is 65)
  - `TURN_BUDGET_S=60`
  - `PLANNER_MODEL`, which defaults to the agent model

**Tests**
- `test_runner.py`: `advance` with sample step strings covers:
  - the 4-call limit
  - a refused tool
  - parallel calls
  - a malformed step
  - an answer whose figures are not in the evidence, which must be withheld
- One focused real-provider test (opt-in) runs Accounts & Cards on "what's my card balance and last 3 transactions".

---

## Phase 4: Offers on the Action Service (not visible to clients)

**Goal:** save and run offers using the command ledger that already exists. There is no new offers table.

**Files**
- Migration `0097_offer_groups.sql` does five things:
  - Adds `offer_group uuid`, `group_order smallint` and `turn_id` to `actions.commands`.
  - Adds a new `public.action_offer(...)`, which is like `action_propose` but takes a TTL. `action_propose` today hard-codes 5 minutes, and offers need 30. Leave `action_propose` itself alone.
  - Adds `public.customer_open_offers(conversation_id)`, which returns the client's own unexpired proposed commands under RLS.
  - Expires every command and workflow turn that is still paused for a decision.
  - Adds a hosted copy under `backend/supabase/hosted/`.
- `services/offers.py`:
  - `save(offers, ctx, turn)`: the idempotency key is `case:turn.task:type`, and `offer_id` is the command id.
  - `open_group(ctx, conversation)` returns the open group.
  - `reply_kind(text)` reads English yes/no words only and returns `yes`, `no` or `other`.
  - `confirm(group_id, ctx)` confirms in `group_order` order. It passes each earlier receipt into the next target. If one fails, it stops and marks the rest as `failed`.
  - `cancel(group_id)` cancels the group.
- `services/authorization.py`: every action type that an agent proposes needs confirmation. `AUTO_TYPES` stay only for UI form paths such as uploads and checklist marks.

**Tests**
- Native SQL (`test_offer_groups.py`, `TEST_DATABASE_LOCAL=1`):
  - ownership and RLS
  - TTL
  - a double confirm records only once
  - ordered execution, and a failure stops the chain
  - old paused turns are expired
- Unit: `reply_kind` cases, including "yes but also…", which must return `other`.

---

## Phase 5: Backend switch-over (clients see it)

**Goal:** the new graph, the restructured agents and confirmations as chat turns, all in **one commit**. Keep the SSE `answer` event backwards-compatible, so the current UI keeps working until phase 6. A typed "yes" already confirms an offer.

**Graph** (`services/orchestrator.py`, rewritten): `planner → agent (Send ×N) → gather → compose`, and `planner → act → compose` for a confirmation turn. There is no checkpointer for chat turns.

- **`planner`**
  1. Rules first, in this order:
     - an open offer plus a yes/no reply, or a button `offer_id`, goes to `act`
     - an answer to our own question (which card, meeting time)
     - `record_context`
     - fraud or login wording, which goes to Security
     - an explicit RM request
     - greetings
  2. Then one call to `planner/v6` on `PLANNER_MODEL`, returning at most 5 `PlanTask`s.
  3. Then the rule overrides for priority and RM handover, and a plan check against `SPECS`.
  4. Guests get only `guide`.
  5. If the planner fails and no rule matched, reply with a fixed rephrase line.
- **`agent`**: `runner.run_agent`. History is the task's question, its inputs and the client's last 3 redacted messages. Assistant text is never included.
- **`gather`**: merges outputs and offers keyed by task id, then routes to `compose`. Phase 7 adds wave 2.
- **`act`**: `offers.confirm`, then `escalate_validation`.
- **`compose`**:
  - sections in plan priority order
  - fixed "skipped" lines
  - offers saved through `offers.save` and shown as text plus "reply *yes* to confirm"
  - a `section` SSE event as each agent finishes
  - an `offer` event
  - the final `answer`, unchanged

**Agents** (`services/agents/`)
- Five specs run through the runner, each with new prompt dirs at `v1`: `guide`, `accounts`, `security`, `compliance` and `relationship`.
- Move the rule answerers unchanged:
  - `card_answers`, `transaction_answers` and `account_answers` go to Accounts & Cards
  - `fraud_reports` goes to Security
  - `meeting_answers` and `request_status_answers` go to Relationship & Service
  - `client_guidance` goes to Guide
- Remove the old `retail`, `fraud`, `document` and `smalltalk` routing. Old prompt dirs stay, because released versions are never deleted.

**Removed**
- `propose_actions` and `review_actions`
- `interrupt`, `WorkflowPausedEvent` and `/workflows/{turn}/preview|resume`
- `resume_pending` and `prioritise` as separate nodes
- the `linked: card_fraud` flag
- the reading back of the bot's previous reply in `fraud_reports.pending`; the open offer replaces it

**Tests and docs in the same commit**
- Update the trajectories to the new node names: `test_graph_cases.py`, `qa1_*_cases.json`, `targets.py` and `docs/evaluation/test-cases.md`.
- Delete the pause tests together with the feature: `test_workflows*` covers only the pause.
- Rewrite `docs/how-agents-run.md` §2–§4.

**Done when:**
- Backend tests have no new failures against the baseline.
- `npm run test:e2e` passes for chat, urgent help and RM handover.
- One focused real-provider run of C1–C12 completes without errors.

---

## Phase 6: Web client (clients see it)

**Files**
- `Chat.jsx`, `Answer.jsx`, `lib/chatTools.js` (SSE parser):
  - After `planner`, show a placeholder per task in priority order.
  - Fill each one from its `section` event.
  - Never reorder sections.
- An offer group gets **one Confirm and one Cancel button**, which send `offer_id`. Show the expiry. When the group expires, the buttons are disabled with the line "Ask again if you still want this".
- Delete `CardBlockDialog.jsx`, `DisputeDialog.jsx` and the workflow-preview code. Leave the forms unchanged: card limit, uploads and card controls.

**Tests**
- Playwright: confirm by button, confirm by typed yes, cancel, expired offer, and two offers in one reply (buttons only).
- Update the unit tests: `chatTools.test.js` and `followUps.test.js`.

---

## Phase 7: Agent dependencies (clients see it)

**Files**
- `AgentOutput.exports` is computed by each agent's code, never by the model. Each export carries `source_ids`, and `source_checks` accepts an imported export as evidence.
- `PlanTask` gains `depends_on`, `condition` and `for_each`. The plan check verifies:
  - the names exist in the specs
  - there are no cycles
  - there are at most 2 waves (a reroute counts as a wave)
  - the plan does not mix roles
  - guests get no dependent tasks
- In `gather`, `next_wave` (§14.6) does four things:
  - It applies "unknown means no", with fixed skip wording.
  - It caps fan-out at 5, sorted by the field each list export declares, and adds "showing 5 of N".
  - It allows one handoff to `hands_to`, passing only the question.
- `act`: `after_offer` chains run in order, and a receipt fills the next target. This builds on phase 4.
- Code computes the rules: trip window, due dates inside it, and bank-holiday shifts. They follow the `retail_checks` style. If a date can't be parsed, the agent asks a clarifying question; it never guesses.
- Migration `0098_travel_notice.sql` adds the `travel_notice` request category and executor.
- Audit `export.passed {from, to, name, count}`.

**Tests:** `test_dependencies.py` covers plan checks, the router with sample outputs, provenance through the validator and chain ordering. Native SQL covers the travel notice.

---

## Phase 8: Evaluation and docs

1. New QA1 client cases. Each has a failure variant: the external API is down, the upstream agent is withheld, or an earlier receipt fails.
   - C23: travel and reschedule
   - C24: tax self-certification
   - C25: affordability, then a meeting
   - C26: a failed document check, then the RM
   - C27: a fraud report, then an RM note
   - a fee-waiver handoff case
2. Add the planner-only dataset (`planner_cases.json`): message → expected agents, entities and dependencies.
3. Run `langsmith_sync.py` and a scored experiment, then compare the speed with the phase 0 baseline.
4. Update the docs:
   - `architecture.md`: §5.5 and §5.6 describe v5 as built, and §14 becomes history.
   - `AGENTS.md`: the rows for orchestrator, agents, offers and external.
   - `docs/evaluation/README.md`
   - `planning/status.md`

---

## Phase 9 (later): RM assistant on the same graph

Replace `services/rm/assistant.py` with two specs, RM Client Insight and RM Service Desk. The graph is chosen by the verified `rm` role. Add R21–R23. This phase starts only after phases 1–8 are merged.

---

## Decisions settled on 3 Nov 2026

| Topic | Decision |
|---|---|
| Rollout | Replace in place. Phases 1–4 add parts nobody uses yet. Switch-over is phase 5 (backend) plus phase 6 (UI). The v1 pipeline is untouched; which pipeline is the demo default is decided after phase 8 |
| Graph | 5 nodes: `planner`, `agent`, `gather`, `act`, `compose`. At most 2 waves. No checkpointer for chat turns. A crash marks the turn failed and the client retries, which is safe because offers are idempotent |
| Planner | Rules, then one model call, then rules and a plan check. `PLANNER_MODEL` defaults to the agent model. At most 5 tasks. Rules are English-only. On failure, a fixed rephrase line. `CLIENT_INTENT_ROUTING=rules` stays as an offline fallback |
| Tool loop | Strict-JSON steps through `llm_gateway`, not native function calling. At most 4 tool calls, 30 s per agent, 60 s per turn |
| Agents | 5 client agents (§5.6.1) with new prompt dirs at `v1`. Rule answerers move unchanged. Dependent history is the question, its inputs and the client's last 3 messages |
| Interactions | Exports are computed by code and carry provenance. Unknown means no. One handoff, within `hands_to`. Fan-out is capped at 5, sorted by a declared field. A condition that can't be parsed leads to a clarifying question |
| Offers | A proposed command on the Action Service plus `offer_group` and `group_order`. 30 min TTL. One button per group. A typed yes counts only in English and with exactly one open group. Every agent write needs confirmation; `AUTO_TYPES` stay only for UI forms. Forms are unchanged |
| External | Nager.Date for holidays and Frankfurter (ECB) for FX. Codes only. Unit tests use sample responses; live tests are opt-in. Failures are reported in that section |
| Contracts | No separate team review, so the AGENTS.md rule is updated in phase 1 |
| RM | Phase 9, after the client side. R21–R23 are deferred until then |
