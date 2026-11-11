# DemoBank (UBS Connect) – AI banking assistant (IS4000 Group 7)

A coursework demo of an AI assistant for a private bank, shown in the app as **UBS Connect**. It has two sides: the client assistant (Michelle) and the relationship manager's workspace and assistant (Sophie). FastAPI backend with a LangGraph multi-agent pipeline, a React (Vite) frontend, Supabase for login and data, an OpenRouter model through LangChain, and LangSmith for tracing. All people and data are fictional. It is not a real bank product.

| The package must include | Where |
|---|---|
| A README file | This file |
| Environment setup instructions | [Environment setup](#2-environment-setup); Zoom on Windows: [docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md) |
| Dependency list | [Dependencies](#1-dependencies) (`backend/pyproject.toml`, `frontend/package.json`) |
| API key setup instructions | [API keys and secrets](#3-api-keys-and-secrets); every setting with its default in [`.env.example`](.env.example) |
| Dataset inclusion rules | [docs/datasets.md](docs/datasets.md) |

## Quick start

With Python 3.12, Node.js 22 and a filled-in `.env` (section 3), from the repository root:

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e './backend[dev]'
(cd frontend && npm ci)
cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # terminal 1
cd frontend && npm run dev                                                                # terminal 2
```

Open http://localhost:5173. Windows commands are in section 2.

## The two workflows

| Workflow | Who | Doc |
|---|---|---|
| 1 – Client | Michelle (`demo.michelle@example.com`) | [docs/workflows/workflow-1-client.md](docs/workflows/workflow-1-client.md) |
| 2 – Relationship manager | Sophie (`demo.sophie@example.com`, app_metadata `demobank_role=rm`) | [docs/workflows/workflow-2-relationship-manager.md](docs/workflows/workflow-2-relationship-manager.md) |
| AI engineering (admin dashboard) | `demo.admin@example.com` (app_metadata `demobank_role=admin`) | [docs/planning/admin-dashboard.md](docs/planning/admin-dashboard.md) |

The client uses the assistant for everyday banking questions within guardrails; her compliance review (checklist, upload, a quick template check, then submit); handover to her RM or the right specialist team, with a ticket she follows in **My requests**; meetings with her RM; and monthly statements she can print. The RM gets a ranked day, client briefs and overviews, meeting requests, minutes from a call transcript, and drafted follow-ups that she approves before anything is sent.

What is done and what is open: [docs/planning/status.md](docs/planning/status.md).

## 1. Dependencies

**Software**

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12 (3.11 is the minimum in `pyproject.toml`; 3.12 is what we test) | Backend, tests, evaluation |
| Node.js and npm | Node 22 | Frontend, frontend tests |
| Git | any | Getting the code; `scripts/package_source.py` |
| WSL 2 with Ubuntu 24.04 | Windows only, optional | Zoom links ([docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md)) |
| PostgreSQL client libraries | optional | Local database tests on Windows (PostgREST needs `libpq.dll` on `PATH`) |
| Tesseract and `pdftoppm` | optional | Reading scanned documents (`DOCUMENT_OCR_COMMAND`); on macOS `python -m scripts.install_document_ocr` builds a Vision adapter instead |

Docker is not needed.

**Python packages** (`backend/pyproject.toml`, installed with `pip install -e './backend[dev]'`): `fastapi` 0.128.8, `uvicorn` 0.39.0, `supabase` 2.32.0, `langchain` ≥1.0, `langchain-openai` ≥1.0, `openai` ≥1.0, `langgraph` 1.2.x, `langsmith` 0.14.x, `python-dotenv` 1.2.1, `pydantic` 2.13.5, `pydantic-settings` 2.11.0, `sse-starlette` 3.3.0, `httpx` 0.28.1, `PyJWT[crypto]` 2.15.1, `psycopg[binary]` 3.3.6, `pypdf` 6.21.1, `reportlab` 5.1.0, `filelock` 4.x, `defusedxml` 0.7.1, `psutil` 7.2.2, `Pillow` 13.0.0, `mcp` 2.x. Extras: `dev` (`pytest`), `dbtest` (`pgserver`, for the local database tests), `fontbuild` (`fonttools`).

**JavaScript packages** (`frontend/package.json`, exact versions in `package-lock.json`, installed with `npm ci`): `react` and `react-dom` 18, `@supabase/supabase-js` 2; dev: `vite` 5, `@vitejs/plugin-react` 4, `@playwright/test` 1.64 (browser tests).

**Hosted services**

| Service | Needed for | Required |
|---|---|---|
| [Supabase](https://supabase.com) (Auth, Postgres, Data API, Storage) | Logins and all data, including the knowledge base | Yes |
| [OpenRouter](https://openrouter.ai) (or another OpenAI-compatible API) | Every AI answer; embeddings for hybrid search | Yes for chat |
| [LangSmith](https://smith.langchain.com) | Tracing, the admin dashboard, scored evaluations | No |
| [Zoom](https://marketplace.zoom.us) | Zoom links and My Notes for meetings | No |

## 2. Environment setup

**Install (first time).** From the repository root.

macOS or Linux:
```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
(cd frontend && npm ci)
cp .env.example .env
```

Windows (PowerShell):
```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".\backend[dev]"
cd frontend; npm ci; cd ..
Copy-Item .env.example .env
```

**Settings.** There is one settings file, `.env` in the repository root, read by both the backend (`backend/app/core/config.py`) and Vite (`envDir: '..'`). Fill it in as section 3 explains. `.env.example` lists every setting with a comment and its default, already set for the full app (`PIPELINE=v2`, `CONVERSATION_STORE=database`). Only `VITE_` variables reach the browser. Restart the backend after any change.

**Database.** The team's hosted Supabase project already has every migration, the knowledge base and the demo logins, so with its values in `.env` there is nothing to set up. The values are shared privately, never in the ZIP. On that project:
- new SQL from `backend/supabase/hosted/` is pasted into the SQL editor in order; [its README](backend/supabase/hosted/README.md) says what each file does;
- `58_reset_storyline_demo.sql` puts Michelle and Sophie back to the demo start.

`backend/supabase/migrations/` is the full schema with its synthetic data (numbered 0001–0150; never edit an applied one). The local tests apply them to a temporary PostgreSQL. A brand-new Supabase project is not a tested path: the hosted batches assume the team project's history (`backend/scripts/prepare_hosted_migrations.py`), and the demo logins have to be created in Authentication → Users (Sophie's steps: *Set up on the hosted project* in the Workflow 2 doc; admin: `32_admin_login_hosted.sql`).

**Run.** Two terminals.

macOS or Linux:
```bash
cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

Windows (PowerShell):
```powershell
cd backend; $env:PYTHONUTF8=1; ..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend; npm run dev
```

Open http://localhost:5173 and sign in as Michelle, Sophie or the admin (passwords are shared privately), or chat as a guest. Vite sends `/api` to the backend on `PORT` from `.env` (8000). `PYTHONUTF8=1` makes Python read the prompt and data files as UTF-8 on Windows.

To use Zoom links on Windows, run the backend in WSL instead: [docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md).

**Optional background workers.**
- *Inbox reminders and notices* need the notification worker: set `WORKER_DATABASE_URL` (a restricted login, section 3) and `RUN_WORKER_IN_API=true`, or run `python -m app.worker` from `backend/`. Reminders start 7 days before a due date, only for documents she still has to send.
- *Meeting minutes* need `MEETINGS_ENABLED=true` and the meeting worker (`RUN_MEETING_WORKER_IN_API=true`, or `python -m app.services.meetings.worker`) with `MEETINGS_DATABASE_URL` and `MEETINGS_CHECKPOINT_SIGNING_KEY` ([docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md), step 4). Zoom itself is not needed: the RM can upload a `.vtt` transcript.

## 3. API keys and secrets

Put every value in the root `.env`. Never commit it, never put a key in a `VITE_` variable unless the table says so, and never use the Supabase **service_role** key anywhere. To make a random signing key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Setting | What it is for | Where to get it | Required |
|---|---|---|---|
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Logins and every data read (the backend acts with the signed-in user's token, under row-level security) | Supabase → Project Settings → API: *Project URL* and the *anon public* key | Yes |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | The same two values for the browser (the anon key is public by design) | Copy of the two above | Yes |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Every AI answer: planner, agents, RM assistant, meeting agents | [openrouter.ai/keys](https://openrouter.ai/keys) → *Create key*, and add credit. `LLM_BASE_URL=https://openrouter.ai/api/v1`; `LLM_MODEL` is an OpenRouter model id (we use `anthropic/claude-haiku-5.5`) | Yes for chat |
| `EMBEDDING_API_KEY` (+ `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`) | Hybrid knowledge search (keywords plus meaning) | A second OpenRouter key, kept apart from `LLM_API_KEY` so it can be capped or revoked alone. Then store the section vectors once (below) | No: empty means keyword search only |
| `CHECKPOINT_SIGNING_KEY` | Saved personal chats on the agent pipeline | Generate (command above). Keep it stable: a new value invalidates saved snapshots | Yes with `CONVERSATION_STORE=database` |
| `DOCUMENT_PRECHECK_SIGNING_KEY` | Michelle's quick document check after an upload | Supabase SQL editor: `select secret from compliance.precheck_keys where key_id='v1';` | No: without it, uploads work and the check says *not switched on yet* |
| `LANGSMITH_API_KEY`, `LANGSMITH_ENDPOINT`, `LANGSMITH_PROJECT`, `LANGSMITH_TRACING=true` | Tracing, the admin dashboard and scored evaluations | [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys. The endpoint is the **API** host of your workspace's region (ours: `https://apac.api.smith.langchain.com`), not the website | No: the admin dashboard needs it |
| `JUDGE_MODEL` | The optional grading model in `evaluation.langsmith_run --judge` | A model id different from `LLM_MODEL` | No |
| `WORKER_DATABASE_URL` | The notification worker (Inbox reminders) | A restricted login, see below | No |
| `MEETINGS_DATABASE_URL`, `MEETINGS_CHECKPOINT_SIGNING_KEY` | The meeting worker (minutes, follow-ups) | A restricted login (below, with `demobank_meetings`); generate the key | Only with `MEETINGS_ENABLED=true` |
| `ZOOM_OAUTH_DIRECTORY` | Zoom links | A folder made by `python -m scripts.zoom_authorize` from a Zoom app's Client ID (no client secret): [docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md) | No |

**Restricted database logins.** The two workers connect to Postgres directly, so each gets its own login with only the rights it needs, never the `postgres` owner. In the Supabase SQL editor (choose your own passwords and keep them out of Git):

```sql
create role demobank_reminders login password '<password>';
grant demobank_worker to demobank_reminders;              -- WORKER_DATABASE_URL
create role demobank_meeting_worker_runtime login password '<password>';
grant demobank_meetings to demobank_meeting_worker_runtime;  -- MEETINGS_DATABASE_URL
```

In Supabase, open **Connect**, copy the **Session pooler** connection string, replace the user `postgres` with the new login (keep the `.<project-ref>` after it) and put in its password. Each worker checks at start that its login has no more rights than it needs.

**Knowledge vectors.** With `EMBEDDING_API_KEY` set, store the section vectors once, and again after every knowledge migration, with the owner connection string:
```bash
cd backend && ../.venv/bin/python -m scripts.embed_knowledge --database-url "postgresql://..."   # add --check to only report
```
Without the owner password, paste `backend/supabase/hosted/embed_knowledge.sql` into the SQL editor instead (put the embedding key in it, and don't save the tab).

**Local subscription instead of an API key (optional).** `CHAT_TRANSPORT=claude` or `codex` sends model calls to a signed-in Claude Code or Codex CLI on this machine instead of OpenRouter (`CLAUDE_MODEL`, `CODEX_MODEL`). No app key is passed to the CLI.

## 4. Datasets

All data is synthetic and seeded by the migrations. What the package includes, what it never includes (keys, logins, recordings, real documents, run outputs) and the rules for adding data: [docs/datasets.md](docs/datasets.md).

**Demo data.** Michelle is customer 1002:
- account ****7734 and Signature Card 7310, with July–October activity for her statements;
- on 27 Oct, card 7310 used in Singapore and then in London within 2 hours (the unrecognised-charge demo), and a pending SGD 12,000 transfer;
- an advised portfolio and a time deposit maturing soon (the Lombard loan storyline, [docs/workflows/storyline-michelle.md](docs/workflows/storyline-michelle.md));
- a periodic review due 30 Nov 2026 with two requested documents, and demo tickets.

Sophie Lim is the RM for Michelle and Tan Mei Ling.

**The bank's decisions** (there is no bank-staff screen; record them in the SQL editor after Michelle submits):
```sql
select item_id,label,submission_status from compliance.requested_items where customer_id=1002;
select compliance.record_bank_decision('<item_id>','accepted');
select compliance.record_bank_decision('<item_id>','rejected','Please send a clearer scan.',current_date+3);  -- reminder due in 3 days
select request_id,question,status,response from compliance.info_requests where customer_id=1002;
select compliance.record_request_decision('<request_id>','more_info','Please also send the sale agreement.');
```
A correction shows on her Compliance review as *The bank asked you* and closes when she uploads a new file for that item.

## 5. Tests

macOS or Linux, from `backend/`:
```bash
../.venv/bin/python -m pytest -q                                            # unit tests, about 2 min, no .env or keys needed
../.venv/bin/python -m pip install -e '.[dbtest]' && ../.venv/bin/python -m scripts.install_postgrest   # once
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 ../.venv/bin/python -m pytest -q   # plus a real local database
cd ../frontend && npm test                                                  # frontend unit tests
```

- Windows: use `..\.venv\Scripts\python.exe`, set `PYTHONUTF8=1`, and keep a PostgreSQL `bin` folder on `PATH` for the local Data API tests (PostgREST needs `libpq.dll`). The local Data API run starts PostgREST for each of about 400 tests and takes over 2 hours on Windows. The registered meeting worker refuses its runtime file on Windows, so its restart test is skipped there.
- The local database tests start a temporary PostgreSQL and PostgREST and never touch the hosted project.
- Browser tests are opt-in and use the real AI provider: `npm run test:e2e`; the native suites run from the backend's browser tests with `TEST_BROWSER_LOCAL=1`.
- Evaluation: `python -m evaluation.run --output evaluation-results.json` (offline, no model calls) and the suites in [docs/evaluation/README.md](docs/evaluation/README.md).

## If something doesn't work

| What happens | What to check |
|---|---|
| `vite: command not found` | Run `npm ci` in `frontend/` (the ZIP has no `node_modules`). |
| `No module named …` | Recreate `.venv` and run `pip install -e './backend[dev]'`. |
| `UnicodeDecodeError` on Windows | Set `PYTHONUTF8=1` before starting Python. |
| The browser can't reach the backend | Both terminals running; the backend on the `PORT` in `.env` (8000). |
| The assistant doesn't use Michelle's records | `PIPELINE=v2` and `CONVERSATION_STORE=database` in `.env`; restart the backend. |
| "Document checks are not switched on yet" | `DOCUMENT_PRECHECK_SIGNING_KEY` missing or wrong; restart the backend. |
| Meetings say they are unavailable (503 `meetings_unavailable`) | `MEETINGS_ENABLED=true` and the meeting worker running. |
| `zoom_not_configured`, or "owner-only checks need macOS or Linux" | [docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md), *If something doesn't work*. |
| Nothing appears in LangSmith | `LANGSMITH_TRACING=true` and the API host of your region in `LANGSMITH_ENDPOINT`; restart. |
| "Could not verify" or "Answer withheld" | The answer failed a source check, by design. Look for `agent_validation` in the backend log and the `validation` step in the LangSmith trace. |

## Tracing and audit

- **Tracing.** With `LANGSMITH_TRACING=true`, each chat request is one LangSmith run with the graph nodes, agents and model calls nested inside. Text is redacted before upload (`backend/app/core/langsmith_client.py`).
- **Admin dashboard.** Sign in as `demo.admin@example.com` to see failures, evaluation results over time, agent scorecards, prompt versions, latency and tokens. The backend reads LangSmith with its own key; nothing LangSmith-related reaches the browser.
- **Audit log.** Every request writes an append-only, hash-chained audit trail in Supabase (`audit.events`) with ids and counts only, never chat text. `python -m scripts.audit_walkthrough --case-id … --database-url … --markdown` prints one case end to end.

## Folder map

```
backend/
  app/
    api/            HTTP routes (chat, RM, meetings, documents, tickets, customer data, actions, admin)
    core/           settings, auth/JWT gateway, Data API client, logging, LangSmith client
    services/       orchestrator, guardrail, validators, document checks, tickets
      agents/       agent specs (registry.py), the runner and the rules
      rm/           the RM assistant graph, handlers and priority score
      meetings/     meeting lifecycle: lifecycle graph, brief/pack/minutes agents, worker, transcripts, Zoom
      actions/      every change goes through here (handoff, ticket, meeting, upload, answer)
    prompts/        prompts by agent and version (never edit a released version)
    schemas/        Pydantic models
  supabase/
    migrations/     database schema and synthetic data (never edit an applied one)
    hosted/         SQL to paste into the hosted Supabase project, in order
  evaluation/       evaluation case files and runners (LangSmith)
  scripts/          helpers: Zoom sign-in, knowledge vectors, hosted SQL, audit walkthrough, staff CLI, MCP server
  tests/            backend tests
frontend/
  src/              App.jsx (picks the client, RM or admin workspace by role), components/, lib/
  tests/            Playwright browser tests
docs/               how the agents run, architecture, setup, datasets, workflows, evaluation, planning
scripts/            package_source.py (the clean source ZIP)
tests/fixtures/     privacy cases shared by the backend and frontend tests
```

## Docs (read in this order)

| Doc | What it covers |
|---|---|
| [docs/how-agents-run.md](docs/how-agents-run.md) | One message end to end: the orchestrator, which agent runs, tools, actions, tickets, the RM handover |
| [docs/workflows/workflow-1-client.md](docs/workflows/workflow-1-client.md) | The client persona and her journeys |
| [docs/workflows/workflow-2-relationship-manager.md](docs/workflows/workflow-2-relationship-manager.md) | The relationship manager workspace |
| [docs/architecture.md](docs/architecture.md) | Full design: components, data contracts, security, audit, knowledge base (appendix) |
| [docs/pdpa-baseline.md](docs/pdpa-baseline.md) | Each PDPA obligation and how the app meets it |
| [docs/evaluation/README.md](docs/evaluation/README.md) | How answers are tested and the results |
| [docs/setup/zoom-wsl.md](docs/setup/zoom-wsl.md) | Zoom meetings with the backend in WSL |
| [docs/datasets.md](docs/datasets.md) | The datasets and the inclusion rules |
| [docs/planning/status.md](docs/planning/status.md) | What is done and what is open |

`AGENTS.md` is a code map for AI coding tools, and `docs/prompt-log/` holds the prompt-version history.

## Rules for contributors

- RLS stays on, and every customer query filters by `customer_id`. Only `VITE_` variables reach the browser.
- Lost card, shared secrets and "talk to a person" are fixed responses that never call the model.
- Don't edit a released migration or prompt version: add a new one, with a prompt-log note for prompts.
- Never commit `.env` files, keys, passwords or real data ([docs/datasets.md](docs/datasets.md)).

## Sharing a clean source ZIP

From the repository root:

```bash
python scripts/package_source.py
```

This writes `IS4000_Group7-Source.zip` beside the project folder (with one top folder, `IS4000_Group7/`); `--output <path>` writes it elsewhere, outside the project. Run from a Claude worktree, it still writes beside the main checkout and never packs `.claude/` or other worktrees. It packs the files Git tracks (uncommitted edits included; `git add` a new file first), so local settings, logins, installed dependencies, recordings, presentation files and caches stay out; [docs/datasets.md](docs/datasets.md) lists what is excluded and why. Without Git it walks the folder and skips the same things. Recipients follow [Environment setup](#2-environment-setup) to recreate `.venv` and run `npm ci`.
