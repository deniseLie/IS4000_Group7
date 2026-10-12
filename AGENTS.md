# AGENTS.md

Navigation guide for coding agents. DemoBank customer-support chatbot (IS4000 coursework demo, not a real bank). React/Vite frontend, FastAPI backend, Supabase (auth + data), OpenAI-compatible LLM via LangChain (OpenRouter). Use synthetic data only.

## Where to look

| I want to... | Go to |
|---|---|
| Change the request/response flow or SSE events | `backend/app/api/chat.py` (`POST /chat`: `status` -> `answer` -> `done`/`error`; `GET /knowledge/{id}`) |
| Change how an answer is produced | `backend/app/services/answers.py` (`make_answer`) |
| Edit the LLM call / structured output | `backend/app/services/llm.py` (strict JSON: `answer`, `source_ids`) |
| Edit the system prompt | `backend/app/prompts/<version>/{system,format,public_only}.md`, loaded by `services/prompts.py`. **Never edit a released version**; add a new `vN` dir and a `docs/prompt-log/vN.md` (copy `TEMPLATE.md`) |
| Add or edit knowledge articles / retrieval | `backend/app/services/knowledge.py` (in-memory, keyword search, no embeddings) |
| Change answer validation (source IDs, figures) | `backend/app/services/source_checks.py` |
| Change redaction | `backend/app/services/privacy.py` **and** `frontend/src/lib/chatTools.js` (keep in sync) |
| Auth, JWT, per-user DB client, customer records | `backend/app/core/auth.py` |
| Settings / env vars | `backend/app/core/config.py`, `.env.example` |
| Logging / tracing | `backend/app/core/observability.py` (JSON logs, ids/counts only); plan in `docs/tracing-plan.md`; tables in `backend/supabase/migrations/` |
| Request/response schemas | `backend/app/schemas/chat.py` |
| Chat UI | `frontend/src/Chat.jsx`; session/guest/login in `App.jsx`, `Login.jsx`; helpers (redact, history bounding, SSE parser) in `lib/chatTools.js`; Supabase client in `lib/supabase.js` |
| Evaluate quality | `backend/evaluation/` (`run.py`, `cases.json` live, `offline_cases.json`) |

Legacy and unused: `frontend/src/api/chat.js`, `frontend/src/components/ChatWindow.jsx`. Ignore `backend/chatbot_backend.egg-info/` and `backend/evaluation/{before,after}.*` (generated artifacts).

## Answer pipeline (`make_answer`)

1. Redact and trim history.
2. Fixed no-LLM responses: secrets detected, lost/stolen card, human-support request, guest asking about "my" balance.
3. Keyword-retrieve articles (all current ones for non-English); add `customer-records` evidence in personal mode.
4. Call the LLM.
5. Validate: source IDs valid, figures present in cited evidence, no leaked secrets/URLs, no "I have blocked/transferred..." claims. Failure -> generic "could not verify" answer with `needs_human=True`.

## Tests and docs

- Backend tests: `backend/tests/` (`test_chat`, `test_customer_scope`, `test_knowledge_checks`, `test_llm_contract`, `test_prompts`, `test_health`; `browser_server.py` is the deterministic server for e2e).
- Frontend tests: `frontend/src/lib/chatTools.test.js` (unit), `frontend/tests/chat.spec.js` (Playwright, deterministic model), `frontend/tests/live.spec.js` (live services).
- Docs: `README.md` (setup, known limits), `docs/chatbot-architecture.md`, `docs/multi-capability-architecture.md`, `docs/prompt-log/`, `docs/tracing-plan.md`.

## Commands

Backend (Python 3.12, run from `backend/`; venv at repo root `.venv`, on Windows `.venv\Scripts\python.exe`):

```bash
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
../.venv/bin/python -m pytest -q
../.venv/bin/python -m evaluation.run --output evaluation-results.json   # offline, no model calls
../.venv/bin/python -m evaluation.run --live                             # opt-in, real model
```

Frontend (Node 22, run from `frontend/`): `npm ci`, `npm run dev`, `npm test`, `npm run build`, `npm run test:e2e` (ports 8037/5177), `npm run test:live` (needs `TEST_ENV_FILE=../.env`).

## Invariants (do not break)

- Customer queries filter by `customer_id` in code **and** rely on RLS. Never use a service-role key in app or browser; never disable RLS.
- Only `VITE_`-prefixed env vars reach the browser.
- Safety-critical cases (lost card, secrets, human support) stay as fixed responses that never call the model.
- External-publisher knowledge articles need `reference_url`, `checked_on`, `review_due`, and an allowed domain (`www.moneysense.gov.sg`, `www.abs.org.sg`). Bump version/dates when editing a summary.
- Logs record ids, counts and outcomes only, never user text or customer records.
- Never commit `.env` or credentials. Only commit or push when asked. Never commit `CLAUDE.md` to `main`.
- Code style is compact; match the surrounding code.
