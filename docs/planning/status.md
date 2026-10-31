# Status (31 Oct 2026)

Story IDs follow [product-backlog.xlsx](product-backlog.xlsx). Focus: finish Persona 1 (client) before starting Persona 2 (RM).

## Foundation

| ID | Story | State | What is left |
|---|---|---|---|
| F1 | System architecture design | Done | – |
| F2 | Data contracts | Done | – |
| F3 | Operational database and synthetic seed | Done | – |
| F4 | RM, ticket and meeting-note seed | Done (0088) | Hosted: create Sophie's Auth user and app_metadata (hosted README, file 15). RM access rules are F12. |
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
| F25 | MCP tool layer | Done | MCP is the default transport; every agent record read goes through it. RM tools come with F14. |
| F26 | LangSmith tracing and evaluation | **Partly** | Tracing and dataset sync done; run and score the experiments. |
| F27 | Model comparison | **Not started** | Needs F26 experiments. |
| F28 | Audit trail walkthrough | Done | – |
| QA1 | Test case set (20 cases) | **Written** | All 20 in [test-cases.md](../evaluation/test-cases.md) and as eval data (`qa1_client_cases.json`, `qa1_rm_cases.json`, LangSmith `demobank-qa1-*`). Sophie R01–R10 are the Workflow 2 spec (need F12, F14 and the RM assistant). Still to do: assign owners, sync the datasets, run C01–C10 and fill the run record. |

## Persona 1 (client) – open items

- **Hosted project:** run `07`, `08`, `09` and `10` from `backend/supabase/hosted/` in order (01–06 are done).
- **Reminders:** off until the worker runs. README step 5: create the `demobank_reminders` login, set `WORKER_DATABASE_URL` and `RUN_WORKER_IN_API=true`.
- **Run the 10 test cases** and record the results (F22 evidence).
- **Assign owners** in the backlog and in `test-cases.md`.

## Done on 31 Oct (F4, F25, CR16; migrations 0086–0088)

- **Loan enquiries (CR16, 0086–0087):** DemoBank home loan and personal loan guidance (0086, already applied on hosted; products, general eligibility, how to apply; no rates, fees or amounts), next to car loans. The general loan article v2 (0087) names the three loan types, so an unclear loan question gets "which loan?"; it also says the chat cannot see loan applications and where to ask instead. New loan suggestion chips and a `home-loan` evaluation case.
- **MCP tool layer (F25):** new tools for profile, accounts, loans, compliance case, documents, document checks, RM, introduction, requests, appointments and free calendar slots. Retail, fraud, compliance, document and relationship agents (and the pending-transfer reads) load their records through `tools.read`. `TOOL_TRANSPORT=mcp` is the default (`direct` skips MCP), and `mcp` is a core dependency. The in-process MCP call re-raises the tool's own error, so a failed read withholds only its section, as before. Each tool call is a span with `request_id` and agent.
- **Persona 2 seed (F4, 0088):** Michelle's adviser record becomes **Sophie Lim (DEMO)**, so Michelle sees no RM change. David's current assignment moves from Marcus to Sophie, with Rachel as his previous RM: 3 internal meeting notes by Rachel, his open request and a consent-shared handover note. Grace (1004) and Hafiz (1005) join Sophie's book. Wei Ming (1006, like Alex) belongs to Priya. Sophie's book has 10 open tickets across 9 categories (7 on hosted, where David is absent), including one urgent suspected fraud and one past its target. `rm.staff_logins` links `demo.sophie@example.com`, and `rm.meeting_notes` is readable by no client. Tests: `test_rm_seed.py`.
- **Still open:** apply `backend/supabase/hosted/15_apply_0087_0088_hosted.sql`, then create Sophie's login. Live (real-provider) loan answers have not been checked yet.

## Done on 30 Oct (code review follow-ups, migration 0082, fraud prompt v7)

- **No duplicate RM tickets (CR9):** each handover gets a topic (complaint, complex transfer, fees or rates, advice, request to contact you). A question on a topic that already has an open RM ticket is added to that ticket as "You added: …" and she gets the same reference.
- **Complex transfers go to the RM (CR8):** large sums, trusts, holding companies and overseas moves. "How do I…" and pending/failed transfer questions stay with the assistant.
- **Context for the RM (CR8):** the ticket carries the topic, her redacted question and up to three of her earlier messages in the chat (never the assistant's replies), within 500 characters.
- **Attachments (CR9):** a document selected in the chat is attached to the RM ticket; My requests lists it by file name.
- **Helpful failures (CR9):** if the handover or a lost-card report fails, she is told nothing was sent and pointed to Talk to a person or the fraud line; the request form shows *Try again* and *Talk to a person*.
- **Confirm before reporting (CR12):** the first turn lists the charges from her records and asks for "Yes, report it" (one-tap button); only then does the urgent fraud ticket open. Fraud prompt v7.
- **Lost or stolen card (CR12):** a signed-in client in a saved chat gets an urgent fraud ticket at once (handoff reason `card_lost`); an open lost-card ticket is reused.
- **Certification checks (CR5):** identity documents need a certification line; translations need the translator's certification. Every result is labelled *Preliminary Check – Not Formal Compliance Approval*.
- **Demo data (CR14, CR15):** Michelle's Travel Card ending 5592 (annual fee and one purchase) so card questions ask which card; a S$25 transfer fee on her account.
- **Card and loan currency (CR14, 0083, retail prompt v7):** cards and loans now record their currency (SGD), so the assistant answers "SGD 316.20" instead of saying the currency is unrecorded, and Overview, Records and statements use the recorded currency.
- **Card answers from records (CR14, CR15, 0084):** card balance, credit limit, minimum payment, due date and annual fee are answered from her records without a model call, so they are never withheld or turned into an RM request. With two cards and no card named she is asked which card, with one-tap replies (each card, "Both cards"). Typo-tolerant matching (`services/typos.py`) for urgent help, routing and card answers ("wats my crad bal", "i lsot my crad"). Michelle's fees are named "Annual card fee" and "Telegraphic transfer fee". Test sheet: `docs/evaluation/assistant-prompt-tests.xlsx`.
- **Transactions, fees and meetings in chat (0085):** recent transactions, fees and "what was this charge?" (by amount and/or date) are answered from records (`agents/transaction_answers.py`); meetings: free times listed in chat, tap to hold, "Confirm booking" books it (`agents/meeting_answers.py`, same hold and appointment.confirm as the Meetings tab). A different RM question in the same chat opens its own ticket (`customer_open_rm_ticket`); same topic still adds to the open ticket. Approved article `overseas-transfers`.
- **Still open:** CR16 has DemoBank product details for car loans only; home and personal loans need approved product information before CR16 can be marked done. Hosted: apply `backend/supabase/hosted/11_apply_0082_hosted.sql`.

## Done on 29 Oct

- **Michelle's own demo data (CR12, CR13, 0075):** card 7310 used in Singapore then London on 27 Oct; a pending SGD 12,000 transfer.
- **The bank's decision on a document (CR3, CR6, 0076):** `compliance.record_bank_decision` accepts a file or asks for a correction, which shows on her review and closes when she resubmits; a near due date also triggers a reminder. Reminders she has already acted on disappear from her Inbox (0078).
- **Meetings with her RM (CR17):** My requests → Meetings (free times, hold, confirm, cancel); meeting questions are no longer turned into RM tickets; prompt `relationship/v5`.
- **Statements (CR15, 0077):** Overview → Statements, last 3 full months for her account and card, printable; older or certified copies via a statement copy request; the assistant points to either.
- **Answering the bank's questions (CR6, 0080):** reply box on *The bank asked you*; answer sent with a receipt and Inbox notice, then "Sent to the bank on … – waiting for review"; the bank accepts or asks a follow-up with `compliance.record_request_decision`.
- **Suggested next questions in the chat:** up to 3 tappable follow-ups under the latest answer, from fixed topic lists.
- **Card actions are bank-only (CR12, 0081, 30 Oct):** no block, unblock, cancel or dispute draft for the client; unrecognised charges get urgent steps and an urgent fraud-team ticket with a `TCK-` reference. Fraud prompt v6.
- **Test cases (QA1):** Michelle C01–C10 in `docs/evaluation/test-cases.md`.
- Compliance review stays on its page after an upload; the chat is used only when the client clicks *Explain in chat*. Submitted items show one line, "Sent to the bank on … – waiting for review", with a *Need to send a different file?* link.
- Inbox moved to the header beside *Talk to a person*.
- Docker removed; docs reorganised into two workflows (client, relationship manager); backlog IDs made consistent.
