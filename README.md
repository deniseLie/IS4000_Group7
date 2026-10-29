# DemoBank – AI banking assistant (IS4000 Group 7)

A coursework demo of an AI assistant for a private bank. FastAPI backend with a LangGraph multi-agent pipeline, React (Vite) frontend, Supabase for login and data, an OpenRouter model through LangChain, and LangSmith for tracing. All data is fictional. It is not a real bank product.

## The two workflows

| Workflow | Who | Status | Doc |
|---|---|---|---|
| 1 – Client | Michelle (`demo.michelle@example.com`) | Built | [docs/workflows/workflow-1-client.md](docs/workflows/workflow-1-client.md) |
| 2 – Relationship manager | Sophie | Planned, not built | [docs/workflows/workflow-2-relationship-manager.md](docs/workflows/workflow-2-relationship-manager.md) |

The client uses the assistant for:
- everyday banking questions, within guardrails;
- her compliance review: checklist, upload, a quick template check, then submit;
- **handover to her relationship manager**: anything the assistant can't or shouldn't answer goes to the RM as a ticket with a `TCK-` reference, which she follows in **My requests**.

Stories and IDs are in [docs/planning/product-backlog.xlsx](docs/planning/product-backlog.xlsx): F1–F28 foundation, CR1–CR17 client, RM1–RM13 relationship manager. What's done and what's open is in [docs/planning/status.md](docs/planning/status.md).

## Folder map

```
backend/
  app/
    api/            HTTP routes (chat, documents, tickets, customer data, actions…)
    core/           config, auth/JWT gateway, Data API client, logging, LangSmith client
    services/       the logic: orchestrator, guardrail, validators, document checks, tickets…
      agents/       the five agents: retail, fraud, compliance, document, relationship
      actions/      every change goes through here (card block, handoff, ticket, upload…)
    prompts/        prompts by agent and version (never edit a released version)
    schemas/        Pydantic models
  supabase/
    migrations/     database schema and synthetic data (never edit an applied one)
    hosted/         SQL to paste into the hosted Supabase project, in order
  evaluation/       test cases and the LangSmith dataset sync
  scripts/          helpers: hosted SQL, audit walkthrough, staff CLI, MCP server, OCR
  tests/            backend tests
frontend/
  src/              Chat.jsx (the app), components/, lib/ (data and helpers), index.css
  tests/            Playwright browser tests
docs/               architecture, how the agents run, workflows, setup, evaluation, planning
tests/fixtures/     privacy cases shared by backend and frontend tests
```

Start reading with [docs/how-agents-run.md](docs/how-agents-run.md). It covers what happens when a message comes in, which agent runs, and how actions, tickets and the RM handover work.

## Run it locally

You need Python 3.12 and Node.js 22. You don't need Docker.

**1. Settings.** Copy `.env.example` to `backend/.env` and fill in:
- the Supabase URL and anon key;
- the model key;
- `CHECKPOINT_SIGNING_KEY` (any long random string);
- `DOCUMENT_PRECHECK_SIGNING_KEY`, from `select secret from compliance.precheck_keys where key_id='v1';` in the SQL editor.

Keep `PIPELINE=v2` and `CONVERSATION_STORE=database`. Never use a service-role key.

**2. Install (first time).**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev,mcp]'
cd frontend && npm ci && cd ..
```

**3. Start.** In one terminal:
```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 5001
```
In a second terminal:
```bash
cd frontend
VITE_API_URL=/api BACKEND_PORT=5001 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```
Then open http://127.0.0.1:5173 and sign in as Michelle.

**4. Hosted database.** Apply any new SQL from `backend/supabase/hosted/` in order. Its README lists what each file does.

## Tests

```bash
cd backend
../.venv/bin/python -m pytest -q                                          # unit tests, ~10 s
../.venv/bin/python -m pip install -e '.[dbtest]' && ../.venv/bin/python -m scripts.install_postgrest   # once
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 ../.venv/bin/python -m pytest -q   # + real local database, ~3 min
cd ../frontend && npm test                                                # frontend unit tests
```

- The local database tests start a temporary PostgreSQL and PostgREST. They never touch the hosted project.
- Browser tests (`npm run test:e2e`, `playwright.native.config.js`) call the real AI provider and are opt-in.
- Offline answer evaluation: `python -m evaluation.run --output evaluation-results.json`. See [docs/evaluation/README.md](docs/evaluation/README.md).

## Tracing and audit

- **Tracing.** Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to send each chat request to LangSmith as one run, with the graph nodes, agents and model calls nested inside. Text is redacted before upload (`core/langsmith_client.py`).
- **Audit log.** Every request writes an append-only, hash-chained audit trail in Supabase (`audit.events`). It holds ids and counts only, never chat text. Run `python -m scripts.audit_walkthrough --case-id … --database-url … --markdown` to print one case end to end.

## Rules for contributors

- RLS stays on, and every customer query filters by `customer_id`. Only `VITE_` variables reach the browser.
- Lost card, shared secrets and "talk to a person" are fixed responses that never call the model.
- Don't edit a released migration or prompt version. Add a new one, with a prompt-log note for prompts (`docs/prompt-log/`).
- Never commit `.env` files or keys. More detail for coding agents is in [AGENTS.md](AGENTS.md).
