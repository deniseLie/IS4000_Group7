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
- **handover to her relationship manager**: anything the assistant can't or shouldn't answer goes to the RM as a ticket with a `TCK-` reference, which she follows in **My requests**;
- **meetings** with her RM (My requests → Meetings) and **monthly statements** she can print or save as PDF (Overview → Statements).

Stories and IDs are in [docs/planning/product-backlog.xlsx](docs/planning/product-backlog.xlsx): F1–F28 foundation, CR1–CR18 client, RM1–RM13 relationship manager. What's done and what's open is in [docs/planning/status.md](docs/planning/status.md).

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
docs/               how the agents run, architecture, workflows, evaluation, planning
tests/fixtures/     privacy cases shared by backend and frontend tests
```

## Docs (read in this order)

| Doc | What it covers |
|---|---|
| [docs/how-agents-run.md](docs/how-agents-run.md) | One message end to end: the orchestrator, which agent runs, tools, actions, tickets, the RM handover and document checks |
| [docs/workflows/workflow-1-client.md](docs/workflows/workflow-1-client.md) | The client persona and her journeys |
| [docs/workflows/workflow-2-relationship-manager.md](docs/workflows/workflow-2-relationship-manager.md) | The relationship manager plan |
| [docs/architecture.md](docs/architecture.md) | Full design: components, data contracts, security, audit, knowledge base (appendix) |
| [docs/evaluation/README.md](docs/evaluation/README.md) | How answers are tested (LangSmith) and earlier results |
| [docs/evaluation/test-cases.md](docs/evaluation/test-cases.md) | Michelle's 10 test cases (QA1) and the run record |
| [docs/planning/status.md](docs/planning/status.md) | What is done and what is open |

`AGENTS.md` is a code map for AI coding tools, and `docs/prompt-log/` holds one note per prompt version (required by the tests); neither needs reading.

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

**5. Reminders (optional).** Deadline reminders reach the Inbox only while the notification worker runs. It needs its own restricted database login, never the `postgres` owner:
1. In the Supabase SQL editor, create the login (choose your own password and keep it out of Git):
   ```sql
   create role demobank_reminders login password '<your password>';
   grant demobank_worker to demobank_reminders;
   ```
2. In Supabase, open **Connect** and copy the **Session pooler** connection string. Replace the user `postgres` with `demobank_reminders` (keep the `.<project-ref>` after it) and put in the password. Save it in `backend/.env` as `WORKER_DATABASE_URL=…`.
3. Set `RUN_WORKER_IN_API=true` in `backend/.env` and restart the backend. (Or leave it `false` and run `../.venv/bin/python -m app.worker` from `backend/` in a third terminal.)

Reminders are sent from 7 days before a due date, only for documents she still has to send, and disappear from her Inbox once she has sent everything (or the file the bank asked for). Michelle's review is due 30 Nov, so to see one today, ask for a correction due in 3 days (see *Demo data*); it reaches her Inbox within a minute.

## If something doesn't work

| What happens | What to check |
|---|---|
| `vite: command not found` | Run `npm ci` in `frontend/` (the zip has no `node_modules`). |
| `No module named …` | Recreate `.venv` and run `pip install -e './backend[dev,mcp]'`. |
| "Document checks are not switched on yet" | `DOCUMENT_PRECHECK_SIGNING_KEY` missing or wrong in `backend/.env`; restart the backend. |
| The assistant doesn't use Michelle's records | `PIPELINE=v2` and `CONVERSATION_STORE=database` in `backend/.env`; restart the backend. |
| Browser can't reach the backend | Both terminals running; frontend started with `VITE_API_URL=/api BACKEND_PORT=5001`. |
| Nothing appears in LangSmith | `LANGSMITH_TRACING=true` and `LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com` (the API host, not the website); restart. |
| "Answer withheld" | The model's answer failed the fact check and was passed to the RM. That is expected behaviour, not a crash. |

## Demo data

Michelle is customer 1002 (`demo.michelle@example.com`). It is all seeded by the migrations:
- account ****7734 and Signature Card 7310, with July–October activity for her statements (`0004`, `0063`, `0077`);
- on 27 Oct, card 7310 used in Singapore and then in London within 2 hours (the unrecognised-charge demo), and a pending SGD 12,000 transfer to Lim Interiors (`0075`);
- a periodic review due 30 Nov 2026 with four requested documents (`0004`, `0056`), and demo tickets (`0071`).

Other test customers exist only in the local test database; `backend/supabase/hosted/06_keep_only_michelle.sql` removes them from the hosted project.

**The bank's decision on a document.** Until the RM workflow exists, record it in the SQL editor after Michelle submits a file. Find the item id with `select item_id,label,submission_status from compliance.requested_items where customer_id=1002;`, then:
```sql
select compliance.record_bank_decision('<item_id>','accepted');
select compliance.record_bank_decision('<item_id>','rejected','The bill is older than 3 months.');
select compliance.record_bank_decision('<item_id>','rejected','Please send a clearer scan.',current_date+3);  -- due in 3 days, so a reminder is sent
```
A correction shows on her Compliance review as "The bank asked you" and closes by itself when she uploads a new file for that item.

**The bank's decision on her answer to a question.** She answers a *The bank asked you* question in the reply box on her Compliance review; it then shows "Sent to the bank on … – waiting for review". Find the request id with `select request_id,question,status,response from compliance.info_requests where customer_id=1002;`, then:
```sql
select compliance.record_request_decision('<request_id>','accepted');
select compliance.record_request_decision('<request_id>','more_info','Please also send the sale agreement.');  -- opens a follow-up question
```

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
