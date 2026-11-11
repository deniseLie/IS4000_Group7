# Datasets and inclusion rules

Checked 11 Nov 2026. What data the code package contains, where each set came from, what is never included, and the rules for adding data.

**In one line:** every person, account, transaction, document and meeting in this project is invented. The package holds the code, the synthetic data that seeds the database, the knowledge articles and the evaluation cases. It never holds keys, passwords, sign-in grants, real documents, recordings or run outputs.

## 1. What the package includes

| Dataset | Where | What it holds | Where it came from | How it is used |
|---|---|---|---|---|
| Synthetic bank records | `backend/supabase/migrations/` (seed rows inside the schema migrations, e.g. `0004` personas, `0063` and `0075`–`0077` Michelle's accounts, cards, activity and statements, `0088` Sophie's book, `0115`–`0117` portfolio) | Customers, accounts, cards, transactions, compliance reviews, tickets, RM notes, meetings, consent grants | Written by the team. Customers 1001–1006 and advisers are fictional; emails use `example.com` | Loaded by applying the migrations (hosted project: `backend/supabase/hosted/`; tests: a temporary local PostgreSQL). Demo resets (`17`, `21`, `34`, `51`, `58`) only delete, re-seed or re-date these rows |
| Knowledge base | Migrations (`0009` first library, `0098` client guidance, `0115` financing, later versions); listed in [knowledge-articles.md](knowledge-articles.md) | The approved articles and sections the assistant cites | Team-written guidance for the fictional bank, plus short summaries of two allowed public sources ([MoneySense](https://www.moneysense.gov.sg), [ABS](https://www.abs.org.sg)) | Read from the database only (`kb.articles`, `kb.sections`). Section vectors are computed from it by `backend/scripts/embed_knowledge.py`, not shipped |
| Background research | `docs/research/` | Notes on how a real private bank explains the same topics | The team's own paraphrase of public web pages, with the date checked. Never copied into articles | Shaped the wording of the articles; not read by the app |
| Evaluation cases | `backend/evaluation/*.json` (table in section 2) | Questions, turns, expected routes, sources and checks | Written by the team against the synthetic personas | Run by `evaluation.run`, `evaluation.retrieval`, `evaluation.langsmith_run` and other suites. `evaluation.langsmith_sync` copies seven of them to LangSmith datasets named `demobank-*`; the JSON files are the source of truth |
| Judge prompts | `backend/evaluation/judge/` | Grading instructions for the optional model judge | Written by the team | `langsmith_run.py --judge` |
| Test fixtures | `tests/fixtures/privacy-cases.json` (21 cases: made-up PINs, OTPs and card numbers, and ordinary text that must stay unchanged), `backend/tests/fixtures/external/zoom_meeting_waiting.json` (a made-up Zoom meeting response), `frontend/tests/fixtures/documents/Synthetic DEMO.*` (a fake PDF, DOCX, JPG and PNG) | Inputs for the redaction, RM Zoom check and upload tests | Made up by the team (the Zoom response has synthetic ids, host and `example.com` email) | Unit and browser tests |
| Demo transcripts | `docs/workflows/demo-files/*.vtt` | Scripted calls between Sophie and Michelle | Written by the team and read aloud by team members | Uploaded on a meeting to make minutes without Zoom |
| Saved evaluation evidence | `docs/evaluation/evidence/`, `docs/evaluation/assistant-prompt-tests.xlsx` | Outputs of earlier runs and a manual test sheet | Produced from the synthetic data above | Supports the results in [evaluation/README.md](evaluation/README.md) |
| Font | `backend/app/assets/fonts/NotoSansSC-Regular.ttf` with `OFL.txt` | A font for PDF recaps | Google Noto, SIL Open Font Licence (included) | `reportlab` PDF output |

## 2. Evaluation case files

| File | Cases | What they test |
|---|---|---|
| `cases.json` | 12 | Public questions: languages, prompt injection, corrections, unsupported facts (`demobank-standard`) |
| `focused_cases.json` | 6 | Source-checked public questions and the three public Workflow 1 questions (`demobank-focused`) |
| `safety_cases.json` | 10 | Fixed safety routes that must answer without a model call (`demobank-safety`) |
| `offline_cases.json` | 19 | Answer validators on fixed sample answers; no model call, stays local |
| `qa1_client_cases.json` | 25 | Michelle C01–C24 and C11b (`demobank-qa1-client`); mirror [test-cases.md](evaluation/test-cases.md) |
| `qa1_rm_cases.json` | 24 | Sophie R01–R20, R24–R27 (`demobank-qa1-rm`) |
| `graph_cases.json` | 4 | Agent dependencies and fault cases (`demobank-graph`) |
| `planner_cases.json` | 15 | The planner alone: route, agents, links, priority (`demobank-planner`) |
| `retrieval_cases.json` | 156 | Knowledge search: 72 `tune`, 84 `holdout` (covered, informal, gap, non-English, off-topic) |
| `talking_points_cases.json`, `talking_points_conversations.json` | 55, 14 | Client chat insights classifier, single messages and multi-turn |
| `meeting_cases.json`, `meeting_boundary_cases.json` | 12, 22 | Meeting minutes and the meeting tools' boundaries |

Tune search settings on `split=tune` only; the `holdout` cases measure the result (floors in `backend/tests/test_retrieval_eval.py`).

## 3. What is never included

| Never in the package | Why | Where it lives instead |
|---|---|---|
| `.env` and every `.env.*` except `.env.example` | API keys, signing keys and database passwords | Each person's machine; handed over privately if needed |
| `personas.json` | Passwords of the demo logins used by live tests | Git-ignored, on the tester's machine |
| Zoom OAuth folders (`zoom-tokens.json`, `zoom-public-client.json`) | Sign-in tokens | The WSL or Linux home folder ([setup/zoom-wsl.md](setup/zoom-wsl.md)) |
| LangSmith traces and experiment results | Contain chat text and synthetic records (redacted before upload) | LangSmith; summaries go in [evaluation/README.md](evaluation/README.md) |
| Run outputs: `backend/evaluation-results.json`, `frontend/test-results/`, `frontend/playwright-report/`, `backend/.cache/` | Regenerated by every run | Re-run the command |
| Real customer data, real documents, screenshots of real accounts | Privacy (PDPA) and the coursework rules | Nowhere: only synthetic data is allowed |
| Call recordings and presentation files (`*.mp4`, `*.pptx`, `Video_DEMO/`) | Large, not code, may show team members | Shared separately |
| Installed dependencies and caches (`.venv/`, `node_modules/`, `__pycache__/`, `dist/`) | Rebuilt from `backend/pyproject.toml` and `frontend/package-lock.json` | `pip install` and `npm ci` |
| Local tool folders (`.claude/`, `.specify/`, `specs/`) | Personal coding-assistant and planning drafts | Each person's machine |

`.gitignore` keeps most of these out of Git, and `python scripts/package_source.py` builds the ZIP from the files Git tracks, minus the list above.

## 4. Rules for adding data

1. **Synthetic only.** Invent every name, address, number and document. Emails use `example.com`. Show cards and accounts by their last four digits; full-length numbers belong only in privacy test cases, made up to test redaction.
2. **Seed rows go in a new migration** with the next free number. Never edit an applied migration. A demo reset may delete, re-seed or re-date rows, never add new kinds of data.
3. **Knowledge articles** are team-written, or a short summary in the team's own words of an allowed public source (`www.moneysense.gov.sg`, `www.abs.org.sg`) with `reference_url`, `checked_on` and `review_due`. Never copy source text. A change is a new article version in a new migration; then run `python -m scripts.embed_knowledge` again.
4. **Evaluation cases** are added to the JSON file with a new stable id; never reuse or renumber an id. QA1 cases must match [evaluation/test-cases.md](evaluation/test-cases.md) (`backend/tests/test_qa1_cases.py` checks this). After changing a live case file, run `python -m evaluation.langsmith_sync`.
5. **External data** comes only from the allowed read-only host (`api.zoom.us`, the RM Zoom meeting check; `EXTERNAL_HOSTS`); a saved response goes in `backend/tests/fixtures/external/` with made-up ids and names.
6. **No personal data in logs, traces or screenshots.** Logs and the audit trail hold ids and counts only; LangSmith uploads go through the redacting client (`backend/app/core/langsmith_client.py`).
7. **Keep large binaries out of Git** unless a test needs them; then keep them small and synthetic, like the `Synthetic DEMO.*` files.
