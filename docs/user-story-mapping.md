# DemoBank user story mapping

Companion to [system-architecture.md](system-architecture.md). That file defines the platform components; this file shows how each backlog story is fulfilled by those components. The detailed behaviour here is a working draft: it will change as stories are refined, without changing the architecture.

Source: [IS4000 Product Backlog](https://docs.google.com/spreadsheets/d/1JNiqRb56N5yu2iCQCmNDPEJwddw278hMsn15UXmh45c/edit) (Google Sheet) → *Product Backlog* and *Persona (new)*.

## 1. Story IDs and personas

Story IDs follow the *Product Backlog* tab. Persona 1 (Alex, Everyday Banking Client) owns stories **EB1–EB7**, Persona 2 (Michelle) **CR1, CR2, CR4, CR6**, Persona 3 (David) **RC1–RC4**. The backlog was refined to remove overlap: the former cross-cutting stories and three persona stories were merged or turned into foundation acceptance criteria (see §6).

| Persona | Profile | Stories | Main agent |
|---|---|---|---|
| Alex — everyday banking | Busy professional; wants quick, concise, actionable answers; often reports several problems at once | EB1–EB7 | Everyday Banking, Fraud & Security |
| Michelle — compliance-ready client | Established client in a compliance review; wants clear explanations, document feedback and status | CR1, CR2, CR4, CR6 | Compliance Review, Document Check |
| David — relationship continuity client | Long-standing client whose RM has changed; wants an introduction, consent-based handover and a meeting | RC1–RC4 | Relationship |
| All customers | — | Foundation criteria on F5, F7, F14, F15 | Platform components (see §3) |

## 2. Agent mapping

### Everyday Banking Agent — Alex, EB2–EB6
Purpose: resolve everyday questions quickly with short, actionable answers (Alex "prefers concise answers and immediate, actionable instructions").

| Story | What the agent does | Data / tools | Rules the agent and validator enforce |
|---|---|---|---|
| EB2 Account Enquiry | Guest: account features, fees, limits, interest, services. Signed in: balances, recent transactions, account details, fees charged, explains a specific charge. Step-by-step service guidance with links | `kb.search`, `kb.get_fact`; `customer.get_accounts`, `get_transactions` | Asks guests to log in before personal answers (fixed response). Masked numbers. No investment/legal/tax advice. Things it can't do (close account, dispute) → `handoff.create` with context |
| EB3 Loan Enquiry | Loan products, eligibility, application steps; **preliminary** eligibility guidance from the mock profile and stated preferences; asks clarifying questions | `kb.search`, `kb.get_fact`, `customer.get_profile`, `get_loans` | Never guarantees approval; states the bank decides; directs real applications to the bank channel; loan disclaimer (F14) |
| EB4 Credit Card Enquiry | **Personal card details**: credit limit, outstanding balance, minimum payment, due date; fees and charges; concepts (minimum payment, interest, available credit) | `customer.get_credit_cards`, `kb.get_fact` | Asks which card when there are several; every figure must match the tool result or fact table; card actions it can't do → official channel; fee disclaimer (F14) |
| EB5 Transaction Issue (failed / pending) | Explains failed vs pending vs unfamiliar; troubleshooting steps per type; signed in → looks up the transaction for a personal explanation | `customer.get_transactions`, `kb.search` | Never claims to reverse, cancel or investigate. **Unfamiliar → hands the task to Fraud & Security** (the Orchestrator re-plans it as a P0 task) |
| EB6 Banking Service Information | "How do I…" for transfers, updating details, statements, online banking; step-by-step instructions; links | `kb.search` | Only from the agreed knowledge base; no invented procedures; out-of-scope → polite redirect |

### Fraud & Security Agent — Alex, EB5 (unfamiliar transaction, lost or stolen card)
Purpose: put the urgent issue first. In Alex's persona he reports three problems at once; the chatbot must "prioritise the potentially unauthorised transaction, direct him to verified security procedures, and then help with login and payment issues".

| Story | What happens | Data / tools | Rules |
|---|---|---|---|
| EB5 Unfamiliar / suspected fraud | Identifies the transaction (signed in), tells the customer to contact the bank immediately through official channels, **offers a temporary card block** | `customer.get_transactions`, `get_credit_cards`, `actions.propose(card.temporary_block)` | Block only after explicit confirmation; reply says "blocked" only with a receipt; never claims to investigate or reverse |
| EB5 Lost or stolen card | **Input Guardrail fixed response, no LLM**: urgent steps first, contact the bank, offers the temporary block straight away (an `action_required` event). After confirmation the Action Service blocks the card (`cards.py`) and the chat confirms it is active. Verification and permanent cancellation → `handoff.create`. Explains replacement-card steps | Action Service (`cards.py`, `handoffs.py`) | The model is never called for the urgent message; calm fixed wording. The agent is used only for later follow-up questions in the same case |

Why it remains a separate agent: it owns the P0 playbook, the only customer-confirmed card action, and the handoff for verification/cancellation. Keeping it apart lets it be tested strictly without affecting everyday answers.

### Compliance Review Agent — Michelle, CR1, CR4 (CR6 via the worker)
Purpose: make the review understandable and trackable without repeated advisor contact. Every answer must come from the **authorised compliance record** or **approved guidance**; when information is missing it says so.

| Story | What the agent does | Data / tools | Rules |
|---|---|---|---|
| CR1 Personalised Document Checklist & Missing Documents | Lists each document explicitly requested in the notice, with submission instructions; client marks items prepared / not prepared; shows outstanding items, separating **not submitted**, **submitted – awaiting review** and **unknown** | `compliance.get_case` (notice + requested items), `kb.search` (approved guidance), `compliance.update_checklist_item` | Adds no unsupported requirements; unclear notice → asks or recommends contacting the bank; unknown stays "unknown"; never "accepted" unless the authorised system says so; checklist updates reflect immediately |
| CR4 Compliance Milestone Tracker & Information Requests | Latest status, actions required from the client vs pending with the bank, date of last update, estimate only when reliable. Explains each recorded request for more information in plain language, with instructions and deadline; after the client responds, explains how to confirm receipt | `compliance.get_case` (status history, recorded requests) | Outdated or missing status is stated, not invented; estimates labelled as estimates; no internal-only fields (e.g. risk scoring) are exposed by the tool; does not speculate on reasons for a request; unclear → support team via `handoff.create` |
| CR6 Review Deadline Reminders | Not a chat feature: the **Background Worker** finds recorded deadlines with outstanding actions and sends reminders through the **Notification Service** per preferences. The agent only answers questions about a reminder | pg_cron, outbox, Notification Service | No reminder without a recorded deadline; supported channels only |

### Document Check Agent — Michelle, CR2
Purpose: give feedback in the same chat instead of waiting days for a human first check.

| Step | What happens |
|---|---|
| 1 Upload | Signed-in client uploads to private Document Storage (formats and size limits shown first); audit `document.uploaded` |
| 2 Open & legibility | Code checks the file opens and the text is legible enough (OCR confidence); if not → "cannot assess reliably" rather than a verdict |
| 3 Predefined checks | Missing pages, visibly incomplete required fields, and other rules defined per document type |
| 4 Explain | The LLM phrases each flagged issue and the corrective action; result labelled **preliminary check, not compliance approval** |
| 5 Record | Results stored on the case (`compliance.document_checks`) so CR1/CR4 can show "submitted – awaiting review"; audit `document.checked` |

Runs **synchronously** in the chat turn (acceptance criterion: feedback "during the same interaction") with `status` events, so expect roughly 5–20 s for a scanned document. Tool: `docs.precheck(document_id, expected_type) → {assessable, issues[{check, finding, fix}]}`.

### Relationship Agent — David, RC1–RC4
Purpose: continuity — David should know who his advisor is, not repeat himself, and reconnect easily.

| Story | What the agent does | Data / tools | Rules |
|---|---|---|---|
| RC1 RM transition | When a new RM is assigned (`rm.assignment_changed` → Background Worker → system turn) or when asked: name, role, contact details, approved transition explanation | `rm.get_assignment`, `rm.get_team_profile`, `kb.search` (approved wording) | No invented reasons; no promises beyond what is confirmed; details missing → support channel |
| RC2 Client-Controlled Context Summary & Meeting Agenda | Drafts a summary from authorised data, and organises topics the client enters into a meeting agenda; client reviews and edits both; agent names the recipient and how they will be used; shares after consent; can book the meeting (RC3) | `customer.get_profile`, `agenda.save`, `consent.request`, `actions.propose(rm.share_summary)`, `actions.propose(rm.share_agenda)` | Shared only after consent + authorisation; confirms success or failure from the receipt |
| RC3 Instant Meeting Scheduling | Shows slots, client picks one, confirms date, time, format and joining details | `appointments.find_slots`, `appointments.hold`, `actions.propose(appointment.confirm)` | Confirms only after the scheduling system confirms; if scheduling is unavailable, gives a verified contact alternative |
| RC4 Continuity of Ongoing Requests & Request Intake | Collects a structured request (who, what, urgency, deadline, country, currency), confirms receipt, shows the RM's next available time and puts it in the RM queue. Reports the status of outstanding requests (*sent → seen → answered*), client actions required and the follow-up contact | `actions.propose(requests.create)`, `appointments.next_available`, `requests.list`, `requests.get_status`, `rm.get_assignment` | Request creation is low-risk: it executes without extra confirmation once the client submits the structured form. Never implies a request was transferred or completed unless recorded; unavailable → how to get an update |

## 3. Stories fulfilled by platform components

| Story | Where it is implemented |
|---|---|
| EB1 Login | Identity Provider (email + password, Supabase Auth), Gateway (session, profile lookup by user id), RLS + `customer_id` filters; audit `auth.session_started/ended`; wrong credentials → error message in the Web Client |
| EB7 Natural Language Query | Orchestrator *Plan* step (informal wording, typos, abbreviations, ambiguity → clarifying question) + each agent; tested with rephrased-question evaluation sets |
| Conversation memory (F7, former X1) | Conversation Store: same-chat memory, and after login the customer's **own previous chats** (RLS) so they can resume; topics kept separate per task |
| Never asks for secrets (F5, former X2) | Input Guardrail redaction + fixed warning; masking in Customer Data Service; validator blocks echoed secrets |
| AI notice and disclaimers (F14, former X3) | Web Client AI notice at chat start; *Compose* step appends the disclaimer to **every** loan and fee section |
| Sources shown (F15, former X5) | Response Validator (sources match the answer) + collapsible sources panel; short reason when declining |
| "Talk to a person" (EB2, EB6, CR4, RC1) | Web Client button + `handoff.create` with a case summary so the client does not repeat themselves |

New contract field: `AgentResult.status` gains `needs_clarification` (with a `question`), because EB3–EB7 and CR1 require clarifying questions instead of guesses.

## 4. Story → component coverage

| Story | Agent | Platform components involved |
|---|---|---|
| EB1 Login | — | Web Client, Identity Provider, API Gateway Layer, Customer Data Service, Audit Log |
| EB2 Account Enquiry | Everyday Banking | Knowledge, Customer Data, Input Guardrail (guest "my balance"), Action Service (handoff) |
| EB3 Loan Enquiry | Everyday Banking | Knowledge (facts), Customer Data, Orchestrator *Compose* (disclaimer) |
| EB4 Credit Card Enquiry | Everyday Banking | Customer Data, Knowledge (facts), Response Validator (figures) |
| EB5 Transaction Issue & Lost Card | Everyday Banking → Fraud & Security | Input Guardrail (lost-card fixed response), Customer Data, Knowledge, Action Service (temporary block, handoff) |
| EB6 Banking Service Information | Everyday Banking | Knowledge |
| EB7 Natural Language Query | All | Orchestrator *Plan*, Tracing & Evaluation (rephrase tests) |
| CR1 Document Checklist & Missing Documents | Compliance Review | Customer Data (case), Knowledge (approved guidance), Action Service (checklist update) |
| CR2 Document Pre-Verification | Document Check | Document Storage, LLM Gateway (vision), Audit Log |
| CR4 Milestone Tracker & Information Requests | Compliance Review | Customer Data (status history, recorded requests), Notification Service (stage changes), Action Service (handoff) |
| CR6 Deadline Reminders | — | Background Worker, Notification Service |
| RC1 RM Transition | Relationship | Background Worker (`rm.assignment_changed`), Customer Data, Knowledge |
| RC2 Context Summary & Meeting Agenda | Relationship | Authorisation & Consent, Action Service (summary and agenda share), Customer Data (booking) |
| RC3 Meeting Scheduling | Relationship | Customer Data (free slots), Action Service (hold, booking confirm) |
| RC4 Ongoing Requests & Request Intake | Relationship | Action Service (`requests.create`), Customer Data (requests, free slots), Notification Service |

## 5. Scenario: Alex's multi-issue message

This scenario tests Persona 1's main pain point in one chat turn. The component-by-component run of the same message (step table and sequence diagrams) is in [system-architecture.md §7](system-architecture.md#7-end-to-end-execution-flow).

Alex is signed in to the web chat (EB1). Between meetings he sends one message with three problems, which matches his pain point of not wanting to "explain multiple problems separately":

> "there's a $2,400 charge on my card i don't recognise?? also the mobile app keeps saying my login failed, and the transfer to my landlord yesterday still says pending — did it go through"

What the persona expects: *recognise all three issues, prioritise the potentially unauthorised transaction, direct him to verified security procedures, then help with the login and payment issues*.

| Task | Priority | Agent | Stories |
|---|---|---|---|
| t1 Unrecognised $2,400 card charge | **P0 fraud** | Fraud & Security | EB5 (unfamiliar transaction, card block) |
| t2 Mobile app login failing | **P1 security** (account access) | Everyday Banking | EB6 (accessing online banking), F5 |
| t3 Transfer still pending | **P3** | Everyday Banking | EB5 (pending) |

Also exercised: EB7 (informal wording, typos, three intents in one message) and the foundation criteria for same-chat follow-ups (F7), never asking for the password (F5) and sources for the login and pending-transfer steps (F15).

**Success criteria**
1. All three issues are recognised and answered in one reply, one section per issue.
2. The unrecognised charge comes first and is clearly marked urgent.
3. Alex is directed to verified security procedures and offered a temporary card block; the reply says "blocked" only after he confirms and a receipt exists.
4. The login help never asks for his password and cites an approved source.
5. The pending transfer is explained from his own transaction record, without claiming to cancel or speed it up.
6. A follow-up ("what about the landlord one?") is understood in the same chat.
7. Alex knows the next step for each issue and which one is urgent.

## 6. Backlog notes
1. Backlog refinement (19 Oct 2026) took the backlog from 24 to 15 stories. Removed and merged rows stay in the *Removed / Merged* section of the sheet:

   | Former story | Now |
   |---|---|
   | X1 Multi-turn Conversation | Acceptance criteria on F7 Conversation Store |
   | X2 Privacy & Security | Acceptance criteria on F5 Input Guardrail (masking stays in F10) |
   | X3 Transparency & Disclaimers | Acceptance criteria on F14 Conversation Orchestrator |
   | X5 Explainable Answers | Acceptance criteria on F15 Response Validator |
   | X4 Lost or Stolen Card | Merged into EB5 |
   | CR3 Missing Document Identification | Merged into CR1 |
   | CR5 Additional Information Requests | Merged into CR4 |
   | RC5 Introductory Meeting Preparation | Merged into RC2 |
   | X6 Request Intake | Merged into RC4 |
2. In *Persona (new)*, Persona 1 has no title, and the *Key concern* row is shifted one column left (Michelle's concern sits under Alex).
3. RC2 lists no explicit authentication step; this design requires a signed-in session for any summary sharing.
