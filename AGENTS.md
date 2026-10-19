# AGENTS.md

Navigation guide for coding agents. DemoBank customer-support chatbot (IS4000 coursework demo, not a real bank). React/Vite frontend, FastAPI backend, Supabase (auth + data), OpenAI-compatible LLM via LangChain (OpenRouter). Use synthetic data only.

## Where to look

| I want to... | Go to |
|---|---|
| Change the request/response flow or SSE events | `backend/app/api/chat.py` (`POST /chat`: `status` -> `answer` -> `done`/`error`; `GET /knowledge/{id}`) |
| Change how an answer is produced | `backend/app/services/answers.py` (`make_answer`) |
| Work on the v2 pipeline | `PIPELINE=v2`; `services/orchestrator.py` (LangGraph; runtime identity outside state), `services/agents/`, `schemas/agents.py`, `Answer.jsx`. Public agents and the first personal fraud review workflow are connected; other personal agents and hosted deployment remain pending. `KNOWLEDGE_STORE=memory\|database` explicitly selects retrieval; v1 stays the default |
| Edit the LLM call / structured output | `backend/app/services/llm_gateway.py` (`call`: the one client for planner and agents; strict JSON check, timeout, retries on transient errors, per-request token budget, `llm` span + `llm_call` log; raises `LLMError`). Answer prompt/schema in `services/llm.py` (`answer`, `source_ids`, `answer_kind`) |
| Switch API / local Codex subscription | `CHAT_TRANSPORT=api\|codex`; `services/codex_cli.py`; `docs/codex-local-provider.md`; `handoff.md` for another Mac. Preserve OpenRouter settings. Never pass app keys to CLI or enable its coding tools. |
| Edit the system prompt | `backend/app/prompts/<agent>/<version>/*.md` (answer agent: `system,format,public_only`), loaded by `services/prompts.py` (`load(agent, version)`; `PROMPT_VERSION` for answer, `PROMPT_VERSIONS` for others). **Never edit a released version** (ids pinned in `tests/test_prompts.py`); add a new `vN` dir and a `docs/prompt-log/<agent>/vN.md` (copy `TEMPLATE.md`) |
| Add or edit knowledge articles / retrieval | `backend/app/services/knowledge.py` (in-memory, keyword search, no embeddings) |
| Database knowledge layer | Migrations `0008` (versioned articles/sections/facts, RLS, ranked keyword RPCs), `0009` (approved library seed), `0011` (exact source lookup); `services/kb.py`, `schemas/knowledge.py`, `core/data_api.py`, `api/knowledge.py`. Native SQL/SDK/API checks in `test_knowledge_database.py` and `test_knowledge_api.py`. Hosted apply and live AI/browser checks of database-backed answers are pending. |
| Change answer validation (source IDs, figures) | `backend/app/services/source_checks.py` |
| Change redaction | `backend/app/services/privacy.py` **and** `frontend/src/lib/chatTools.js` (keep in sync) |
| Change lost-card detection | `backend/app/services/safety.py`; fixed urgent response stays in `answers.py` |
| Assess input risk / choose a fixed safety route | `backend/app/services/guardrail.py` (`assess_request` returns `RiskAssessment`; counts redactions, flags suspected fraud, preserves fixed no-model routes). Called before personal data loading and by the answer pipeline |
| Auth, JWT, per-user DB client, customer records | `backend/app/core/gateway.py` (API Gateway Layer: size limit, JWT, rate limit, `RequestContext`, `auth.session_started`; routes use `Depends(get_ctx)` and `request.state.db`), `core/auth.py` (JWKS verify, user DB client, customer records), `core/ratelimit.py`, `core/errors.py` (stable error codes), `api/auth.py` (`POST /auth/session-end`); migration `0007` |
| Database schemas, RLS, persona seed | `backend/supabase/migrations/` (`NNNN_name.sql`, next free number, never edit an applied one); `0002` banking baseline, `0003` business schemas (read-only customer policies via `banking.my_customer_id()`), `0004` seed for Alex/Michelle/David (ids 1001-1003, spec in `docs/synthetic-data-spec.md`). New schemas must also be added to the Data API's exposed schemas (`events` stays unexposed) |
| Settings / env vars | `backend/app/core/config.py`, `.env.example` |
| Audit log | `backend/supabase/migrations/0005_audit_log.sql` (table, `audit.record()`, hash chain, `verify_chain`), `0006_audit_row_triggers.sql` (row trigger + audited tables); `app/services/audit.py` (per-request batch, flushed before `answer`, failure fails the request); `scripts/verify_audit.py`; contract `AuditEvent` |
| Logging / tracing | `backend/app/core/observability.py` (JSON logs, ids/counts only); plan in `docs/tracing-plan.md`; tables in `backend/supabase/migrations/` |
| Request/response schemas | `backend/app/schemas/chat.py` (request), `schemas/events.py` (SSE payloads) |
| Shared pipeline contracts (architecture §4) | `backend/app/schemas/contracts.py`; change only by PR reviewed by all developers |
| Customer Data | `services/customer_data.py`, `schemas/customer_data.py`, `api/customer.py`; migrations `0028`/`0029` use caller JWT + invoker RLS + explicit SQL customer predicates, returning atomic read-audit/call-id/as-of snapshots. `RecordsPanel.jsx` shows recorded synthetic data, including filtered transactions. `api/relationship.py` and `schemas/relationship.py` cover requests/slots; migration `0012` excludes private booking conflicts. Native `test_customer*.py` and relationship suites cover SQL/HTTP/UI. Hosted apply, other personal agents and booking writes remain pending. |
| Tool authorisation | `services/authorization.py` (`decide` is pure; `require_permission` audits and enforces); customer readers and v2 knowledge retrieval use it. `RequestContext.agent` is backend-assigned. Unknown tools, scope mismatches and staff operations are denied; staff/system access is not configured. |
| Consent | `services/consent.py` (`has_consent(query, ctx, db)`), models in `schemas/consent.py`; migration `0010` checks exact scope/category coverage, revocation and wall-clock expiry under JWT + RLS. `api/consent.py`: `POST /consent/check`, `GET /consent/rm`; audit must flush before release. Read-only UI in `SharingPanel.jsx`. Grant/revoke and sharing writes still require the Action Service. |
| Data API calls | `core/data_api.py`: public-key/JWT connector and bounded transient retries with warnings, then raises the last error. Writes using `execute()` must have database idempotency keys; business conflicts are non-transient. |
| Conversation storage | Migrations `0013`/`0014`; `CONVERSATION_STORE=off\|database`; `schemas/conversations.py`, `services/conversations.py`, `api/conversations.py`, `api/chat_store.py`, `SavedChats.jsx`. Saved `/chat` uses server history, emits `conversation` SSE metadata, records guarded questions before provider work and preserves explicit failures. Customer reads retain RLS; guest capabilities expire after 24 hours. Hosted apply remains pending. Native API/UI checks cover scope, expiry, conflicts, cancellation and rollback. |
| Durable workflow checkpoints | Migrations `0015`/`0016` (pending writes may precede snapshots), `services/checkpoints.py` (owned Data API saver, HMAC envelopes, stable private `CHECKPOINT_SIGNING_KEY`), `checkpoint_codec.py` (closed JSON models; no credentials/pickle/imports), `tests/test_checkpoints.py`. Saved v2 turns use synchronous durability. Automatic interrupted-model-request recovery remains pending. |
| Action proposals and execution | `services/actions/` (`run_action`, typed executor contracts, scoped reads); `schemas/actions.py`, `api/actions.py`, migrations `0017`–`0019` (private executors, atomic writes/receipts/audit/outbox). `ActionsPanel.jsx` reviews real proposals. Synthetic card blocks and customer support handoffs are implemented; other executors and worker delivery remain pending. Shared signatures stay pinned in `test_stubs.py`. |
| Agent action pause/resume | Migrations `0020`–`0022`; `services/workflow_store.py`, `workflows.py`, `schemas/workflows.py`, `services/agents/fraud.py` (personal v2 prompt, masked evidence and bounded proposals), `orchestrator.py` review/settlement nodes. SSE `workflow_paused`; Actions confirm/reject and explicit `/workflows/{turn}/resume` retry. Runtime context carries ownership, never checkpoints. `test_workflows.py`/`test_workflows_browser.py` use real graph/SQL mechanics after dispatch with safety-origin fixtures, not fake AI. Live AI proposal accuracy remains unverified (HTTP 429); hosted apply is deferred. Manual native preview: `tests/preview_actions.py`. |
| Notification delivery / inbox | `app/worker.py` (restricted login, queue polling and recorded-deadline scheduler), `core/lifecycle.py` (opt-in API worker and health), migrations `0023`–`0027` (versioned templates, preferences, transactional delivery/retry/audit, inbox read RPCs, Realtime publication). `services/notifications.py`, `api/notifications.py`, `schemas/notifications.py`; `NotificationsPanel.jsx`, `lib/notifications.js`. Worker login provisioning and reminder window are in the worker docstring. Native `test_notifications*.py` covers real worker/JWT/API/UI, not hosted Realtime. English templates only; hosted activation, pg_cron, email and worker-started AI turns are pending. |
| Chat UI | `frontend/src/Chat.jsx`; session/guest/login in `App.jsx`, `Login.jsx`; `components/Panel.jsx` (accessible source/support dialogs), `components/Icon.jsx` (local SVG icons), `index.css` (responsive theme); helpers (redact, history bounding, SSE parser) in `lib/chatTools.js`; Supabase client in `lib/supabase.js` |
| Evaluate quality | `backend/evaluation/` (`run.py`, `cases.json` live, `offline_cases.json`) |

Legacy and unused: `frontend/src/api/chat.js`, `frontend/src/components/ChatWindow.jsx`. Ignore `backend/chatbot_backend.egg-info/` and `backend/evaluation/{before,after}.*` (generated artifacts).

## Answer pipeline (`make_answer`)

1. Redact and trim history.
2. Fixed no-LLM responses: secrets detected, lost/stolen card, human-support request, guest asking about "my" balance.
3. Keyword-retrieve articles. Use the small current library when there is no match or for non-English queries; never rank all previous questions as the latest query. Add `customer-records` evidence only in personal mode.
4. Call the LLM.
5. Banking answers require valid sources and supported figures. Short conversational replies have no sources or figures. All replies are checked for leaked secrets/URLs and action claims. These checks do not prove factual correctness or correct model classification. Failure -> withheld answer with `needs_human=True`.
6. Report origin to the UI: `ai`, `safety`, or `validation`. Never substitute stored article text for an AI answer.

## Tests and docs

- Backend tests: `backend/tests/` (`test_chat`, `test_customer_scope`, `test_knowledge_checks`, `test_source_units`, `test_llm_contract`, `test_llm_gateway`, `test_prompts`, `test_health`, `test_gateway` (JWT, rate limit, size limit, session events with locally minted tokens); `test_rls_live` is opt-in via `TEST_PERSONA_FIXTURE_FILE` (persona logins in git-ignored `personas.json`); browser suites use the normal application and real provider).
- Native database checks: `backend/tests/test_database_local.py` (setup/run instructions in its module docstring; optional `dbtest` dependencies). Applies unchanged migrations to temporary PostgreSQL after test-only platform/legacy prerequisites; tests SQL RLS and audit transactions. It does not run hosted Auth, the Data API or Realtime.
- Native Data API checks: `backend/tests/test_data_api_local.py`, fixtures in `tests/data_api_support.py` (both local opt-in flags; setup in its docstring). Uses pinned official PostgREST installed by `python -m scripts.install_postgrest` into `.venv`; real SDK/HTTP, JWKS, JWT, RLS and API audit RPC calls. This covers the local Data API boundary, not hosted Auth/password login or Realtime.
- Native consent browser checks: `backend/tests/test_consent_browser.py` (setup/run instructions in its module docstring). Starts the normal frontend/backend against native PostgREST/PostgreSQL; local signed sessions exercise API verification, RLS and consent status at desktop/mobile sizes. It does not simulate a model or exercise hosted password login.
- Saved-chat browser checks: `backend/tests/test_conversation_browser.py`; manual Computer Use preview in `tests/preview_native.py` (setup in its module docstring). Uses the normal application and real native services; no model or hosted password login is simulated.
- Frontend tests: `frontend/src/lib/chatTools.test.js` (unit), `frontend/tests/chat.spec.js` (Playwright, real backend safety paths), `frontend/tests/live.spec.js` (live services).
- Docs: `README.md` (setup, known limits), `docs/chatbot-architecture.md`, `docs/multi-capability-architecture.md`, `docs/prompt-log/`, `docs/tracing-plan.md`, `docs/synthetic-data-spec.md`.
- Active completion evidence: `docs/core-completion-plan.md`. Explicit currency/percentage checks and limited English attribution checks are not semantic proof. Do not count offline validation as live AI accuracy.
- Shared synthetic privacy cases: `tests/fixtures/privacy-cases.json`, exercised by both frontend and backend. Keep ordinary password-reset questions and non-secret Unicode text unchanged.
- Focused live evaluation: `python -m evaluation.run --live --suite focused` (three real source questions; excludes deferred complex checks). Offline evidence must include publisher/scope just like the answer pipeline. Raw live results require review; `docs/core-results-review.md` records the latest assessment and limitations.

## Commands

Backend (Python 3.12, run from `backend/`; venv at repo root `.venv`, on Windows `.venv\Scripts\python.exe`):

```bash
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
../.venv/bin/python -m pytest -q
../.venv/bin/python -m evaluation.run --output evaluation-results.json   # offline, no model calls
../.venv/bin/python -m evaluation.run --live                             # opt-in, real model
```

Frontend (Node 22, run from `frontend/`): `npm ci`, `npm run dev`, `npm test`, `npm run build`, `npm run test:e2e` (real provider, ports 8038/5178), `npm run test:live` (needs `TEST_ENV_FILE=../.env`).

## Invariants (do not break)

- Customer queries filter by `customer_id` in code **and** rely on RLS. Never use a service-role key in app or browser; never disable RLS.
- Only `VITE_`-prefixed env vars reach the browser.
- Safety-critical cases (lost card, secrets, human support) stay as fixed responses that never call the model.
- External-publisher knowledge articles need `reference_url`, `checked_on`, `review_due`, and an allowed domain (`www.moneysense.gov.sg`, `www.abs.org.sg`). Bump version/dates when editing a summary.
- Logs and audit events record ids, counts and outcomes only, never user text or customer records. `audit.events` is append-only: never grant update/delete on it or edit a released audit migration.
- Never commit `.env` or credentials. Only commit or push when asked. Never commit `CLAUDE.md` to `main`.
- Code style is compact; match the surrounding code.

Do not add a fake model or a server that replaces the AI client. Unit tests may check sample strings directly through validators; browser model checks must use the real provider. Provider failures must be reported, not replaced with sample answers.
