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
| F22 | Hardening and report evidence | **Not started** | RLS check for Michelle (and Sophie later), audit chain check, Playwright smoke run, latency numbers for the report. |
| F23 | Ticket service | Done | – |
| F24 | Request categories | Done | – |
| F25 | MCP tool layer | Done | – |
| F26 | LangSmith tracing and evaluation | **Partly** | Tracing and dataset sync done; run and score the experiments. |
| F27 | Model comparison | **Not started** | Needs F26 experiments. |
| F28 | Audit trail walkthrough | Done | – |
| QA1 | Test case set (20 cases) | **Not started** in repo | Fill the plan doc's Test cases tab; sync to LangSmith. |

## Persona 1 (client) – open items

- **CR12 / CR13 demo data.** The suspicious-charge and pending-transfer records belong to customer 1001 (login removed). Seed Michelle with one unrecognised charge and one pending transfer if we demo these.
- **CR13–CR15** are marked No but the retail agent answers them for Michelle's records; test and tick, or leave as Should/Could.
- Run `04`, `05` and `06_keep_only_michelle.sql` on the hosted project (see `backend/supabase/hosted/README.md`).

## Done on 29 Oct

- Compliance review stays on its page after an upload; the chat is used only when the client clicks *Explain in chat*. Submitted items show one line, "Sent to the bank on … – waiting for review", with a *Need to send a different file?* link.
- Inbox moved to the header beside *Talk to a person*.
- Docker removed; docs reorganised into two workflows (client, relationship manager); backlog IDs made consistent.
