<!-- Source review package: generated from original code; caches, local credentials and Git internals excluded. -->
# DemoBank – AI banking assistant (IS4000 Group 7)

A coursework demo of an AI assistant for a private bank. Both workflows are built: the client assistant (Michelle) and the relationship manager workspace and assistant (Sophie). FastAPI backend with a LangGraph multi-agent pipeline, React (Vite) frontend, Supabase for login and data, an OpenRouter model through LangChain, and LangSmith for tracing. All data is fictional. It is not a real bank product.

## The two workflows

| Workflow | Who | Status | Doc |
|---|---|---|---|
| 1 – Client | Michelle (`demo.michelle@example.com`) | Built | [docs/workflows/workflow-1-client.md](docs/workflows/workflow-1-client.md) |
| 2 – Relationship manager | Sophie (`demo.sophie@example.com`, app_metadata `demobank_role=rm`) | Built (1 Nov 2026: RM1–RM12, RM16) | [docs/workflows/workflow-2-relationship-manager.md](docs/workflows/workflow-2-relationship-manager.md) |
| AI engineering (admin) | `demo.admin@example.com` (app_metadata `demobank_role=admin`; create the Auth user, then run `backend/supabase/hosted/32_admin_login_hosted.sql`) | Built (5 Nov 2026) | [docs/planning/admin-dashboard.md](docs/planning/admin-dashboard.md) |

The client uses the assistant for:
- everyday banking questions, within guardrails;
- her compliance review: checklist, upload, a quick template check, then submit;
- **appropriate human handover**: relationship-specific enquiries go to the assigned RM; card, fraud and other operational issues follow the relevant specialist route, where supported. Trackable requests receive a backend ticket reference, which she follows in **My requests**;
- **meetings** with her RM (My requests → Meetings) and **monthly statements** she can print or save as PDF (Overview → Statements).

Stories and IDs (F1–F28 foundation, CR1–CR18 client, RM1–RM16 relationship manager) come from the product backlog spreadsheet (`docs/planning/product-backlog.xlsx`, deleted in commit `712eacb`; its last version is in git history). What's done and what's open is in [docs/planning/status.md](docs/planning/status.md).

## Folder map

```
backend/
  app/
    api/            HTTP routes (chat, documents, tickets, customer data, actions…)
    core/           config, auth/JWT gateway, Data API client, logging, LangSmith client
    services/       the logic: orchestrator, guardrail, validators, document checks, tickets…
      agents/       the v5 agent specs (registry.py: guide, accounts, compliance, relationship and the three RM agents),
                    the runner, rules and the v4 responders they call (retail, compliance, document, relationship)
      rm/           the RM assistant graph, handlers and priority score
      meetings/     the meeting lifecycle (lifecycle graph, brief/pack/minutes agents, worker, Zoom; meeting team)
      actions/      every change goes through here (handoff, ticket, meeting, upload, answer…)
    prompts/        prompts by agent and version (never edit a released version)
    schemas/        Pydantic models
  supabase/
    migrations/     database schema and synthetic data (never edit an applied one)
    hosted/         SQL to paste into the hosted Supabase project, in order
  evaluation/       test cases and the LangSmith dataset sync
  scripts/          helpers: hosted SQL, audit walkthrough, staff CLI, MCP server, OCR
  tests/            backend tests
frontend/
  src/              App.jsx (picks Chat, RmWorkspace or AdminWorkspace by role), components/, lib/ (data and helpers), index.css
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
| [docs/evaluation/test-cases.md](docs/evaluation/test-cases.md) | The 49 QA1 cases (Michelle C01–C24 and C11b, Sophie R01–R20 and R24–R27) and the run record |
| [docs/planning/status.md](docs/planning/status.md) | What is done and what is open |

`AGENTS.md` is a code map for AI coding tools, and `docs/prompt-log/` holds the prompt-version change history; neither is required for a first look.

## Run it locally

You need Python 3.12 and Node.js 22. You don't need Docker.

**1. Settings.** Copy `.env.example` to `.env` in the repo root (the backend and the frontend both read it) and fill in:
- the Supabase URL and anon key;
- the model key;
- `CHECKPOINT_SIGNING_KEY` (any long random string);
- `DOCUMENT_PRECHECK_SIGNING_KEY`, from `select secret from compliance.precheck_keys where key_id='v1';` in the SQL editor.

Set `PIPELINE=v2` and `CONVERSATION_STORE=database` (the code defaults are `v1` and `off`); leave `CLIENT_INTENT_ROUTING=adaptive`. Knowledge is always read from the database, so apply the knowledge migrations (through batch 28) first. Never use a service-role key.

**2. Install (first time).**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
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
Then open http://127.0.0.1:5173 and sign in as Michelle, or as Sophie for the relationship manager workspace (hosted set-up: apply `backend/supabase/hosted/16_apply_0089_0090_hosted.sql`, then follow *Set up on the hosted project* in the Workflow 2 doc).

**4. Hosted database.** Apply any new SQL from `backend/supabase/hosted/` in order. Its README lists what each file does.

**5. Reminders (optional).** Deadline reminders reach the Inbox only while the notification worker runs. It needs its own restricted database login, never the `postgres` owner:
1. In the Supabase SQL editor, create the login (choose your own password and keep it out of Git):
   ```sql
   create role demobank_reminders login password '<your password>';
   grant demobank_worker to demobank_reminders;
   ```
2. In Supabase, open **Connect** and copy the **Session pooler** connection string. Replace the user `postgres` with `demobank_reminders` (keep the `.<project-ref>` after it) and put in the password. Save it in `.env` as `WORKER_DATABASE_URL=…`.
3. Set `RUN_WORKER_IN_API=true` in `.env` and restart the backend. (Or leave it `false` and run `../.venv/bin/python -m app.worker` from `backend/` in a third terminal.)

**6. Hybrid knowledge search.** Knowledge search ranks sections by keywords *and* meaning (migration 0099) whenever an embedding key is set; with no key, or if the embedding provider fails, it falls back to keyword search and records `mode: keyword_only` in the audit event. There is no switch. It needs its own embedding key, never the model key: `EMBEDDING_API_KEY`, plus `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` and `EMBEDDING_MIN_SIMILARITY=0.4` (see `.env.example`). Store the section vectors once, and again after every knowledge migration, using the owner connection string:
```bash
cd backend
../.venv/bin/python -m scripts.embed_knowledge --database-url "postgresql://..."           # add --check to only report
```
Without the owner password, paste `backend/supabase/hosted/embed_knowledge.sql` into the Supabase SQL editor instead (put the embedding key in it; it stores the same vectors). Leave `EMBEDDING_API_KEY` empty and restart for keyword-only search. Each search logs a `knowledge_search` line (and a LangSmith `kb.search` run) with the mode and how long the embedding and database steps took, so the two can be compared. `KNOWLEDGE_RESPELL=false` turns off the chat-spelling table for search only.

Reminders are sent from 7 days before a due date, only for documents she still has to send, and disappear from her Inbox once she has sent everything (or the file the bank asked for). Michelle's review is due 30 Nov, so to see one today, ask for a correction due in 3 days (see *Demo data*); it reaches her Inbox within a minute.

## If something doesn't work

| What happens | What to check |
|---|---|
| `vite: command not found` | Run `npm ci` in `frontend/` (the zip has no `node_modules`). |
| `No module named …` | Recreate `.venv` and run `pip install -e './backend[dev]'`. |
| "Document checks are not switched on yet" | `DOCUMENT_PRECHECK_SIGNING_KEY` missing or wrong in `.env`; restart the backend. |
| The assistant doesn't use Michelle's records | `PIPELINE=v2` and `CONVERSATION_STORE=database` in `.env`; restart the backend. |
| Browser can't reach the backend | Both terminals running; frontend started with `VITE_API_URL=/api BACKEND_PORT=5001`. |
| Nothing appears in LangSmith | `LANGSMITH_TRACING=true` and `LANGSMITH_ENDPOINT=https://apac.api.smith.langchain.com` (the API host, not the website); restart. |
| "Could not verify" or "Answer withheld" | Look for the `agent_validation` and `agent_revision` events in the backend log and the `validation` step in the LangSmith trace. The system will not bypass source checks. Check approved KB matches and model configuration; an RM handover may require a signed-in saved conversation. |

## Demo data

Michelle is customer 1002 (`demo.michelle@example.com`). It is all seeded by the migrations:
- account ****7734 and Signature Card 7310, with July–October activity for her statements (`0004`, `0063`, `0077`);
- on 27 Oct, card 7310 used in Singapore and then in London within 2 hours (the unrecognised-charge demo), and a pending SGD 12,000 transfer to Lim Interiors (`0075`);
- a periodic review due 30 Nov 2026 with four requested documents (`0004`, `0056`), and demo tickets (`0071`).

Other test customers exist only in the local test database; `backend/supabase/hosted/06_keep_only_michelle.sql` removes them from the hosted project.

**The bank's decision on a document.** The RM workspace has no decision screen, so record it in the SQL editor after Michelle submits a file. Find the item id with `select item_id,label,submission_status from compliance.requested_items where customer_id=1002;`, then:
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
- Browser tests are opt-in: `npm run test:e2e` runs `chat.spec.js` and `live.spec.js` against the real AI provider; the native suites in `playwright.native.config.js` run from the backend pytest browser tests with `TEST_BROWSER_LOCAL=1`.
- Offline answer evaluation: `python -m evaluation.run --output evaluation-results.json`. See [docs/evaluation/README.md](docs/evaluation/README.md).
- Knowledge search comparison (keyword vs hybrid, spelling table on/off, quality and timing): `python -m evaluation.retrieval --search keyword,hybrid --respell on,off` (a temporary local database unless `--database-url` is given). Hybrid runs call the real embedding provider; the opt-in check is `TEST_DATABASE_LOCAL=1 TEST_EMBEDDINGS_LIVE=1 pytest tests/test_retrieval_eval.py`.

## Tracing and audit

- **Tracing.** Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to send each chat request to LangSmith as one run, with the graph nodes, agents and model calls nested inside. Data reads are traced only as steps of a chat, RM assistant or evaluation run; the reads a page makes when it loads are in the audit log only. Text is redacted before upload (`core/langsmith_client.py`).
- **Admin dashboard.** Sign in as `demo.admin@example.com` to see what LangSmith holds: failures with a debug card each, evaluation accuracy over time, agent scorecards, prompt versions, and latency and tokens. The backend reads LangSmith with its own key; nothing LangSmith-related reaches the browser.
- **Audit log.** Every request writes an append-only, hash-chained audit trail in Supabase (`audit.events`). It holds ids and counts only, never chat text. Run `python -m scripts.audit_walkthrough --case-id … --database-url … --markdown` to print one case end to end.

## Rules for contributors

- RLS stays on, and every customer query filters by `customer_id`. Only `VITE_` variables reach the browser.
- Lost card, shared secrets and "talk to a person" are fixed responses that never call the model.
- Don't edit a released migration or prompt version. Add a new one, with a prompt-log note for prompts (`docs/prompt-log/`).
- Never commit `.env` files or keys.

## Sharing a clean source ZIP

From the project root, run:

```bash
python3 scripts/package_source.py
```

This creates `IS4000_Group7-Source.zip` beside the project folder. Share that
ZIP instead of using Finder's Compress option, which includes ignored files.
The script excludes installed dependencies, virtual environments, Git history,
builds, caches, test reports, macOS metadata and local `.env` files. It keeps
`.env.example`, source code, migrations, documentation, package lockfiles and
Vite configuration. Recipients follow **Run it locally** to recreate `.venv`
and run `npm ci` in `frontend/` to install Vite and other dependencies.

`.gitignore` prevents new local/generated files being added to Git; it does not
remove files already tracked. In an existing clone, refresh the index once
from the project root (local files stay on disk):

```bash
git rm -r --cached .
git add .
git status
```

Review the staged changes before committing. No Git commits are made by the
packaging script. Keep local settings privately; they are absent from sharing ZIPs.
