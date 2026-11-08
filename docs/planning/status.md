# Status (8 Nov 2026)

Story IDs follow the product backlog spreadsheet (`docs/planning/product-backlog.xlsx`, deleted in commit `712eacb`; its last version is in git history). Both personas are built; the dated sections below are history, newest first.

## Foundation

| ID | Story | State | What is left |
|---|---|---|---|
| F1 | System architecture design | Done | – |
| F2 | Data contracts | Done | – |
| F3 | Operational database and synthetic seed | Done | – |
| F4 | RM, ticket and meeting-note seed | Done (0088) | Synthetic Sophie sign-in and current book verified through the hosted-connected local app. RM access rules are F12. |
| F5 | Audit log | Done | – |
| F6 | API gateway | Done | – |
| F7 | Input guardrail | Done | – |
| F8 | LLM gateway | Done | – |
| F9 | Conversation store | Built, sheet says **No** | Saved chats work (migrations 0013–0014); tick it after a quick check. |
| F10 | Knowledge service | Done | – |
| F11 | Authorisation and consent | Done | – |
| F12 | RM role and book-based access | Done (0089) | Sophie’s verified RM role and current Michelle/Mei Ling book work in the hosted-connected local app; out-of-book refusals remain enforced. |
| F13 | Customer data service | Done | – |
| F14 | RM data functions | Done (0089) | – |
| F15–F17 | Action service, card/handoff, appointment executors | Done | – |
| F18 | Orchestrator | Done | Handover to RM added (0073). |
| F19 | Response validator | Done | Withheld answers go to the RM. |
| F20 | Stub agents | Built, sheet says **No** | The v5 agents exist (Guide, Accounts & Cards, Compliance & Documents, Relationship, and the RM Client, Requests and Calendar agents); tick it. |
| F21 | Local deployment (Docker) | Dropped | Docker removed; run with uvicorn + npm. |
| F22 | Hardening and report evidence | **Partly** | Automated: RLS and owner-only checks (`test_rls_live.py`, `test_bank_decisions.py`), audit chain (`test_audit*.py`), the 49 QA1 cases written. Still to do for the report: re-run QA1 against the hosted project with LangSmith on and keep the run record current (latest run 5 Nov), one Playwright smoke run, latency per request type from the LangSmith runs, Sophie's RLS checks (Persona 2). |
| F23 | Ticket service | Done | – |
| F24 | Request categories | Done | – |
| F25 | MCP tool layer | Done | Every agent record read goes through `tools.call`; `TOOL_TRANSPORT=direct` is the default since 31 Oct, and `mcp` sends the same calls through the MCP server. The RM tools (`rm_*`) are in the same table. |
| F26 | LangSmith tracing and evaluation | **Partly** | Client/RM tracing, versioned datasets, real API targets and independent Codex judge are working. Native Codex usage reaches the admin dashboard. Rule failures, judge review, manual workflow boundaries and annotation-queue automation remain open; see [evaluation](../evaluation/README.md). |
| F27 | Model comparison | **Not started** | Needs F26 experiments. |
| F28 | Audit trail walkthrough | Done | – |
| QA1 | Test case set (49 cases) | **Runnable / manual boundaries explicit** | [C01–C24, C11b, R01–R20 and R24–R27](../evaluation/test-cases.md) follow the current downloaded backlog IDs, titles and owners, with source row/hash and current-record adaptations. Client: 17 runnable, 8 manual; RM: 17 runnable through `/rm/assistant`, 7 manual. The runner supports independent Codex judging; deterministic and judge scores require separate review. Approval, upload and specially prepared failure/consent fixtures remain outside automated scoring. |

## Done on 8 Nov (client chat insights v2, naming, care pause retired; migrations 0143-0145, on hosted with 0142)

What changed and how to check it: [chat-insights-changes-8-nov.md](chat-insights-changes-8-nov.md).
- **Insights follow the conversation (0143, `talking_points/v2`):** a follow-up without the topic's name updates the insight while the topic is active in the chat; each topic can name a kind (HDB resale, ETFs, ...); her own words that a plan is off put it on hold, hidden from her RM until she mentions it again. Live: conversations 15 of 15 against v1's 11 of 15, no false insights or false on hold.
- **Naming (0144):** *Chat insights* on her pages, *Client chat insights* on the RM side; code keeps `talking_points`.
- **Care pause retired (0145):** a care message no longer pauses chat insights; open pauses ended (audited), `customer_pause_suggestions` dropped; the care flag and its *Talk to a person* chip are unchanged. Care cases C01-C04 and CV09 removed from the evaluation (57 messages, 14 conversations).
- **Hosted:** batches 48 (0142) and 49 (0143-0145) applied in the SQL editor; recorded SQL matches the files. The backend and frontend must run `main`.

## Done on 7 Nov (storyline alignment and Brief Writer; migrations 0115-0117, 0118)

Demo script and metrics: [storyline-michelle.md](../workflows/storyline-michelle.md).
- **Financing (0115):** private-client articles: property financing (`home-loans` v2), borrowing against investments (`lombard-financing` v1), `loans` v3; personal and car loans retired. Evaluation cases, retrieval cases and chips updated.
- **Portfolio and collateral (0116):** Michelle's advised portfolio on her Overview and on Sophie's client page and brief; a Lombard collateral alert on Today when borrowing exceeds the lending value (demo: `44_demo_lombard_alert.sql`).
- **Demo data (0117):** time deposit wording, the meeting "Property financing options", topic labels, the `financing_specialist` request type.
- **Brief Writer (0118, meeting side):** saved, versioned briefs per meeting, prepared in the background for the next 7 days, re-checked against consent on open (*Removed by client on <date>*), approved by exact version; `prepare`, `decide` (brief) and `status` in the seam.
- **Checked here:** every migration applied to a fresh PostgreSQL 16; the new SQL (RLS, refusals, collateral alert, brief save/replay/recheck/approve) by hand with psql; hosted batch 42 applied and re-run (identical SQL notice) on a copy without 0115–0117/0118; frontend unit tests (`node --test`) and an esbuild bundle of the app; the pure Python tests that import without the full dependency set. **Not run here** (no package registry in that sandbox): the full `pytest` suite with `TEST_DATABASE_LOCAL=1` (new `test_storyline_db.py`), the Playwright suites and a live model brief. Run them before merging.
- **Still open:** the Proposal Drafter, the durable lifecycle graph and `on_event`; automatic Zoom capture (free Zoom has no cloud transcript); longer-call chunking; Advisor Messaging (vision only).

## Admin dashboard (5 Nov 2026)

Built in six slices on `claude/langsmith-admin-dashboard-eeeca0` ([plan](admin-dashboard.md)): the admin role and workspace (Quality removed from the RM workspace and client view), per-request summaries on root runs, Overview and Failures, Evaluations and Prompts, Agents and Latency. Still to do by the owner: create the `demo.admin@example.com` Auth user, run `backend/supabase/hosted/32_admin_login_hosted.sql`, add the login to the persona fixture and run the opt-in hosted check (`npm run test:hosted`). Offer counts and the other new root-run facts appear only on requests made after this change; older runs show "not recorded".

## Persona 1 (client) – open items

- **Hosted project:** the migration batches through `40` are applied (36–40 on 5 Nov), and the meeting side applied 0120–0124 itself; `32_admin_login_hosted.sql` is the owner's step under *Admin dashboard* above. Run `41_eval_cleanup.sql` before each client evaluation run.
- **Reminders:** off until the worker runs. README step 5: create the `demobank_reminders` login, set `WORKER_DATABASE_URL` and `RUN_WORKER_IN_API=true`.
- **Re-run QA1** and record the results (F22 evidence); the 5 Nov hosted run was client 10 of 16, RM 16 of 17.
- **Assign owners** where the backlog or `test-cases.md` still says TBC.

## Done on 6 Nov (speed and RM answers; no migrations)

- **Frontend load:** `App.jsx` loads only the signed-in role's workspace (`React.lazy`, `lib/roles.js`), and `index.html` now loads the Stitch fonts (d258d05).
- **Admin dashboard:** runs are read through LangSmith's v2 run query, with one live read shared by every page (a232246).
- **RM answers:** every RM answer runs with the model's hidden reasoning off (`rm/handlers.REASONING`, `llm_gateway.call(reasoning=False)`; 06f790f), and a `today` section whose records all ranked lower says so instead of "none" (aa17f4a).
- **RM answer speed** ([rm-answer-speed.md](rm-answer-speed.md), `rm_assistant/v6`; cded0b1): code writes the client reports' record sections (`services/rm/brief_facts.py`) and the model only the judgement points; rule plans for one-client briefs, overviews and *plan my day*; `/rm/assistant` streams `section` events. R22 now passes at the default limits (48 s, 21k tokens; evaluation §9). Still open: thinking is most of each report call's output on the CLI, the browser streaming was not checked live, and R05 (manual) was not run.

## Done on 5 Nov (evaluation fixes, batches 2 and 3)

- **Batch 2** (a75aef8): R08 picks an RM ticket only on a topic match; a guest's personal tasks merge into one Guide task (P12) and Accounts drops the fee-waiver hint when Relationship is planned (C28); the guardrail refuses a named person's financial records (P15, `NAMED_RECORDS`); an open review's answer ends with its due date (C07); `runner_common/v2` never restates her figures, with a `validation` span (C12).
- **Batch 3** (f54d7fa): `planner/v10`, rules that receive imported values and return exports, `savings_after_cards` (`below_threshold`) and Compliance `check_failed`; P05–P07 pass. Accounts' transfer and due-day rules stay on `retail/v11` (from 3 Nov, 5cf1ec0).
- Results: [evaluation README §8](../evaluation/README.md#8-results-on-the-hosted-project-5-nov-2026-build-plan-5b).

## Done on 5 Nov (meeting lifecycle, by the meeting team; migrations 0120–0124)

- **Notes and follow-up** (45e2ea9): RM meeting proposals from Schedule, held until the client accepts; Zoom links and TXT/VTT transcript import; a Minutes Writer graph (`minutes_writer/v1`) and a restricted worker; RM review and approval of minutes, follow-ups and a recap as separate steps; the client's per-meeting transcript withdrawal (`meeting_transcript_revoke`, 0122). Off unless `MEETINGS_ENABLED=true`; 0120–0124 are applied on hosted.
- **Not built:** the pre-meeting brief writer and proposal drafter, and the seam functions in `services/meetings/__init__.py` (`prepare`, `decide`, `status`, `on_event` still raise `MeetingsUnavailable`), so 5a still waits. (Since 7 Nov: the Brief Writer and `prepare`/`decide`/`status` are built, 0118.)

## Done on 5 Nov (RM copilot build plan 2a–4 and 5b; migrations 0106–0108 on hosted)

- **Meeting requests (2a):** the client requests a meeting, withdraws it or answers a counter, in chat or on Meetings. Sophie accepts, proposes a time or declines from Schedule or the copilot. The `care` flag is set from her messages.
- **No Security agent (2b):** urgent card cases get fixed steps, plus one urgent request to the RM when she confirms.
- **RM copilot (3a, 3b):** three RM agents split by data (Client, Requests, Calendar) with one bounded loop and full tracing. A priority score in code (`services/rm/priority.py`) ranks Today, *Plan my day* and `priorities`.
- **Talking points (4, 0108):** topics are inferred from her chats only under her `chat_topics` grant, or come from her through *Let her know*. Data sharing is back in the client view.
- **Consent v2 (6a, 6b, 6d–6f):** each category can have a scope and an end, and topics can be deleted with Undo. Also built: an access log, RM asks to share, *Delete all my data*, and share switches on the meeting request card (0109, 0110, 0112–0114, `relationship/v7`). Checked natively only. Hosted batches `36`–`40` were applied on 5 Nov 2026. On hosted, only the two-argument `offer_group_confirm` has been exercised so far, by C11 and C11b.
- **Evaluation (5b):** on the hosted project, safety 8/8, planner 11/16, RM QA1 16/17 (R24–R27 all pass), graph 3/5 and client QA1 10/16. The consent step was rehearsed twice. Results, transport and failures: [evaluation README §8](../evaluation/README.md#8-results-on-the-hosted-project-5-nov-2026-build-plan-5b).
- **Still open:**
  - 5a: waits for the meeting side's `prepare`/`status`;
  - RM usefulness ratings from teammates;
  - phase 4's done-when steps in the browser on hosted;
  - the full copilot demo twice in a row (needs 5a);
  - re-running the consent cases and the demo's consent step on v2 (batches `36`–`40` are applied).
  - (The planner links of the removed C24–C26 and C28's fee-waiver handoff were fixed in batches 2 and 3, above.)

## Done on 4 Nov (v5 orchestrator, build plan phases 1–9; migrations 0100–0101)

- **Agents and graph:** one `AgentSpec` per agent with default reads per intent and a bounded tool loop only for open questions; planner → agents in parallel → gather → compose, or planner → act → compose for a Confirm/Cancel turn. Confirmations are chat turns; there is no pause.
- **Offers:** one Confirm/Cancel per offer group on the action ledger (0100); a chained offer runs only after the one before it succeeded (0101).
- **Agent dependencies (phase 7):** a task can wait for another (`depends_on`, `condition`, `for_each`, at most 5 items); exports are computed by code (trip due dates, account references); one out-of-scope handoff (fee waiver → Relationship). Travel notice tickets (0101). Planner v7.
- **Evaluation (phase 8):** graph cases C23–C28 with failure variants, planner-only dataset and target, dependency checks.
- **RM assistant on the same graph (phase 9):** RM Client Insight and RM Service Desk specs; `services/rm/graph.py` replaces `services/rm/assistant.py` (its handlers moved to `services/rm/handlers.py`); planner `rm_planner/v4` (main's v3 plus the task list); R21 one draft per client with a review due (at most 5), R22 follow-ups from the brief's latest note, R23 the client's handover note only under consent. Several drafts show as separate review cards.
- **Still open:** Compliance and Relationship still answer with their v4 responders and ignore imported values (C24); meeting reschedule and RM note offers (C23, C27); `below_threshold` and `check_failed` exports (C25, C26); `PLANNER_MODEL` and the 60 s turn budget; LangSmith sync and a scored v5 experiment against the phase 0 timings; QA1 R01–R20 still name the v4 `rm_assistant` agent (state spec). (Later: C23–C27 were removed from the graph set on 4 Nov; their planner links and C28 were fixed in batches 2 and 3 on 5 Nov.) Hosted: 0100 and 0101 applied on 4 Nov (`28_apply_0100_hosted.sql`, `29_apply_0101_hosted.sql`).

## Done on 1 Nov (Persona 2: F12, F14, RM1–RM12, RM16; migrations 0089–0090)

- **RM access (F12):** every RM function resolves the advisor from administrator-owned `app_metadata` (`demobank_role=rm`, `rm_id`) and the `rm.staff_logins` link, then checks the client is in her current book. Refusals are audited (`policy.rm_client_denied`) and return nothing. Customers cannot call RM functions or read RM tables. `audit.record` now records an RM as `staff`.
- **RM reads and approved writes (F14):** book, client 360 view with gaps, meeting notes (previous RMs' shared notes, private notes withheld), consent-checked handover note, tickets (specialist detail restricted), reviews, appointments, 14-day availability, tasks, follow-ups, handover sources (audited with sources used); RM-approved documentation, tasks, service requests (verification required, duplicates refused), client messages (on her tickets with optional status, message-only on non-restricted specialist tickets, or to the Inbox), working hours and blocks.
- **RM workspace and assistant (RM1–RM12, RM16):** `RmWorkspace.jsx` (assistant, My clients, Ticket inbox, Schedule, Follow-ups, Quality) in the client app's shell; `/rm/*` API (`api/rm.py`); the assistant (`services/rm/`, prompts `rm_planner/v1`, `rm_assistant/v1`) reads through the RM tools (MCP-capable), labels and cites every item and withholds unverified ones. Details and demo script: [workflow-2-relationship-manager.md](../workflows/workflow-2-relationship-manager.md).
- **Demo data (0090):** Sophie's own notes (one deliberately unclear), preferences, maturities and reviews, tasks (one overdue), confirmed and held appointments, and Tan Mei Ling (1007) inherited from Rachel with consent-filtered handover notes.
- **Client side:** My requests shows an RM's approved update as *Latest update*; status notes for RM-owned requests read "Your relationship manager will review it." (was "The your relationship manager…").
- **Saved RM conversations (0091):** Recent chats in the RM workspace, saved after every reply (redacted, 50 per RM, source records not stored); reopened conversations hide turns about clients who left her book, and earlier drafts are read-only.
- **RM layout and weekly meeting limit (0092):** report-style answers with numbered inline sources (copy, print/PDF); ticket inbox split into My tickets / Specialist tickets (status only), plain-word ages and due times; schedule tabs (appointments by day, availability, working-hours table); client list with live search and a tabbed client page; responsive RM and client headers. Clients can book at most 2 meetings a week with their RM (enforced in `actions.require_available_slot` and `appointments_find_slots`); status notes say "the fraud operations team". Hosted: `20_apply_0092_hosted.sql`.
- **Smaller RM book (0093):** Sophie keeps Michelle and Tan Mei Ling; Grace, Hafiz and David move to Priya (their meetings with Sophie cancelled, her open tasks for them closed), so whole-book assistant questions send less to the model. Second UI pass: availability as a week calendar beside working hours, two-column ticket details, one dropdown style, grouped report labels, working print, no underlined text links. Hosted: `22_apply_0093_hosted.sql`.
- **Fix (0094):** 0089's schema-wide revoke also removed the customer grant on `rm.is_my_advisor`, used by the RLS policies on advisors, working hours and meeting options; Michelle's Overview failed with "Record access does not match the signed-in customer". Grant restored (test added). `17_reset_michelle_demo.sql` now also removes actions whose chat it deletes; `23_repair_michelle_actions.sql` fixes a project where it already ran.
- **RM insights (0095, rm_assistant v2, rm_planner v2):** Today start page; conversation starters from large money movements, maturities and client statements; *Document this meeting* linked to the appointment and *Draft a recap*; questions across the book (`book_question`); browser dictation (RM5 stretch, demo only). Hosted: `25_apply_0095_hosted.sql`.
- **Tests:** `test_rm_workspace.py` (native SQL), `test_rm_assistant.py` (checks on sample outputs), `rm.test.js`; `test_rm_seed.py` updated for Mei Ling.
- **Still open:** apply `16_apply_0089_0090_hosted.sql` on hosted and create Sophie's login; run R01–R10 with the real model and record results; RM5 audio transcription is not built. (Since done: Sophie signs in on hosted and QA1 RM ran there on 5 Nov; the meeting side imports text transcripts, not audio.)

## Done on 31 Oct (F4, F25, CR16; migrations 0086–0088)

- **Loan enquiries (CR16, 0086–0087):** DemoBank home loan and personal loan guidance (0086, already applied on hosted; products, general eligibility, how to apply; no rates, fees or amounts), next to car loans. The general loan article v2 (0087) names the three loan types, so an unclear loan question gets "which loan?"; it also says the chat cannot see loan applications and where to ask instead. New loan suggestion chips and a `home-loan` evaluation case.
- **MCP tool layer (F25):** new tools for profile, accounts, loans, compliance case, documents, document checks, RM, introduction, requests, appointments and free calendar slots. Retail, fraud, compliance, document and relationship agents (and the pending-transfer reads) load their records through `tools.read`. `TOOL_TRANSPORT=mcp` is the default (`direct` skips MCP), and `mcp` is a core dependency (later on 31 Oct the default became `direct`). The in-process MCP call re-raises the tool's own error, so a failed read withholds only its section, as before. Each tool call is a span with `request_id` and agent.
- **Persona 2 seed (F4, 0088):** Michelle's adviser record becomes **Sophie Lim (DEMO)**, so Michelle sees no RM change. David's current assignment moves from Marcus to Sophie, with Rachel as his previous RM: 3 internal meeting notes by Rachel, his open request and a consent-shared handover note. Grace (1004) and Hafiz (1005) join Sophie's book. Wei Ming (1006, like Alex) belongs to Priya. Sophie's book has 10 open tickets across 9 categories (7 on hosted, where David is absent), including one urgent suspected fraud and one past its target. `rm.staff_logins` links `demo.sophie@example.com`, and `rm.meeting_notes` is readable by no client. Tests: `test_rm_seed.py`.
- **Still open:** apply `backend/supabase/hosted/15_apply_0087_0088_hosted.sql`, then create Sophie's login. Live (real-provider) loan answers have not been checked yet. (Since done: batch 15 applied and Sophie's login created; C12 checked a live loan answer on 5 Nov.)

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
- **Transactions, fees and meetings in chat (0085):** recent transactions, fees and "what was this charge?" (by amount and/or date) are answered from records (`agents/transaction_answers.py`); meetings: free times listed in chat, tap to hold, "Confirm booking" books it (`agents/meeting_answers.py`, same hold and appointment.confirm as the Meetings tab; superseded by 2a, where the chat drafts a meeting request). A different RM question in the same chat opens its own ticket (`customer_open_rm_ticket`); same topic still adds to the open ticket. Approved article `overseas-transfers`.
- **Still open:** CR16 has DemoBank product details for car loans only; home and personal loans need approved product information before CR16 can be marked done. Hosted: apply `backend/supabase/hosted/11_apply_0082_hosted.sql`. (Since done: home and personal loan articles in 0086 on 31 Oct; batch 11 applied.)

## Done on 29 Oct

- **Michelle's own demo data (CR12, CR13, 0075):** card 7310 used in Singapore then London on 27 Oct; a pending SGD 12,000 transfer.
- **The bank's decision on a document (CR3, CR6, 0076):** `compliance.record_bank_decision` accepts a file or asks for a correction, which shows on her review and closes when she resubmits; a near due date also triggers a reminder. Reminders she has already acted on disappear from her Inbox (0078).
- **Meetings with her RM (CR17):** My requests → Meetings (free times, hold, confirm, cancel); meeting questions are no longer turned into RM tickets; prompt `relationship/v5`. (Superseded by 2a on 5 Nov: she requests a meeting and the RM answers it.)
- **Statements (CR15, 0077):** Overview → Statements, last 3 full months for her account and card, printable; older or certified copies via a statement copy request; the assistant points to either.
- **Answering the bank's questions (CR6, 0080):** reply box on *The bank asked you*; answer sent with a receipt and Inbox notice, then "Sent to the bank on … – waiting for review"; the bank accepts or asks a follow-up with `compliance.record_request_decision`.
- **Suggested next questions in the chat:** up to 3 tappable follow-ups under the latest answer, from fixed topic lists.
- **Card controls (CR19, 0096, 2 Nov):** Overview → Cards → *Manage*: freeze and unfreeze her own card (not one the bank froze, nor during a fraud case or replacement), replace a lost, stolen or damaged card (frozen at once), change the limit (steps of SGD 1,000, max 50,000, temporary up to 90 days, declaration, once per 30 days), dispute a recognised charge within 60 days (reason list, merchant contact, one per charge). Rule refusals come back as `action_rule` with the bank's wording. Hosted: `26_apply_0096_hosted.sql`.
- **Card actions are bank-only (CR12, 0081, 30 Oct; partly reversed by 0096):** no block, unblock, cancel or dispute draft for the client; unrecognised charges get urgent steps and an urgent fraud-team ticket with a `TCK-` reference. Fraud prompt v6.
- **Test cases (QA1):** Michelle C01–C24 and Sophie R01–R20 in `docs/evaluation/test-cases.md`.
- Compliance review stays on its page after an upload; the chat is used only when the client clicks *Explain in chat*. Submitted items show one line, "Sent to the bank on … – waiting for review", with a *Need to send a different file?* link.
- Inbox moved to the header beside *Talk to a person*.
- Docker removed; docs reorganised into two workflows (client, relationship manager); backlog IDs made consistent.
