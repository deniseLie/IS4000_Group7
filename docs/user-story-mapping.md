# DemoBank user story mapping

Companion to [system-architecture.md](system-architecture.md). That file defines the platform components; this file shows how each backlog story is fulfilled by those components. The detailed behaviour here is a working draft: it will change as stories are refined, without changing the architecture.

Source: [IS4000 Product Backlog](https://docs.google.com/spreadsheets/d/1JNiqRb56N5yu2iCQCmNDPEJwddw278hMsn15UXmh45c/edit) (Google Sheet) → *Product Backlog* and *Persona (new)*.

## 1. Story IDs and personas

Story IDs follow the *Product Backlog* tab. Persona 1 (Alex, Everyday Banking Client) owns stories **EB1–EB6 and EB8**, Persona 2 (Michelle) **CR1, CR2, CR4, CR6, CR7**, Persona 3 (David) **RC1–RC4**. Row **CC** holds the common acceptance criteria that every chatbot story must also meet, so they are not repeated per story. The backlog was refined to remove overlap; removed, merged and split stories are listed in §6.

| Persona | Profile | Stories | Main agent |
|---|---|---|---|
| Alex — everyday banking | Busy professional; wants quick, concise, actionable answers; often reports several problems at once | EB1–EB6, EB8 | Everyday Banking, Fraud & Security |
| Michelle — compliance-ready client | Established client in a compliance review; wants clear explanations, document feedback and status | CR1, CR2, CR4, CR6, CR7 | Compliance Review, Document Check |
| David — relationship continuity client | Long-standing client whose RM has changed; wants an introduction, consent-based handover and a meeting | RC1–RC4 | Relationship |
| All customers | — | CC, plus foundation criteria on F5, F7, F14, F15, F18 | Platform components (see §3) |

### Common acceptance criteria (CC)

Every chatbot story also meets these:
- Asks a clarifying question when the request is unclear or missing details, instead of guessing.
- Answers only from approved knowledge or the customer's own records; never invents figures, fees, dates or procedures.
- When it cannot help, says so and directs the customer to the right official channel or a person, with the conversation context.
- A "Talk to a person" option is available at any point.
- Does not give personalised investment, legal or tax advice, and says so when asked.

## 2. Agent mapping

### Everyday Banking Agent — Alex, EB2–EB6
Purpose: resolve everyday questions quickly with short, actionable answers (Alex "prefers concise answers and immediate, actionable instructions").

| Story | What the agent does | Data / tools | Rules the agent and validator enforce |
|---|---|---|---|
| EB2 My Account Information | Signed in only: balances, recent transactions, account details, fees charged; explains a specific charge in plain language | `customer.get_accounts`, `get_transactions`, `kb.get_fact` | Guests asking about "my" account get the log-in fixed response. Masked numbers. Things it can't do (close account, dispute) → `handoff.create` with context |
| EB3 Loan Enquiry | Loan products, eligibility requirements, application steps | `kb.search`, `kb.get_fact` | Never guarantees approval; states the bank decides; directs real applications to the bank channel; loan disclaimer (F14) |
| EB4 Credit Card Enquiry | **Personal card details**: credit limit, outstanding balance, minimum payment, due date; fees and charges; concepts (minimum payment, interest, available credit) | `customer.get_credit_cards`, `kb.get_fact` | Asks which card when there are several; every figure must match the tool result or fact table; fee disclaimer (F14) |
| EB5 Failed or Pending Transaction | Explains failed vs pending; troubleshooting steps per type; signed in → looks up the transaction for a personal explanation | `customer.get_transactions`, `kb.search` | Never claims to reverse, cancel or speed up a transaction. **An unrecognised charge is EB8** (the Orchestrator plans it as a P0 Fraud & Security task) |
| EB6 Product & Service Information | No login needed: account features, fees, limits, interest; "How do I…" for transfers, updating details, statements, online banking; step-by-step instructions; links to the right place in the app or website | `kb.search`, `kb.get_fact` | Only from the agreed knowledge base; no invented procedures |

### Fraud & Security Agent — Alex, EB8
Purpose: put the urgent issue first. In Alex's persona he reports three problems at once; the chatbot must "prioritise the potentially unauthorised transaction, direct him to verified security procedures, and then help with login and payment issues".

| Story | What happens | Data / tools | Rules |
|---|---|---|---|
| EB8 Suspicious charge | Identifies the transaction (signed in), tells the customer to contact the bank immediately through official channels, **offers a temporary card block** | `customer.get_transactions`, `get_credit_cards`, `actions.propose(card.temporary_block)` | Block only after explicit confirmation; reply says "blocked" only with a receipt; never claims to investigate or reverse |
| EB8 Lost or stolen card | **Input Guardrail fixed response, no LLM**: urgent steps first, contact the bank, offers the temporary block straight away (an `action_required` event). After confirmation the Action Service blocks the card (`cards.py`) and the chat confirms it is active. Verification and permanent cancellation → `handoff.create`. Explains replacement-card steps | Action Service (`cards.py`, `handoffs.py`) | The model is never called for the urgent message; fixed wording. The agent is used only for later follow-up questions in the same case |

Why it remains a separate agent: it owns the P0 playbook, the only customer-confirmed card action, and the handoff for verification/cancellation. Keeping it apart lets it be tested strictly without affecting everyday answers.

### Compliance Review Agent — Michelle, CR1, CR4 (CR6 via the worker)
Purpose: make the review understandable and trackable without repeated advisor contact. Every answer must come from the **authorised compliance record** or **approved guidance**; when information is missing it says so.

| Story | What the agent does | Data / tools | Rules |
|---|---|---|---|
| CR1 Personalised Document Checklist | Lists each document explicitly requested in the notice, with submission instructions; each item shows one status: **not prepared**, **prepared**, **submitted – awaiting review** or **unknown**; client marks items prepared / not prepared | `compliance.get_case` (notice + requested items), `kb.search` (approved guidance), `compliance.update_checklist_item` | Adds no requirements that are not in the notice; unknown stays "unknown", never "missing"; never "accepted" unless the authorised system says so; checklist updates reflect immediately; unclear notice → recommends contacting the bank |
| CR4 Compliance Status & Information Requests | Latest status and the date it was last updated, actions required from the client vs pending with the bank; explains each recorded request for more information in plain language | `compliance.get_case` (status history, recorded requests) | Outdated or missing status is stated, not invented; no internal-only fields (e.g. risk scoring) are exposed by the tool; does not speculate on reasons for a request; unclear → support team via `handoff.create` |
| CR6 Review Deadline Reminders | Not a chat feature: the **Background Worker** finds recorded deadlines with outstanding actions and sends reminders through the **Notification Service** per preferences. The agent only answers questions about a reminder | pg_cron, outbox, Notification Service | No reminder without a recorded deadline; supported channels only |

### Document upload and Document Check Agent — Michelle, CR7 and CR2
Purpose: let Michelle submit documents in the chat and get feedback there instead of waiting days for a human first check.

| Step | Story | What happens |
|---|---|---|
| 1 Upload | CR7 | Signed-in client uploads against a checklist item to private Document Storage (formats and size limits shown first; other files rejected with a clear message); the item shows "submitted – awaiting review"; audit `document.uploaded` |
| 2 Open & legibility | CR2 | Code checks the file opens and the text is legible enough (OCR confidence); if not → "cannot assess reliably" rather than a verdict |
| 3 Predefined checks | CR2 | File opens, page count matches what is expected, required fields are present |
| 4 Explain | CR2 | The LLM phrases each flagged issue and the fix; result labelled **preliminary check, not compliance approval** |
| 5 Record | CR2 | Results stored on the case (`compliance.document_checks`); audit `document.checked` |

The check runs **synchronously** in the chat turn (feedback "in the same chat") with `status` events, so expect roughly 5–20 s for a scanned document. Tool: `docs.precheck(document_id, expected_type) → {assessable, issues[{check, finding, fix}]}`. Upload (CR7) does not use an agent: it is a Web Client + Document Storage + Action Service flow.

### Relationship Agent — David, RC1–RC4
Purpose: continuity — David should know who his advisor is, not repeat himself, and reconnect easily.

| Story | What the agent does | Data / tools | Rules |
|---|---|---|---|
| RC1 Smooth Relationship Manager Transition | When a new RM is assigned (`rm.assignment_changed` → Background Worker → system turn) or when asked: name, role, contact details, approved transition explanation | `rm.get_assignment`, `rm.get_team_profile`, `kb.search` (approved wording) | Approved wording only: no invented reasons, no unconfirmed promises; details missing → support channel |
| RC2 Handover Note for New Relationship Manager | Drafts one note from authorised data (preferences, priorities) plus the topics the client wants to discuss, organised as a short agenda; client reviews and edits the whole note; agent names the recipient and how it will be used; shares after consent | `customer.get_profile`, `agenda.save`, `consent.request`, `actions.propose(rm.share_summary)` | Shared only after consent + authorisation; confirms success or failure from the receipt |
| RC3 Instant Meeting Scheduling | Shows slots, client picks one, confirms date, time, format and joining details | `appointments.find_slots`, `appointments.hold`, `actions.propose(appointment.confirm)` | Confirms only after the scheduling system confirms; if scheduling is unavailable, gives a verified contact alternative |
| RC4 Request Intake & Status | Collects a structured request (what, urgency, deadline), confirms receipt and puts it in the RM queue. Reports each request's status (*sent → seen → answered*) and any action required from the client | `actions.propose(requests.create)`, `requests.list`, `requests.get_status`, `rm.get_assignment` | Request creation is low-risk: it executes without extra confirmation once the client submits the structured form. Never implies a request was transferred or completed unless recorded; status unavailable → follow-up contact channel |

## 3. Stories and criteria fulfilled by platform components

| Story or criterion | Where it is implemented |
|---|---|
| EB1 Login | Identity Provider (email + password, Supabase Auth), Gateway (session, profile lookup by user id), RLS + `customer_id` filters; audit `auth.session_started/ended`; wrong credentials → error message in the Web Client |
| CR7 Upload a Document | Web Client upload control, private Document Storage, Action Service (checklist status), Audit Log |
| Conversation memory (F7, former X1) | Conversation Store: same-chat memory, and after login the customer's **own previous chats** (RLS) so they can resume; topics kept separate per task |
| Never asks for secrets (F5, former X2) | Input Guardrail redaction + fixed warning; masking in Customer Data Service; validator blocks echoed secrets |
| AI notice and disclaimers (F14, former X3) | Web Client AI notice at chat start; *Compose* step appends the disclaimer to **every** loan and fee section |
| Sources shown (F15, former X5) | Response Validator (sources match the answer) + collapsible sources panel; short reason when declining |
| Informal wording (F18, former EB7) | Orchestrator *Plan* step handles informal wording, typos and abbreviations; an evaluation set of informal, misspelled, ambiguous and rephrased questions per persona |
| CC "Talk to a person" | Web Client button + `handoff.create` with a case summary so the client does not repeat themselves |
| CC clarifying questions | New contract field: `AgentResult.status` gains `needs_clarification` (with a `question`) so every agent can ask instead of guessing |

## 4. Story → component coverage

| Story | Agent | Platform components involved |
|---|---|---|
| EB1 Login | — | Web Client, Identity Provider, API Gateway Layer, Customer Data Service, Audit Log |
| EB2 My Account Information | Everyday Banking | Customer Data, Knowledge (facts), Input Guardrail (guest "my balance"), Action Service (handoff) |
| EB3 Loan Enquiry | Everyday Banking | Knowledge (facts), Orchestrator *Compose* (disclaimer) |
| EB4 Credit Card Enquiry | Everyday Banking | Customer Data, Knowledge (facts), Response Validator (figures) |
| EB5 Failed or Pending Transaction | Everyday Banking | Customer Data, Knowledge |
| EB6 Product & Service Information | Everyday Banking | Knowledge |
| EB8 Suspicious Charge & Lost Card | Fraud & Security | Input Guardrail (lost-card fixed response, fraud flag), Customer Data, Action Service (temporary block, handoff) |
| CR1 Personalised Document Checklist | Compliance Review | Customer Data (case), Knowledge (approved guidance), Action Service (checklist update) |
| CR2 Document Pre-Check | Document Check | Document Storage, LLM Gateway (vision), Audit Log |
| CR4 Compliance Status & Information Requests | Compliance Review | Customer Data (status history, recorded requests), Notification Service (stage changes), Action Service (handoff) |
| CR6 Review Deadline Reminders | — | Background Worker, Notification Service |
| CR7 Upload a Document | — | Web Client, Document Storage, Action Service (checklist status), Audit Log |
| RC1 Smooth RM Transition | Relationship | Background Worker (`rm.assignment_changed`), Customer Data, Knowledge |
| RC2 Handover Note | Relationship | Authorisation & Consent, Action Service (note share), Customer Data (profile) |
| RC3 Meeting Scheduling | Relationship | Customer Data (free slots), Action Service (hold, booking confirm) |
| RC4 Request Intake & Status | Relationship | Action Service (`requests.create`), Customer Data (requests), Notification Service |

## 5. Scenario: Alex's multi-issue message

This scenario tests Persona 1's main pain point in one chat turn. The component-by-component run of the same message (step table and sequence diagrams) is in [system-architecture.md §7](system-architecture.md#7-end-to-end-execution-flow).

Alex is signed in to the web chat (EB1). Between meetings he sends one message with three problems, which matches his pain point of not wanting to "explain multiple problems separately":

> "there's a $2,400 charge on my card i don't recognise?? also the mobile app keeps saying my login failed, and the transfer to my landlord yesterday still says pending — did it go through"

What the persona expects: *recognise all three issues, prioritise the potentially unauthorised transaction, direct him to verified security procedures, then help with the login and payment issues*.

| Task | Priority | Agent | Stories |
|---|---|---|---|
| t1 Unrecognised $2,400 card charge | **P0 fraud** | Fraud & Security | EB8 (suspicious charge, card block) |
| t2 Mobile app login failing | **P1 security** (account access) | Everyday Banking | EB6 (accessing online banking), F5 |
| t3 Transfer still pending | **P3** | Everyday Banking | EB5 (pending) |

Workflow 1 ([docs/workflows/workflow-1-alex.md](workflows/workflow-1-alex.md)) uses the wording "i got locked out of the app on my phone". That wording is ordinary app help (P3), so after the urgent charge the sections keep the customer's order: pending transfer, then app login. Wording that names a login failure or another security term (as above) still ranks the login issue P1.

Also exercised: informal wording, typos and three intents in one message (F14 planning, F18 evaluation set), same-chat follow-ups (F7), never asking for the password (F5) and sources for the login and pending-transfer steps (F15).

**Success criteria**
1. All three issues are recognised and answered in one reply, one section per issue.
2. The unrecognised charge comes first and is clearly marked urgent.
3. Alex is directed to verified security procedures and offered a temporary card block; the reply says "blocked" only after he confirms and a receipt exists.
4. The login help never asks for his password and cites an approved source.
5. The pending transfer is explained from his own transaction record, without claiming to cancel or speed it up.
6. A follow-up ("what about the landlord one?") is understood in the same chat.
7. Alex knows the next step for each issue and which one is urgent.

## 6. Backlog notes
1. Backlog refinement (19 Oct 2026) took the backlog from 24 stories to 15 plus the common criteria row (CC), and cut repeated acceptance criteria into CC. Removed and merged rows stay in the *Removed / Merged* section of the sheet:

   | Former story | Now |
   |---|---|
   | X1 Multi-turn Conversation | Acceptance criteria on F7 Conversation Store |
   | X2 Privacy & Security | Acceptance criteria on F5 Input Guardrail (masking stays in F10) |
   | X3 Transparency & Disclaimers | Acceptance criteria on F14 Conversation Orchestrator |
   | X5 Explainable Answers | Acceptance criteria on F15 Response Validator |
   | EB7 Natural Language Query | Evaluation set in F18 Hardening |
   | X4 Lost or Stolen Card | Merged into EB8 (split from EB5) |
   | CR3 Missing Document Identification | Merged into CR1 |
   | CR5 Additional Information Requests | Merged into CR4 |
   | RC5 Introductory Meeting Preparation | Merged into RC2 (one handover note) |
   | X6 Request Intake | Merged into RC4 |

   Splits and re-cuts: EB5 split into EB5 (failed or pending, Everyday Banking) and EB8 (suspicious charge and lost card, Fraud & Security); CR2 split into CR7 (upload) and CR2 (pre-check); EB2 is now signed-in account questions only, and guest product questions moved to EB6. EB3 no longer gives preliminary eligibility guidance.
2. In *Persona (new)*, Persona 1 has no title, and the *Key concern* row is shifted one column left (Michelle's concern sits under Alex).
3. RC2 lists no explicit authentication step; this design requires a signed-in session for any note sharing.
