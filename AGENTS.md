# AGENTS.md

Navigation guide for coding agents. DemoBank customer-support chatbot (IS4000 coursework demo, not a real bank). React/Vite frontend, FastAPI backend, Supabase (auth + data), OpenAI-compatible LLM via LangChain (OpenRouter). Use synthetic data only.

## Where to look

| I want to... | Go to |
|---|---|
| Change the request/response flow or SSE events | `backend/app/api/chat.py` (`POST /chat`: `status` -> `answer` -> `done`/`error`; `GET /knowledge/{id}`) |
| Change how an answer is produced | `backend/app/services/answers.py` (`make_answer`) |
| Edit the LLM call / structured output | `backend/app/services/llm.py` (strict JSON: `answer`, `source_ids`, `answer_kind`) |
| Switch API / local Codex subscription | `CHAT_TRANSPORT=api\|codex`; `services/codex_cli.py`; `docs/codex-local-provider.md`; `handoff.md` for another Mac. Preserve OpenRouter settings. Never pass app keys to CLI or enable its coding tools. |
| Edit the system prompt | `backend/app/prompts/<version>/{system,format,public_only}.md`, loaded by `services/prompts.py`. **Never edit a released version**; add a new `vN` dir and a `docs/prompt-log/vN.md` (copy `TEMPLATE.md`) |
| Add or edit knowledge articles / retrieval | `backend/app/services/knowledge.py` (in-memory, keyword search, no embeddings) |
| Change answer validation (source IDs, figures) | `backend/app/services/source_checks.py` |
| Change redaction | `backend/app/services/privacy.py` **and** `frontend/src/lib/chatTools.js` (keep in sync) |
| Change lost-card detection | `backend/app/services/safety.py`; fixed urgent response stays in `answers.py` |
| Auth, JWT, per-user DB client, customer records | `backend/app/core/auth.py` |
| Database schemas, RLS, persona seed | `backend/supabase/migrations/` (`NNNN_name.sql`, next free number, never edit an applied one); `0002` banking baseline, `0003` business schemas (read-only customer policies via `banking.my_customer_id()`), `0004` seed for Alex/Michelle/David (ids 1001-1003, spec in `docs/synthetic-data-spec.md`). New schemas must also be added to the Data API's exposed schemas (`events` stays unexposed) |
| Settings / env vars | `backend/app/core/config.py`, `.env.example` |
| Audit log | `backend/supabase/migrations/0005_audit_log.sql` (table, `audit.record()`, hash chain, `verify_chain`), `0006_audit_row_triggers.sql` (row trigger + audited tables); `app/services/audit.py` (per-request batch, flushed before `answer`, failure fails the request); `scripts/verify_audit.py`; contract `AuditEvent` |
| Logging / tracing | `backend/app/core/observability.py` (JSON logs, ids/counts only); plan in `docs/tracing-plan.md`; tables in `backend/supabase/migrations/` |
| Request/response schemas | `backend/app/schemas/chat.py` (request), `schemas/events.py` (SSE payloads) |
| Shared pipeline contracts (architecture §4) | `backend/app/schemas/contracts.py`; change only by PR reviewed by all developers |
| v2 Customer Data (real Supabase reads with the user's JWT + code-side `customer_id` filter; not used by the live v1 path) | `services/customer_data.py` (`get_*`, incl. `get_compliance_case`, `get_rm_assignment`); tests in `tests/test_customer_data.py` |
| v2 service stubs (sample data only, never used by the live v1 path) | `services/kb.py` (`search`, `get_fact`), `services/actions/` (`run_action`, `registry.py`, `cards.py`, `handoffs.py`), `services/policy.py` (`decide`, `has_consent`); signatures pinned in `tests/test_stubs.py` |
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

- Backend tests: `backend/tests/` (`test_chat`, `test_customer_scope`, `test_knowledge_checks`, `test_source_units`, `test_llm_contract`, `test_prompts`, `test_health`; `test_rls_live` is opt-in via `TEST_PERSONA_FIXTURE_FILE` (persona logins in git-ignored `personas.json`); browser suites use the normal application and real provider).
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
