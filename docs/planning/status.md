# Status (29 Oct 2026)

Story IDs follow [product-backlog.xlsx](product-backlog.xlsx). Focus: finish Persona 1 (client) before starting Persona 2 (RM).

## Foundation

| ID | Story | State | What is left |
|---|---|---|---|
| F1 | System architecture design | Done | – |
| F2 | Data contracts | Done | – |
| F3 | Operational database and synthetic seed | Done | – |
| F4 | RM, ticket and meeting-note seed | **Partly** | Tickets done. No Sophie login, RM book or meeting notes yet (needed for Persona 2). |
| F5 | Audit log | Done | – |
| F6 | API gateway | Done | – |
| F7 | Input guardrail | Done | – |
| F8 | LLM gateway | Done | – |
| F9 | Conversation store | Built, sheet says **No** | Saved chats work (migrations 0013–0014); tick it after a quick check. |
| F10 | Knowledge service | Done | – |
| F11 | Authorisation and consent | Done | – |
| F12 | RM role and book-based access | **Not started** | Persona 2 |
| F13 | Customer data service | Done | – |
| F14 | RM data functions | **Not started** | Persona 2 |
| F15–F17 | Action service, card/handoff, appointment executors | Done | – |
| F18 | Orchestrator | Done | Handover to RM added (0073). |
| F19 | Response validator | Done | Withheld answers go to the RM. |
| F20 | Stub agents | Built, sheet says **No** | All five agents exist; tick it. |
| F21 | Local deployment (Docker) | Dropped | Docker removed; run with uvicorn + npm. |
| F22 | Hardening and report evidence | **Partly** | Automated: RLS and owner-only checks (`test_rls_live.py`, `test_bank_decisions.py`), audit chain (`test_audit*.py`), 10 Michelle cases written. Still to do for the report: run C01–C10 against the hosted project with LangSmith on and fill the run record, one Playwright smoke run, latency per request type from the LangSmith runs, Sophie's RLS checks (Persona 2). |
| F23 | Ticket service | Done | – |
| F24 | Request categories | Done | – |
| F25 | MCP tool layer | Done | – |
| F26 | LangSmith tracing and evaluation | **Partly** | Tracing and dataset sync done; run and score the experiments. |
| F27 | Model comparison | **Not started** | Needs F26 experiments. |
| F28 | Audit trail walkthrough | Done | – |
| QA1 | Test case set (20 cases) | **Half** | Michelle C01–C10 in [test-cases.md](../evaluation/test-cases.md) with an owner column to assign and a run record. Sophie R01–R10 when Workflow 2 starts. |

## Persona 1 (client) – open items

- **Hosted project:** run `07`, `08`, `09` and `10` from `backend/supabase/hosted/` in order (01–06 are done).
- **Reminders:** off until the worker runs. README step 5: create the `demobank_reminders` login, set `WORKER_DATABASE_URL` and `RUN_WORKER_IN_API=true`.
- **Run the 10 test cases** and record the results (F22 evidence).
- **Assign owners** in the backlog and in `test-cases.md`.

## Done on 29 Oct

- **Michelle's own demo data (CR12, CR13, 0075):** card 7310 used in Singapore then London on 27 Oct; a pending SGD 12,000 transfer.
- **The bank's decision on a document (CR3, CR6, 0076):** `compliance.record_bank_decision` accepts a file or asks for a correction, which shows on her review and closes when she resubmits; a near due date also triggers a reminder. Reminders she has already acted on disappear from her Inbox (0078).
- **Meetings with her RM (CR18):** My requests → Meetings (free times, hold, confirm, cancel); meeting questions are no longer turned into RM tickets; prompt `relationship/v5`.
- **Statements (CR15, 0077):** Overview → Statements, last 3 full months for her account and card, printable; older or certified copies via a statement copy request; the assistant points to either.
- **Answering the bank's questions (CR6, 0080):** reply box on *The bank asked you*; answer sent with a receipt and Inbox notice, then "Sent to the bank on … – waiting for review"; the bank accepts or asks a follow-up with `compliance.record_request_decision`.
- **Suggested next questions in the chat:** up to 3 tappable follow-ups under the latest answer, from fixed topic lists.
- **Card actions are bank-only (CR12, 0081, 30 Oct):** no block, unblock, cancel or dispute draft for the client; unrecognised charges get urgent steps and an urgent fraud-team ticket with a `TCK-` reference. Fraud prompt v6.
- **Test cases (QA1):** Michelle C01–C10 in `docs/evaluation/test-cases.md`.
- Compliance review stays on its page after an upload; the chat is used only when the client clicks *Explain in chat*. Submitted items show one line, "Sent to the bank on … – waiting for review", with a *Need to send a different file?* link.
- Inbox moved to the header beside *Talk to a person*.
- Docker removed; docs reorganised into two workflows (client, relationship manager); backlog IDs made consistent.
