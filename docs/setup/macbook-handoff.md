# MacBook handoff: DemoBank and Codex Luna

Updated 14 October 2026. This guide sets up the working chatbot on another Mac.
It uses real GPT-5.6 Luna through your signed-in Codex CLI. The OpenRouter route
stays available for the presentation. No fake model or automatic fallback is used.

## 1. Get the project and tools

Use Terminal on the MacBook. You need Git, Python 3.12, Node.js 22 and a Codex CLI
that supports the options used by this project. Check Python and Node first:

```bash
python3.12 --version
node --version
```

If you already have the project, open its folder and run:

```bash
git status
git switch main
git pull --ff-only origin main
```

Keep any MacBook changes safe before switching or pulling. Do not reset them.
For a new checkout, run:

```bash
git clone https://github.com/deniseLie/IS4000_Group7.git
cd IS4000_Group7
```

Use GitHub Desktop to clone or pull if Terminal is not signed into the private
repository. The remaining commands assume your current directory is the project
root. Create dependencies locally; do not copy `.venv` or `node_modules` from the
other Mac.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
cd frontend
npm ci
cd ..
```

## 2. Find the Codex executable and sign in

The CLI tested on the original Mac was **0.162.0-alpha.17.2**, bundled at:

```text
/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
```

This is an observed local path, not a promise about every app installation. If the
same path exists on the MacBook, select it in Terminal:

```bash
DEMO_CODEX_BIN='/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
test -x "$DEMO_CODEX_BIN"
"$DEMO_CODEX_BIN" --version
"$DEMO_CODEX_BIN" exec --help
"$DEMO_CODEX_BIN" login status
```

If the executable is elsewhere, use `command -v codex` to find it, then set
`DEMO_CODEX_BIN` to that absolute path. If no CLI is installed, follow the official
[Codex installation guide](https://learn.chatgpt.com/docs/cli), then check its
version and options. Do not assume a different build supports this adapter.

If it is not signed in with ChatGPT, run:

```bash
"$DEMO_CODEX_BIN" login
"$DEMO_CODEX_BIN" login status
```

Complete the ChatGPT sign-in in the browser using your own subscription account.
Do not copy authentication files or paste login tokens into this repository.
Use the same macOS user for CLI sign-in and the backend. If you intentionally use
a custom `CODEX_HOME`, it must be the same for both processes.

The adapter uses `--no-daemon`, `--ignore-user-config`, `--strict-config`,
`--ephemeral`, `--output-schema` and request-local feature overrides. The tested
build supports them. If the MacBook build rejects flags/configuration, use a
compatible build or the existing API route; do not remove the tool restrictions.
Model access must also be verified by a real request. Availability on one Mac or
account does not prove access on another.

## 3. Configure the private root .env

Create it only if it does not already exist:

```bash
if [ ! -f .env ]; then cp .env.example .env; fi
```

Edit `.env` locally. Use these settings, replacing the CLI path if needed:

```dotenv
CHAT_TRANSPORT=codex
CODEX_MODEL=gpt-5.6-luna
CODEX_CLI_PATH=/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
PROMPT_VERSION=v2
PORT=8000
VITE_API_URL=/api
TRACE_STORE=0
```

`CODEX_CLI_PATH` must be the executable itself, not a shell alias or a command with
arguments. It is read by the backend, so an absolute path avoids PATH differences.
Selecting Luna in a Codex chat does not configure the chatbot; these variables do.
No separate OpenAI API key is needed for this route. It uses your Codex allowance
and still requires internet access.

Keep the existing `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` and optional
`LLM_PROVIDER` settings for OpenRouter. They are unused while `CHAT_TRANSPORT=codex`.
Fill the Supabase URL and public anon key in both the backend `SUPABASE_*` and
frontend `VITE_SUPABASE_*` entries if you want sign-in. Obtain project configuration
through your team's private channel. Never use a service-role key in the app.
Only public browser configuration belongs in `VITE_` variables.

Use one root `.env`. Shell environment values override file values, and an old
`backend/.env` takes priority over the root file. Check for stale overrides rather
than copying conflicting settings into several files. Do not run `source .env`;
the application reads it itself. Restart the backend after provider changes, and
restart Vite after browser/proxy settings change. `.env` and login files stay out
of Git; `.env.example` contains placeholders only.

## 4. Start the app in two Terminal tabs

Backend tab, from the project root:

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend tab, from the project root:

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open **http://127.0.0.1:5173/**. Keep both tabs running. The backend starts the CLI
for each AI request; you do not need to leave an interactive Codex chat open.
The path is: browser → Vite `/api` proxy → FastAPI → Codex CLI → checked answer.
Keep the servers bound to `127.0.0.1`; this is a trusted local demo, not a public
subscription gateway.

## 5. Confirm the MacBook routing works

In another Terminal tab:

```bash
curl --fail http://127.0.0.1:8000/health
```

Health only proves that the backend is running. To verify the actual model, ask
in the browser: **Does paying only the minimum on my credit card avoid interest?**
Expect an **AI-generated** label, an explanation that the unpaid balance incurs
interest, and the **Singapore credit card payments** source. Open its source
panel. A lost-card question follows the fixed safety path and does not prove the
CLI works.

For a recorded real-model check, run from `backend/` while no other chat request
is active:

```bash
TRACE_STORE=0 ../.venv/bin/python -m evaluation.run --live --suite focused --output evaluation-results.json
```

Check that `model_config` identifies `codex`, `codex-cli` and `gpt-5.6-luna`.
Review the returned answers; a completed request is not proof of answer quality.
The focused suite contains three source questions and excludes the deferred
broad complex-question checks. It consumes subscription allowance.

Local checks, without model calls:

```bash
# From backend/
../.venv/bin/python -m pytest -q
../.venv/bin/python -m evaluation.run --output evaluation-results.json

# Then from frontend/
npm test
npm run build
```

Optional browser verification from `frontend/` (uses the real backend; the second
command also calls the real model):

```bash
npx playwright install chromium
TEST_ENV_FILE=../.env npx playwright test tests/chat.spec.js
TEST_ENV_FILE=../.env npx playwright test tests/live.spec.js --grep 'real AI gives sourced'
```

Browser tests start their own servers on **8038/5178**. They do not need the normal
8000/5173 servers stopped, but avoid simultaneous model requests during testing.

## 6. Switch to OpenRouter for the presentation

In the same private `.env`:

```dotenv
CHAT_TRANSPORT=api
LLM_PROVIDER=openai
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b:free
LLM_API_KEY=YOUR_PRIVATE_OPENROUTER_KEY
```

The model above is the project's previous configured model, not a guarantee of
current free availability. Use the intended available model and test it before
presenting. Stop and restart the backend, then repeat the minimum-payment question
and focused evaluation. The browser URL stays the same. Set `CHAT_TRANSPORT=codex`
and restart again to return to Luna. Failures are reported; there is no silent
switch to another provider or stored answer.

## Troubleshooting

| What happens | What to check |
| --- | --- |
| CLI not found | Verify `CODEX_CLI_PATH` exists and is executable. A shell alias is insufficient. |
| CLI fails or model is unavailable | Run the selected executable's `login status`; check its version, supported options, model access and subscription allowance. |
| Requests still use the old provider | Restart FastAPI. Check exported variables and any `backend/.env` override. |
| Browser cannot reach the backend | Check both Terminal tabs, `VITE_API_URL=/api`, and backend port 8000. Remove an unintended `BACKEND_PORT` override before starting Vite. |
| Port already in use | Stop your earlier server in its Terminal tab before starting another. |
| Codex is busy | Wait for the current request. The adapter permits one active CLI call per backend process. |
| Timeout or rate limit | Retry later after checking account limits. Each CLI attempt has a 75-second timeout; validation can request one revision. |
| Answer withheld | The real reply failed validation. Review the evidence; do not bypass checks or replace it with a sample answer. |
| No linked customer profile | Sign out and chat as a guest. Do not create banking rows or change RLS as part of this handoff. |

## What this update includes

- Real Luna CLI routing, with the existing OpenRouter route retained.
- Source/unit checks, shared privacy cases, lost-card wording and chat-reset fixes.
- Evaluation metadata, focused real-model results and updated `plan.docx`.
- UBS-inspired responsive UI using DemoBank branding and no UBS logo.

Latest local evidence: **151 backend tests, 21 frontend utility tests and 19 offline
validator cases passed**. UI refresh evidence covers **14 distinct browser
workflows across runs**, including two real Luna scenarios. The initial dialog
focus failure was fixed and re-tested. See [core results](../evaluation/core-results-review.md),
[UI results](../planning/ui-refresh.md) and [transport details](codex-local-provider.md).
These results were obtained on the original Mac; run the checks on the MacBook too.

Keep these two checks for later: **two linked Supabase customers / live isolation**
and **broad complex-question reliability**. Saved conversations, broader language
coverage and approved bank-specific product content are not completed by this setup.

Official reference for the CLI's non-interactive JSON/schema workflow:
[Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
