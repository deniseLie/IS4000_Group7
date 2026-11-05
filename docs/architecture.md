# DemoBank system architecture (v4, as built)

Status: **as built** (2026-10-31; client view CR1–CR17 refined to the finalised product backlog, see §5.1.1). This document describes the system that is in the code today, using the component names of the v3.1 proposal (the draw.io diagram "DemoBank — Proposed Multi-Agent Architecture"). Every place where the build differs from that proposal is listed in [§10](#10-differences-from-the-v31-proposal). `chatbot-architecture.md` describes the original single-path (v1) answer flow; `multi-capability-architecture.md` describes the intent-router step. The stories are in [the product backlog](planning/product-backlog.xlsx); the two workflows (client, relationship manager) are in `docs/workflows/`. Coursework demo with synthetic data only.

v4 changes from v3.1: the document now records what was built rather than what was planned. The overall shape is unchanged (one FastAPI process, the same components and contracts), but the orchestrator has eight nodes instead of four, agents read their data in code and make one model call instead of calling tools themselves, only the Fraud & Security agent proposes actions, most other changes start from forms in the Web Client, the Background Worker sends notifications only, and the multi-agent pipeline is switched on with `PIPELINE=v2` (v1 is still the default).

**Target v5 (agreed 3 Nov 2026, not built yet):** §14 records the agreed redesign (orchestrator–workers graph, agent catalogue with a bounded tool loop and typed exports, confirmations as chat turns, external read-only tools). §5.6.1 lists the target agents and their data boundaries. Everything else describes v4 as built.

---

## 0. Design principles

All seven v3.1 principles are implemented; the notes say how.

1. **Deterministic before probabilistic.** Rules and fixed responses run before any LLM. Safety-critical replies (lost card, secrets, human support, a guest asking about "my" balance) never call the model. The planner uses keyword rules first; fraud priority, action proposals and decision wording are chosen by code.
2. **Modular monolith.** Every backend component is a Python module in one FastAPI process. Modules call each other as functions. The only other process is the optional notification worker (same codebase).
3. **Agents reach data only through service functions.** Agents never query tables; they call the Customer Data and Knowledge services, which use the caller's JWT so RLS applies. In the build, the agent code makes these calls itself before the model runs; the model never chooses a tool (§5.6).
4. **Policy check on every tool call.** `authorization.require_permission` checks identity, customer scope, the agent's allow-list and action type, and audits every decision.
5. **No action without a receipt.** Every state change goes through the Action Service and one SQL transaction that writes the change, receipt, audit event and outbox row together. Agents may only propose; only the customer (or, for staff types, a staff member) confirms.
6. **Grounded output only.** Each section is checked against its own evidence; a failed section is withheld, never replaced by stored text. For a signed-in, saved conversation the failure also records a support handoff.
7. **Everything auditable, nothing sensitive in logs.** Audit events and traces hold ids, enums and counts only; redacted chat text lives only in the conversation store.

---

## 1. Naming map

| Draw.io label | Component | Built as | Main code |
|---|---|---|---|
| Customer · chat + inbox | **Web Client** | React SPA: chat, Records, Actions, Relationship, Compliance, Inbox and Data sharing pages | `frontend/src/` |
| — (implicit) | **Identity Provider** | Supabase Auth (hosted) | — |
| API gateway | **API Gateway Layer** | ASGI middleware + `Depends(get_ctx)` | `core/gateway.py`, `core/auth.py`, `core/ratelimit.py`, `core/errors.py` |
| ① Safety guard | **Input Guardrail** | Rules only | `services/guardrail.py`, `privacy.py`, `safety.py` |
| ② Orchestrator | **Conversation Orchestrator** | LangGraph graph, eight nodes, includes the composer | `services/orchestrator.py` |
| shared case state | **Conversation Store** | Saved conversations + signed workflow checkpoints | `services/conversations.py`, `checkpoints.py`, `workflow_store.py` |
| ③ five agents | **Domain Agents** | Five agents: Complex Banking Needs (`retail`), Fraud & Security, Compliance Review, Document Check, Relationship | `services/agents/` |
| ④ Knowledge search | **Knowledge Service** | Postgres `kb` tables: hybrid search with keyword fallback | `services/kb.py`, `embeddings.py` |
| ④ Customer 360 tools | **Customer Data Service** | Read functions over audited SQL RPCs | `services/customer_data.py`, `agents/*_records.py` |
| ④ Action ledger | **Action Service** | `run_action()` + SQL executors | `services/actions/` |
| ④ Consent service | **Authorisation & Consent Service** | `decide()` + `has_consent()` | `services/authorization.py`, `consent.py`, `policy.py` |
| ④ Scheduling | — | Slot reads in Customer Data; hold/confirm/cancel in `appointments.py` | `api/relationship.py`, `actions/appointments.py` |
| ④ Notifications | **Notification Service** | In-app inbox + Supabase Realtime | `services/notifications.py`, `api/notifications.py` |
| ⑤ Composer | — | `compose` node of the orchestrator | `orchestrator.answer_for` |
| ⑥ Validator | **Response Validator** | Per-section checks inside each agent + `act` node (`escalate_validation`) | `services/source_checks.py`, `agents/answer.py` |
| Reply over SSE | **Streaming Response (SSE)** | `fetch` + parser | `api/chat.py`, `frontend/src/lib/chatTools.js` |
| Human handoff queue | **Human Handoff** | `handoffs.py` executor + staff script | `actions/handoffs.py`, `actions/staff.py`, `scripts/staff_support.py` |
| Proactive triggers + event bus + rules | **Background Worker** | Outbox poller + reminder scheduler (no pg_cron) | `app/worker.py`, `core/lifecycle.py` |
| Observability | **Tracing & Evaluation** | LangSmith runs (redacted), JSON request logs, eval harness, admin dashboard | `core/observability.py`, `core/langsmith_client.py`, `backend/evaluation/`, `services/quality.py`, `services/insights.py`, `api/admin.py` |
| — (new) | **Audit Log Service** | Append-only hash-chained `audit.events` | `services/audit.py`, migrations `0005`–`0007`, `0054` |
| LLM provider | **LLM Gateway** | One client for planner and agents | `services/llm_gateway.py`, `prompts.py` |
| Supabase Postgres | **Operational Database** | Hosted Supabase Postgres + RLS | `backend/supabase/migrations/` |
| Supabase Storage | **Document Storage** | Private bucket `demobank-compliance-private` | `services/document_uploads.py` |

---

## 2. System architecture

Shown for `PIPELINE=v2` with `CONVERSATION_STORE=database` (the configuration used for the persona workflows). Lines go to and from groups, not to each agent or service.

```mermaid
flowchart TB
  WEB["Web Client<br/>chat, Records, Actions, Inbox"]
  IDP["Identity Provider<br/>Supabase Auth"]
  WORKER["Background Worker<br/>outbox poller + reminder scheduler"]

  subgraph API["Backend API — one FastAPI process"]
    direction TB
    GW["API Gateway Layer<br/>JWT, rate limit, size limit, SSE"]
    IG["Input Guardrail<br/>rules only, no LLM"]
    ORC["Conversation Orchestrator<br/>plan, prioritise, dispatch,<br/>actions, escalate, compose"]
    subgraph AGENTS["Domain Agents"]
      direction LR
      A1["Complex Banking Needs"]
      A2["Fraud & Security"]
      A3["Compliance Review"]
      A4["Document Check"]
      A5["Relationship"]
    end
    subgraph SVC["Shared Services"]
      direction LR
      S1["Knowledge"]
      S2["Customer Data"]
      S3["Action<br/>cards, handoffs,<br/>appointments, records"]
      S4["Authorisation & Consent"]
      S5["Notification"]
    end
    VAL["Response Validator<br/>per section"]
    REST["Record & form routes<br/>/customer, /relationship,<br/>/documents, /commands"]
  end

  LLM["LLM Gateway → OpenRouter"]
  AUDIT["Audit Log Service<br/>append-only"]
  DB[("Operational Database<br/>Postgres + RLS")]
  STORE[("Document Storage")]

  WEB -->|HTTPS + SSE| GW
  WEB -.->|sign-in| IDP
  WEB -.->|upload with user JWT| STORE
  GW --> IG --> ORC --> AGENTS --> SVC
  AGENTS --> VAL --> ORC
  ORC -->|reply| GW
  GW --> REST --> SVC
  AGENTS --> LLM
  SVC --> DB
  SVC --> STORE
  WORKER --> DB
  WORKER --> S5
  API -.->|every event| AUDIT --> DB
```

Lines that are deliberately **not** drawn: every component writes audit events and trace spans (shown once as the dotted `API → Audit` line); the orchestrator also calls the LLM for planning; Notification pushes inbox items to the Web Client through Supabase Realtime; inside Shared Services, Customer Data does every read and the Action Service every write, both asking Authorisation & Consent first. The Background Worker does **not** start orchestrator turns (§5.10).

The second entry point (`REST`) matters: the Web Client reads records and starts most actions directly (hold a slot, confirm a booking, grant consent, save an agenda, mark a checklist item, upload or submit a document, create a request). These calls go through the same gateway, Customer Data, Action Service and audit path as chat, but no agent or model is involved.

---

## 3. Runtime model

| Process | Contains | Notes |
|---|---|---|
| `web` | React SPA | Vite dev server locally; static build for hosting |
| `api` | Gateway, Guardrail, Orchestrator, Agents, all Shared Services, Validator, Audit client, LLM Gateway | `uvicorn app.main:app` |
| `worker` | Background Worker | `python -m app.worker` with its own restricted database login (`WORKER_DATABASE_URL`); or inside `api` with `RUN_WORKER_IN_API=true` |
| Supabase (hosted) | Auth, Postgres, Storage, Realtime | Not built by us. pg_cron is not used |

Communication inside `api` is plain function calls. Network hops: browser ↔ API, browser ↔ Supabase Auth/Storage/Realtime, API ↔ Supabase Data API (PostgREST, always with the caller's JWT), worker ↔ Postgres, API ↔ OpenRouter. Document parsing runs in a short-lived child process with no credentials (§5.6).

**Configuration switches** (`core/config.py`, defaults first):

| Setting | Values | Effect |
|---|---|---|
| `PIPELINE` | `v1` \| `v2` | `v1` is the original single-path `make_answer` flow; `v2` is the orchestrator and agents described here. Personal v2 chat requires saved conversations |
| `EMBEDDING_API_KEY` | key \| empty | Hybrid knowledge search when set; keyword search when empty or the provider fails. Knowledge always comes from audited Postgres `kb` sections (no switch since `0102`) |
| `CONVERSATION_STORE` | `off` \| `database` | Saved conversations, server-side history and durable workflow checkpoints |
| `CHAT_TRANSPORT` | `api` \| `codex` | OpenRouter via LangChain, or the local Codex CLI (manual alternative) |
| `RUN_WORKER_IN_API` | `false` \| `true` | Run the notification worker inside the API process |

Chat has two modes, chosen from the login with no UI switch: `public` (guests, approved knowledge only) and `personal` (signed-in customer: approved knowledge plus own records). Older saved `public` chats of signed-in users reopen in their saved mode; New chat returns to `personal`.

---

## 4. Canonical contracts

Pydantic models in `backend/app/schemas/contracts.py` (shared; change only by a PR reviewed by all developers) and `schemas/events.py`. The examples below are round-tripped by `tests/test_contracts.py`.

**RequestContext** (created by the gateway, passed to every module)
```json
{ "request_id": "uuid", "session_id": "uuid", "case_id": "uuid|null",
  "principal": { "kind": "guest|customer|system", "user_id": "uuid|null", "customer_id": "bigint|null",
                 "auth_session_id": "uuid|null", "aal": "aal1|aal2", "jwt": "<never logged>" },
  "agent": "retail|fraud|compliance|document|relationship|null",
  "locale": "en-SG", "received_at": "iso8601" }
```
`agent` is set only by backend dispatch and never read from model output; Authorisation uses it for the allow-list.

**RiskAssessment** (Input Guardrail → Orchestrator)
```json
{ "redacted_message": "string", "redactions": [{"type": "card_number|password|pin|otp|cvv|nric|email", "count": 1}],
  "risk_flags": ["fraud_suspected","lost_card","secret_shared","human_requested"],
  "priority": "P0|P1|P2|P3", "fixed_response": {"code": "lost_card_v1", "needs_human": true} }
```
`fixed_response` set → the orchestrator is skipped and the fixed reply is returned.

**TaskPlan** (Orchestrator, saved in the workflow checkpoint)
```json
{ "plan_id": "uuid", "tasks": [{ "task_id": "t1", "domain": "fraud|retail|compliance|document|relationship|smalltalk",
  "intent": "report_unrecognised_txn", "priority": "P0", "entities": {"amount": 2400},
  "needs_auth": true, "status": "pending|running|awaiting_confirmation|done|failed" }] }
```
Priority: **P0 fraud > P1 security > P2 regulatory deadline > P3 everything else**; ties keep message order. (Since build plan 2b: P0 urgent card, P2 compliance deadline or RM handover, P3 the rest; P1 is no longer assigned.) In the build the planner fills `intent: "answer_question"` and `entities: {question}` (the part of the message for that task); intent and entity extraction were not needed.

**AgentResult** (Agent → Orchestrator)
```json
{ "task_id": "t1", "agent": "fraud", "status": "ok|needs_clarification|needs_confirmation|needs_human|error",
  "answer_kind": "banking|conversation", "text": "string", "question": "string|null",
  "citations": [{"source_id": "kb:card-dispute@v3"}], "tool_calls": [{"call_id": "uuid", "tool": "customer.get_transactions"}],
  "pending_action": {"command_id": "uuid", "type": "card.temporary_block", "summary": "Temporarily block card ending 4821"},
  "receipts": [{"receipt_id": "rcpt_...", "command_id": "uuid"}] }
```
`question` is required exactly when `status` is `needs_clarification`.

**ActionCommand / Receipt** (Action Service; the executor is looked up from `type`, never chosen by the model)
```json
{ "command_id": "uuid", "idempotency_key": "case_id:task_id:type", "type": "card.temporary_block",
  "target": {"card_id": "uuid"}, "state": "proposed|confirmed|succeeded|failed|expired", "expires_at": "iso8601" }
{ "receipt_id": "rcpt_...", "command_id": "uuid", "outcome": "succeeded|failed", "audit_event_id": 1042 }
```
State and `expires_at` are assigned by the database, not by Python. Stored commands can also be `rejected`.

**AuditEvent** — see §6.

**SSE events** (`POST /chat`)

| Event | Payload | When |
|---|---|---|
| `conversation` | saved session/turn metadata | Saved conversations only; before the answer and again after it is stored |
| `status` | text, e.g. "Checking the available information…" | Always first |
| `answer` | `AnswerEvent` + `sections[]` (one per task, priority order; each with `pending_action` or `decision`) | Normal reply |
| `workflow_paused` | `{workflow, actions[ActionRequiredEvent], preview: AnswerEvent}` | A proposed action needs the customer's decision; replaces the proposed `action_required` event |
| `done` / `error` | unchanged | Always last |

`ActionRequiredEvent {command_id, type, summary, expires_at}` exists as proposed, but travels inside `workflow_paused` rather than as its own event. `GET /workflows/{turn_id}/preview` rebuilds the paused preview from the signed checkpoint after a reload.

---

## 5. Component catalogue

Five fields per component: **Category**, **Responsibility & boundaries**, **Inputs / outputs**, **Infrastructure / pattern**, **Dependencies** (↑ callers, ↓ callees).

### 5.1 Web Client
- **Category:** Presentation tier (SPA).
- **Responsibility & boundaries:** Bank-site layout with pages for overview, chat (with Recent chats history), Relationship (guided RM checklist, requests, appointments, Data sharing & permissions), Compliance workspace, Inbox; the Actions drawer reviews and confirms proposals; Records shows the customer's own synthetic data and private documents. `CardBlockDialog` confirms or rejects a card block inside the chat. Client-side redaction preview mirrors the Input Guardrail (`lib/chatTools.js`). No business logic or secrets; only `VITE_` variables.
- **Inputs / outputs:** Out: `POST /chat`, `POST /commands`, `POST /commands/{id}/confirm|reject`, `POST /workflows/{turn}/resume`, record and form routes (`/customer/*`, `/relationship/*`, `/documents/*`, `/consent/*`, `/notifications/*`, `/conversations/*`), `POST /auth/session-end`, each with bearer JWT; direct Storage uploads with the user's JWT. In: SSE stream; Realtime inbox channel.
- **Infrastructure / pattern:** React + Vite. SSE is read with `fetch()` + `ReadableStream` and a small parser, because `EventSource` supports only `GET` without an `Authorization` header.
- **Dependencies:** ↓ Identity Provider, API Gateway Layer, Document Storage, Notification Service (Realtime).

#### 5.1.1 Client view (Michelle) by story — finalised backlog, 31 Oct 2026

The client stories are all implemented. The RM stories (RM1–RM16) are **not built yet**, so the RM side of handovers,
ticket status and meetings is the shared database record plus the support staff queue (`scripts/staff_support.py`).

| Story | Where it lives in the client | How it decides |
|---|---|---|
| CR1 Login | `Login.jsx` (masked password, Show/Hide, Forgot password guidance, one neutral error); sign in from the header, the sidebar and every locked page; sign out from the sidebar | Supabase Auth; personal pages locked without a session |
| CR2 General help | Assistant (guest mode); "Go to …" page buttons (`lib/pageLinks.js`) | Approved articles only; personal questions ask her to sign in |
| CR3–CR6 Compliance | Compliance review: checklist marks, upload, preliminary check, bank status | Her mark, the system's "Submitted", and the bank's decision are separate fields; only the bank sets Accepted/Correction |
| CR7 Reminders | Inbox | Recorded deadlines only |
| CR8–CR10 Handover and tickets | Assistant preview ("Here's what I'd send…") with Send to my RM / Add to TCK / Open a new request / Cancel; My requests | Rules choose RM vs specialist; nothing is sent until she chooses; fraud never goes to the RM |
| CR11 Card limit | Assistant explanation + request form (latest stated amount) | Cards and credit team decides |
| CR12–CR13 Fraud, lost card, pending | Assistant: urgent steps, charge list + "Yes, report it"; lost card "Yes, report my card" | Rules from records; urgent fraud ticket only after she confirms |
| CR14–CR15 Cards and account | Assistant (records answers, "which card?", follow-ups), Overview | Figures copied from records |
| CR16 Loans | Assistant (home, personal, car loan articles) | No eligibility, rate or approval promises |
| CR17 Meetings | Assistant (free times → hold → Confirm booking) and My requests > Meetings | Same hold and confirm actions in both places |

**Rules versus AI.** Actions and safety (handover, fraud and lost-card reports, bookings, confirmations) are decided by rules,
with her explicit confirmation. Explanations are written by the AI from approved articles and her records and checked by the
Response Validator. Several record questions (card figures, transactions, fees, card limit) are currently answered by rules
because the validator withheld correct AI answers; moving them back to AI wording with record facts is the planned next step.

#### 5.1.2 UI conventions (web client)

- **Buttons:** three kinds only. *Primary* (`.primary-button`, red) for the one main action on a screen; *secondary*
  (`.bank-chat-logout`, outline) for everything else; *icon* (`.icon-button`, round) for icon-only actions with an
  `aria-label`. All share one height (38 px), radius (8 px), weight, hover, press (scale .97) and keyboard focus ring.
  One-tap replies in the chat (`.follow-ups button`) are pills.
- **Signing in and out** are offered in the same places: header (Sign in), sidebar session area (Sign in when signed out,
  Sign out when signed in) and every locked page.
- **Motion:** pages settle in when opened (`.page-enter`, 0.3 s, children staggered); replies and one-tap replies rise in;
  loading shows a moving red bar with pulsing text (`.loading`) or shimmering skeletons for lists and cards. All motion is
  switched off under `prefers-reduced-motion`.
- **Colours** are unchanged (`--red`, `--ink`, `--line`, `--canvas`, `--tint`); new tokens are only `--btn-h`,
  `--btn-radius`, `--ease-out` and `--focus-ring`.

### 5.2 Identity Provider
- **Category:** OAuth2 / OIDC identity provider.
- **Responsibility & boundaries:** Sign-in, sign-out, token refresh, JWKS, MFA (`aal2`). Keeps its own auth log. Staff accounts carry an administrator-set `app_metadata.demobank_role=support`. Does not authorise banking operations.
- **Inputs / outputs:** In: credentials. Out: JWT (`sub`, `session_id`, `aal`, `exp`).
- **Infrastructure / pattern:** Supabase Auth (hosted). Persona logins live in a git-ignored `personas.json`.
- **Dependencies:** ↑ Web Client, API Gateway Layer (JWKS). ↓ Operational Database (`auth` schema).

### 5.3 API Gateway Layer
- **Category:** Edge middleware inside the backend.
- **Responsibility & boundaries:** Verifies JWT (cached JWKS), resolves `customer_id`, builds `RequestContext`, rate-limits (in-process token bucket), limits payload size, sets CORS, opens the SSE stream, maps errors to stable codes, and creates the per-request user database client (`request.state.db`). Records `auth.session_started` the first time it sees a new `session_id`. No domain logic.
- **Inputs / outputs:** In: HTTPS. Out: `RequestContext` to the route; SSE events.
- **Infrastructure / pattern:** Pure ASGI middleware + FastAPI dependency (`core/gateway.py`, `core/auth.py`, `core/ratelimit.py`, `core/errors.py`, `api/auth.py`). Error body `{"error": {"code", "message", "request_id"}}`; codes include `auth_invalid`, `auth_expired`, `auth_required`, `profile_missing`, `rate_limited` (with `Retry-After`), `payload_too_large`, `auth_unavailable`, `audit_unavailable`, `conversation_unavailable`, `personal_unavailable`. Guests send an optional `X-Session-Id`; signed-in sessions use the JWT `session_id`. One TLS context is reused for all per-request Supabase clients.
- **Dependencies:** ↑ Web Client. ↓ Identity Provider, Input Guardrail, Conversation Store, Orchestrator, record routes, Action Service, Audit Log Service.

### 5.4 Input Guardrail
- **Category:** Deterministic pre-filter / data loss prevention.
- **Responsibility & boundaries:** `assess_request` redacts secrets and PII, counts redactions, trims history, detects lost card, fraud wording (including informal wording, `guardrail.FRAUD`), human-support requests and guest "my balance" questions, assigns priority and returns fixed responses. Runs before any personal data is loaded. **Never calls an LLM.**
- **Inputs / outputs:** In: `ChatRequest`. Out: `RiskAssessment`.
- **Infrastructure / pattern:** Python rules (`guardrail.py`, `privacy.py`, `safety.py`); fixed reply texts in `answers.py`; shared fixtures `tests/fixtures/privacy-cases.json` used by backend and frontend.
- **Dependencies:** ↑ `/chat`, Conversation Store (`chat_store.prepare` stores the guarded question before any provider work). ↓ Orchestrator (or the fixed reply); Audit Log Service.

### 5.5 Conversation Orchestrator
- **Category:** Supervisor, planner–executor workflow.
- **Responsibility & boundaries:** Turns a message into a `TaskPlan`, runs the agents concurrently, records the proposed actions, pauses for the customer's decision, writes the recorded outcome into the reply, records a handoff for withheld sections and composes one reply. It does not answer banking questions itself. Runtime identity (`RequestContext`, owned turn) is passed as LangGraph runtime context, never stored in checkpoint state.
- **How it works:** a LangGraph `StateGraph` with eight nodes. Edges after `resume_pending`, `dispatch` and `propose_actions` are conditional, so nodes with nothing to do (all action nodes for guests and plain answers) are skipped. Saved turns use the owned checkpoint saver with synchronous durability, so the state after every node is in Postgres.

```mermaid
flowchart LR
  IN(["RiskAssessment"]) --> RP["1 resume_pending"] -->|new message| P["2 plan"] --> R["3 prioritise"] --> D["4 dispatch"]
  RP -->|reply to our question| R
  D -->|nothing to act on| C["8 compose"] --> OUT(["AnswerEvent"])
  D -->|fraud or RM task| A["7 act"] --> C
  D -->|fraud proposal, owned turn| PA["5 propose_actions"] --> A
  PA -->|decision pending| RA["6 review_actions"] --> A
  RA -. "proposal pending: interrupt" .-> W(["workflow_paused<br/>wait for confirm/reject"]) -. "resume" .-> RA
```

| Node | What it does | LLM? |
|---|---|---|
| 1 resume_pending | A reply to a question in our previous reply (charge list, lost-card offer, RM preview, which card, meeting time) goes straight to `prioritise`; a fresh request matched by `fixed_intent` still goes to `plan`. Clears last turn's `pending_reviews` and `decision_sections` | No |
| 2 plan | A document/compliance record selected in the UI routes straight to that agent. `fixed_intent` and `critical_route` rules decide fraud, login, RM and a few fixed intents; otherwise one structured-output planner call splits the message into `{domain, question}` issues (`CLIENT_INTENT_ROUTING=rules` uses `simple_issue` first). English results are re-checked by routing rules (`route_issues`) | Unless a rule decides |
| 3 prioritise | Adds a fraud task if the Guardrail flagged `fraud_suspected`/`lost_card` and the planner missed it; assigns P0 fraud, P1 security wording, P2 compliance/document deadline wording, P3 otherwise | No |
| 4 dispatch | Runs every task concurrently (`asyncio.TaskGroup`), each bounded by `AGENT_TIMEOUT_S`. Each task loads its own evidence and calls its agent. Expected provider/data failures withhold only that section, with no evidence or proposal released | Agents |
| 5 propose_actions | Retired by 0081: records nothing for new turns (`offer_card_actions` kept for older saved turns and tests). Reached only with a fraud proposal in an owned turn | No |
| 6 review_actions | Loads the ledger; if any proposal is still `proposed`, pauses the graph (`interrupt`). After resume, the ledger, not the resume value, decides what happened | No |
| 7 act | `settle_actions`: fraud charge list to confirm, then the P0 fraud-team handoff after "Yes, report it"; fixed decision text for older recorded decisions. `escalate_validation`: RM handover preview/choice and, with `VALIDATION_HANDOFF=1`, one `general_support` handoff for withheld sections (no rejected text in its summary). Fixed command ids and idempotency keys make a retry safe | No |
| 8 compose | Deterministic merge: one section per task in priority order, citations kept with their section, decisions written into their own task's section, overall `needs_human`/`source_check`/origin computed | No |

Confirming or rejecting (`POST /commands/{id}/confirm|reject`) resumes the same graph from its checkpoint; `POST /workflows/{turn}/resume` retries a resume that failed. The final reply is stored on the saved turn, and the client reads it from the conversation.

- **Inputs / outputs:** In: `ChatRequest` + `RiskAssessment` + runtime context. Out: `AnswerEvent` or `WorkflowPausedEvent`; checkpoints and workflow rows in the Conversation Store.
- **Infrastructure / pattern:** LangGraph `StateGraph` + owned Data API checkpoint saver (`services/checkpoints.py`) with HMAC-signed envelopes and a closed JSON codec (no pickle).
- **Dependencies:** ↑ `/chat`, `/commands/{id}/confirm|reject`, `/workflows/{turn}/resume`. ↓ Domain Agents, Knowledge Service, LLM Gateway (planner), Action Service, Conversation Store, Audit Log Service.
- **Target (v5, not built):** a five-node orchestrator–workers graph (`planner`, `agent`, `gather`, `act`, `compose`) with confirmations as chat turns and no pause; see §14.

### 5.6 Domain Agents
- **Category:** Bounded-context agents (five, drawn separately in §2).
- **Responsibility & boundaries:** Each agent owns one business area, has its own versioned prompt and an allow-list of tools (`agents/registry.py`), and returns an `AgentResult` plus the evidence it used. **How agents work in the build:** the agent code reads the records it needs through Customer Data and Knowledge (each read checked against the allow-list by `require_permission` with `ctx.agent` set), turns them into masked evidence, then makes **one** structured-output model call with that evidence. The model never chooses a tool, and no model output executes an action. The draft is then validated (§5.8). In `public` mode and for small talk, every domain uses the shared public answer agent over approved knowledge.

| Agent (`domain`) | Reads (in code) | Model writes | Actions | Must not do |
|---|---|---|---|---|
| **Complex Banking Needs** (`retail`) | Profile, accounts, transactions, credit cards, loans; knowledge | Explanation with citations | None from chat ("Talk to a person" handoffs come from the UI) | Change anything; give investment, legal or tax advice; promise approval; investigate or reverse transactions |
| **Fraud & Security** (`fraud`) | Masked transactions and cards (`fraud_records.py`); fraud articles | Description of the matched charge and urgent steps | Code (`fraud.decision_for`) decides which card to offer for a temporary block; a rule-flagged charge with one eligible card/transaction pair is always offered. The orchestrator records the proposal; a succeeded block records a P0 handoff | Execute without confirmation; claim a block without a receipt; reverse or permanently cancel (staff only) |
| **Compliance Review** (`compliance`) | Client-visible compliance case, requested items, status history; the record the customer selected | Explanation of requests and statuses | None from chat; checklist marks are set by the customer in Records (`compliance.update_checklist_item`) | Invent requirements or deadlines; mark documents accepted; reveal internal fields |
| **Document Check** (`document`) | The recorded precheck report for the selected document (`prechecks.workspace`) | Explanation of the recorded findings | None | See or quote document contents (they never enter model evidence); present results as approval |
| **Relationship** (`relationship`) | RM assignment, introduction/transition notice, requests, appointments, 14-day slot search, consent records | Explanation with per-dataset citations | None from chat; each reply offers a hand-off button to the Relationship page, where holds, bookings, consent, agendas, sharing and requests are done with forms | Share data without consent; book; draft agendas with AI; invent transition reasons |

Document checking itself is not an LLM step. `prechecks.py` and `notice_checks.py` apply source-quoted requirements from the owned compliance notice and approved guidance, plus configured calendar and page checks, optionally with local OCR. Technical inspection (`document_inspection.py`) opens PDF/DOCX/PNG/JPEG files in a bounded child process with no credentials (`document_parser_*`) and returns counts only. Neither produces an approval or changes case status.

Stories without an agent are handled by platform components: CR1 login (Identity Provider, Gateway), CR4 upload (Web Client, Document Storage, `document.register`), CR7 reminders (Background Worker, Notification Service).

- **Inputs / outputs:** In: `{task, history, language, RequestContext with agent, public evidence}`. Out: `(AgentResult, evidence[], prompt_id)`; Fraud also returns a bounded `FraudProposal`.
- **Infrastructure / pattern:** Code-driven reads + one strict-JSON model call per task; prompts in `backend/app/prompts/<agent>/vN` (released versions immutable).
- **Dependencies:** ↑ Orchestrator. ↓ Customer Data, Knowledge, Authorisation, LLM Gateway.

#### 5.6.1 Target agents and boundaries (agreed 3 Nov 2026, not built yet)

The agents above are split by topic. The target set is split by **the data an agent may see and the harm a mistake could do**: five client agents and two RM agents. It goes with the target orchestrator in §14 (`planner` → specialist agents in parallel → `act` → `compose`; confirmations are chat turns, no pause). Agents read through a bounded tool loop (at most 4 tool calls) instead of fixed reads in code, and pass typed exports to each other through the graph (§14.6).

| Target agent | Replaces | Reason |
|---|---|---|
| **Guide** (public) | shared public answer agent, `smalltalk` | Guests and general questions get an agent with no personal data, instead of borrowing Retail's name |
| **Accounts & Cards** | `retail` without login, tickets and RM requests | One financial data domain |
| ~~**Security**~~ (removed in build plan 2b) | `fraud`, plus login/OTP/phishing from `retail` | Superseded: urgent card cases get fixed steps and an urgent RM request (Relationship, by rules, no model); login, OTP and phishing go to Guide |
| **Compliance & Documents** | `compliance` + `document` | Document only read the compliance workspace; merging loses no protection |
| **Relationship & Service** | `relationship`, plus request/ticket status from `retail` | Everything about a person at the bank |
| **RM Client** (`rm_client`; was RM Client Insight) | part of the RM assistant ([workflow 2](workflows/workflow-2-relationship-manager.md)) | Sensitive per-client reads, including what a client shared under consent; read-only apart from the *Ask the client to share* card (0113) |
| **RM Requests** (`rm_requests`; was RM Service Desk) | part of the RM assistant | Tickets, follow-ups and drafts in the RM's book |
| **RM Calendar** (`rm_calendar`, build plan 3a) | the schedule part of RM Service Desk | Appointments, availability and clients' meeting requests |

**Client agents.** Row scope is the signed-in client only (caller JWT + RLS + explicit customer id checked in code); Guide sees no customer rows.

| Agent | Fields it may see | Tools (bounded) | External | Offers (confirm-first) |
|---|---|---|---|---|
| **Guide** | Approved knowledge only | `knowledge_search`, `request_categories` | `public_holidays`, `fx_rate` | None; personal questions get "sign in to check" |
| **Accounts & Cards** | Accounts (type, balance, currency); cards (last 4, product, limit, balance, due date, fee, status); loans; transactions ≤90 days; statements | `my_accounts`, `my_cards`, `my_loans`, `my_transactions` (window ≤90 days), `my_statements`, `knowledge_search` | `fx_rate`, `public_holidays` | Card-limit request, statement copy request (`ticket.create`) |
| ~~**Security**~~ (removed in build plan 2b) | Cards: `card_id`, `last4`, `status` only; transactions ≤90 days; travel flags. No balances, profile or loans | `my_cards` (security view), `my_transactions`, `travel_flags`, `knowledge_search` (security articles) | `public_holidays` | Fraud report (P0 handoff), lost-card report, travel notice, replacement card. Never freeze, unfreeze, dispute or cancel |
| **Compliance & Documents** | Review case, checklist, bank questions, upload metadata and check results (counts, no file contents); only the profile fields a form needs (tax-form pre-fill) | `my_compliance_case`, `my_document_checks`, `document_templates`, `my_profile` (form fields), `knowledge_search` | — | Checklist mark, answer a bank request, submit a document, compliance handoff. Never a bank decision |
| **Relationship & Service** | RM name and phone (no email or ids), appointments, free slots ≤14 days, consent records, own tickets (status and client-safe text, no internal notes), request categories, her Data sharing cards (`consent.sharing`) and her talking points (`insights.read`) | `my_rm`, `my_appointments`, `free_slots`, `my_consents`, `my_tickets`, `request_categories`, `knowledge_search` | `public_holidays` | Appointment hold/confirm/cancel, RM handover, ticket follow-up, consent grant/revoke, share summary with the RM (active consent required), let her RM know about a topic (`insight.share`, added by a compose rule from the topic in her own words, never by the model; Sophie sees list values only), consent grant with a scope and an end, revoke, and topic deletion (`insight.delete`), offered by rules from her own words (`consent_answers`), never by the model; on a meeting request, optional share-until-answered switches |

**Build plan 2b (5 Nov 2026): no Security agent.** A charge she doesn't recognise or a lost card gets fixed steps from code and one urgent request to her RM (a P0 `general_support` handoff that 0082 turns into an urgent `rm_handover` ticket at the top of the RM's inbox). Login, OTP and phishing questions are Guide's (`login_help`, `phishing`).

**RM agents.** Row scope is the RM's own book (`rm.in_book`); a client outside the book gets an audited refusal that reads the same whether or not the client exists.

| Agent | Fields it may see | Tools | Offers (RM approval required) |
|---|---|---|---|
| **RM Client** | 360 profile, products, review status, shared notes (previous RMs' private notes withheld), book facts; under the client's consent only: transaction insights, talking points, cash events ≥10,000 over 180 days, the shared summary, handover sources after a recorded reassignment | `rm_client_profile`, `rm_meeting_notes`, `rm_review_status`, `rm_book_facts`; consent-gated fixed reads `rm_transaction_insights`, `rm_talking_points`, `rm_cash_events`, `rm_shared_summary`, `rm_handover_sources` | Proposes *Ask the client to share* on a consent gap (one card per missing category, a fixed reason); Sophie approves it through `POST /rm/consent-requests`. Everything else is read-only (overview, meeting history, briefs, reviews, book questions) |
| **RM Requests** | Tickets in the book: client-safe status and text; specialist-restricted detail withheld; ticket messages are untrusted data. Never consent-gated data | `rm_tickets`, `rm_ticket`, `rm_followups`, `request_categories`, `knowledge_article` (the approved article a ticket's request type names, read by id); borrowed fixed reads of the profile, notes, availability and appointments for drafts; the ranking from `services/rm/priority.py` over its own tickets and follow-ups plus the Calendar and Client exports (3b: `today` answers her day in rank order) | Draft reply (sent only on Approve), tickets and tasks from notes, prepared requests (duplicate and missing-field checks), status change on her own tickets only |
| **RM Calendar** | Her appointments, availability and working hours, clients' meeting requests (purposes are the client's words). Never client financial data | `rm_appointments`, `rm_availability`, `rm_meeting_requests`; exports `meetings_next_7d` and `requests_to_answer` (ids, dates and codes, never the client's purpose) | Accept a requested time, propose another, decline with a reason; block time, open it again, change weekly hours (cards built by code, approved one by one) |

No RM agent decides approvals or gives investment advice, and none changes specialist-managed fraud tickets.

**Since build plan 3a** each RM data tool has one owner (`registry.RM_OWNER`); Requests holds the profile, notes, availability and appointments only as fixed reads (`BORROWED`). Consent-gated reads (`CONSENT_GATED`) are fixed reads of RM Client only, with `purpose` set by the intent, so no draft carries transaction content. Every client and RM turn traces ids, codes and counts only:
- **planner:** side, plan source, the plan, and dropped links with a reason code;
- **agent:** path, loop stop and limit, reads, refusals, cards, export names;
- **each loop step:** tools, safe arguments, refusals, reads left;
- **each tool call:** allowed or the refusal reason, rows, and for a gated read whether it was shared, under which category, grant and purpose.

The request summary (`observability.request_facts`) adds side, role, tool calls, loop stops, refusals, refused tools and consent gaps. The admin dashboard filters Agents and Failures by side (`client` or `rm`), lists loops that reached their read limit as failures, and counts tool refusals and consent gaps as checks working.

**Rules for every agent**
1. **One declared spec per agent** (`AgentSpec` in `agents/registry.py`): row scope (`public`, `own_customer`, `rm_book`), tool names with the field view each returns, per-call limits (date window, row count), external hosts and offer types. Policy (`authorization.decide`) and the tool layer both read it, so permission and tool names cannot drift apart.
2. **Fields are projected in the tool layer**, not in the agent: `my_cards` returns the security view to Security and the full view to Accounts & Cards. The model never receives more than the spec allows, including inside the tool loop.
3. **No data is shared between agents.** Each agent's evidence stays its own; cross-agent context is a flag only (for example `linked: card_fraud`).
4. **Agents never write.** They return offers; `act` carries them out on the next turn after Confirm or "yes", with the offer id as the idempotency key.
5. **External calls send codes only** (country, currency, year), never names, card digits or amounts. Hosts are allow-listed; a failed call is reported in that section, not replaced.
6. **No identifiers reach the model.** Cards appear as last 4 digits and tickets as `TCK-` references; NRIC, emails and internal UUIDs are dropped.
7. **Fixed safety replies stay outside the agents.** Lost card, pasted secrets and "talk to a person" remain no-model guardrail routes (§5.4).

**Use-case coverage**

| Agent | QA1 and backlog use cases |
|---|---|
| Guide | General question while logged out; loan enquiry; investment advice (refused, RM offered) |
| Accounts & Cards | Card limit increase; pressure to approve; statement then the other account; two cards and a fee waiver (waiver handed to Relationship & Service) |
| Security (since 2b: Guide and the urgent card rules) | Lost card; model unavailable during a fraud message; wrong and forgotten password; password, OTP and NRIC in chat (guardrail first); travel readiness |
| Compliance & Documents | Compliance checklist; uploads with an address mismatch; tax-form pre-fill; unreadable or wrong-format upload; review status with mixed results |
| Relationship & Service | Book a meeting; ticket status with internal notes hidden; RM matter and fraud in one message (with Security in parallel); claiming to be her assistant (refused, no data) |
| RM Client Insight | Pre-meeting brief with and without consent; client outside the book; 360 overview then switch client; inherited client; transition message |
| RM Service Desk | Ticket inbox triage; draft and approve a reply; meeting notes into tickets; summarise a ticket without changing it; specialist-managed fraud ticket; prepare requests; instructions hidden in a ticket |

### 5.7 Shared Services

#### 5.7.1 Knowledge Service
- **Category:** Keyword retrieval with citations, plus an exact fact table.
- **Responsibility & boundaries:** Returns ranked article sections with citations and exact fees/limits/rates. Does not write answers. Unrelated questions return nothing. External-publisher articles need `reference_url`, `checked_on`, `review_due` and an allowed domain. Full design and the content each story needs are in the [knowledge base appendix](#appendix-knowledge-base).

| Part | Built as |
|---|---|
| Articles / sections | `kb.articles`, `kb.sections` (versioned, cited as `article_id@vN#anchor`), migration `0008`; approved seed `0009`; Workflow 1 content `0055` |
| Keyword search | Ranked keyword RPCs, called with the caller's JWT and audited (`services/kb.py`); since `0097` a section matches only when a question word is in its heading or keywords. Retrieval quality: `backend/evaluation/retrieval.py` ([results](evaluation/README.md#knowledge-retrieval-offline-no-model)) |
| Hybrid search (default; keyword fallback) | Migration `0099`: pgvector, `kb.section_embeddings` (one vector per section and model, written by the owner with `scripts/embed_knowledge.py`), `kb_hybrid_search` fuses the keyword ranking with embedding similarity (reciprocal rank fusion); a section found only by similarity must reach `EMBEDDING_MIN_SIMILARITY` (0.4). The question is embedded by `services/embeddings.py` with its own key (`EMBEDDING_API_KEY`, never `LLM_API_KEY`). Without a key, or if the embedding call fails, keyword search runs and the audit event records `mode: keyword_only` ([results](evaluation/README.md#3-nov-2026-hybrid-search-migration-0099)) |
| Exact source lookup | Migration `0011`; `GET /knowledge/sections/{article}/{version}/{anchor}` |
| One article by id | `kb_get_article` (`0102`): its published, in-review sections in anchor order, for RM ticket guidance (the Service Desk's `knowledge_article` tool, permission `kb.search`). `kb.facts` was dropped in `0102`; figures are checked against section text |
| Read-only access | RLS on `kb` tables; content changes only through migrations |

- **Inputs / outputs:** `search(KnowledgeQuery) → [KbChunk]`; `get_fact`; `GET /knowledge/{id}`.
- **Infrastructure / pattern:** In-memory Python or hosted Postgres; pgvector inside the same Postgres for hybrid search, no separate vector database.
- **Dependencies:** ↑ Orchestrator (`database_evidence`), agents, v1 answer flow. ↓ Operational Database.

#### 5.7.2 Customer Data Service
- **Category:** Entitlement-scoped data access layer.
- **Responsibility & boundaries:** The only way agents and record pages read customer records. Read-only; every write goes through the Action Service.

| Part | Built as |
|---|---|
| Read functions | Profile, accounts, transactions (filtered), credit cards, loans, compliance case, documents metadata, RM assignment/introduction, requests, appointments, slots, meeting options, agendas, consent records (`customer_data.py`, `agents/*_records.py`, `api/customer.py`, `api/relationship.py`) |
| Scope enforcement | `customer_id` comes from the JWT; every query runs with the caller's JWT, invoker RLS **and** an explicit SQL customer predicate (migrations `0028`/`0029`) |
| Field masking | Card numbers → last 4; emails, ids and internal codes excluded from model evidence |
| Read audit + fingerprint | Each read is one SQL call that also writes the `data.read` audit event and returns `{call_id, data, as_of}` |

- **Inputs / outputs:** In: read request + `RequestContext`. Out: `ToolResult {call_id, data, as_of}`.
- **Infrastructure / pattern:** Supabase Data API with the user's JWT (`core/data_api.py`: bounded transient retries). No cache, no read model.
- **Dependencies:** ↑ Agents, record routes. ↓ Authorisation, Operational Database, Audit Log Service.

#### 5.7.3 Action Service
- **Category:** Command processing with confirmation and idempotency.
- **Responsibility & boundaries:** Every state change. `run_action(command, ctx, confirmed)` checks permission, builds the canonical target from the typed executor contract, and calls one SQL function; agents may propose but `confirmed=True` with an agent set is denied. The SQL side checks JWT ownership, confirmation and expiry independently and runs the executor registered in the private `actions.executors` table. Command state, business change, receipt, audit event and outbox row commit in **one transaction** (migrations `0017`–`0019`). Confirmation always reloads the stored target; the client's copy is never used.

| Step | Built as |
|---|---|
| 1 Duplicate check | Unique `idempotency_key`; repeats return the same command/receipt |
| 2 Expiry | Database sets `expires_at` (5 minutes for proposals; 10 minutes for slot holds); confirming after expiry executes nothing |
| 3 Authorise | `require_permission` in Python + ownership checks in SQL; re-checked at confirm time |
| 4 Confirm | *Confirm* types stop as `proposed`; *auto* types (`authorization.AUTO_TYPES`) run immediately |
| 5–6 Execute + record | One SQL transaction: executor, ledger, receipt, audit, outbox |
| 7 Receipt | `Receipt {receipt_id, command_id, outcome, audit_event_id}` |

**Executors** (Python files in `services/actions/` define the typed targets; SQL executors do the writes)

| File | Action types | Confirm? | Business rules |
|---|---|---|---|
| `cards.py` | `card.temporary_block` | confirm | `active → temporarily_blocked`; demo `banking.credit_cards` |
| `handoffs.py` | `handoff.create` | auto | `support.handoffs`, one open handoff per case and reason, priority + redacted label-only summary |
| `appointments.py` | `appointment.hold` | auto | 10-minute hold; GiST exclusion prevents overlaps; working hours and current assignment checked |
| | `appointment.confirm`, `appointment.cancel` | confirm | Booking only while the hold is live; within 366 days |
| `records.py` | `consent.request`, `agenda.save`, `compliance.update_checklist_item`, `document.register`, `document.submit` | auto | Redacted agenda topics with optimistic revision; checklist marks under case/item locks; document registration after upload; submission moves a precheck only to awaiting review |
| | `consent.grant`, `consent.revoke`, `rm.share_summary`, `requests.create` | confirm | Grants never share data; sharing copies one saved revision under one exact grant and rechecks expiry and recipient; requests derive recipient names server-side |
| `staff.py` | `handoff.claim`, `handoff.close`, `card.unblock`, `card.cancel_permanently` | staff only | Requires `demobank_role=support`, `aal2` and a claimed handoff; `temporarily_blocked → active / cancelled` only |

`POST /commands` lets the Web Client propose a confirm-type action (e.g. booking); `POST /actions/cases` and `POST /relationship/holds` create an owned action case without a fabricated chat turn.

- **Inputs / outputs:** In: `ActionCommand` + `RequestContext`. Out: `Receipt` or `ProposedCommand {command_id, expires_at}`; `ActionOutcome` over HTTP.
- **Infrastructure / pattern:** Command pattern; executor registry in SQL; one Postgres transaction per command.
- **Dependencies:** ↑ Orchestrator, record/form routes, staff script. ↓ Authorisation, Operational Database, Audit Log Service.

#### 5.7.4 Authorisation & Consent Service
- **Category:** Attribute-based authorisation + consent check.
- **Responsibility & boundaries:** `decide(subject, action, resource, context)` is pure; `require_permission` audits each decision (`policy.decision`) and raises on deny. Unknown tools, scope mismatches and staff operations from customer/agent paths are denied. `has_consent` checks exact purpose/recipient/category coverage, revocation and wall-clock expiry under JWT + RLS. Grants and revocations are written only by confirmed actions.

| Part | Built as |
|---|---|
| Policy rules | Python, versioned in git: guests get public tools only; each agent has an allow-list; `AUTO_TYPES`, `CONFIRM_TYPES`, `STAFF_TYPES` |
| Decision | `Decision {allow, reasons[], obligations[]}` |
| Consent | `consent.grants`, `consent.requests` (migration `0010`, `0037`–`0040`); `POST /consent/check`, `GET /consent/rm`, `GET /consent/records` |

- **Dependencies:** ↑ Customer Data, Action Service, Knowledge, Orchestrator. ↓ Operational Database, Audit Log Service.

#### 5.7.5 Notification Service
- **Category:** In-app notification delivery.
- **Responsibility & boundaries:** Turns outbox events into inbox items from versioned templates, applies preferences, retries or dead-letters failures. Never writes free-text advice. Only English templates exist; a missing locale fails explicitly.

| Part | Built as |
|---|---|
| Delivery | Transactional delivery + retry + audit in SQL (migration `0023`), called by the worker |
| Templates | Versioned, with typed variables (`notify.templates`, `0026`) |
| Preferences | `notify.preferences` (`0027`); service messages cannot be switched off |
| Inbox | `notify.inbox`; `GET /notifications` (latest 50), `POST /notifications/{id}/read`; Supabase Realtime publication for live updates |

- **Dependencies:** ↑ Background Worker. ↓ Operational Database, Web Client (Realtime), Audit Log Service.

### 5.8 Response Validator
- **Category:** Post-generation grounding checks.
- **Responsibility & boundaries:** Runs **per section, inside each agent** (`agents/answer.validate_draft`, `source_checks.py`): JSON schema valid; source ids exist in that section's evidence and match scope; banking answers cite sources; figures and units appear in the evidence (`unit_matches`); no secrets, URLs or prompt leakage; no claimed actions. A failed section gets `status=error`, origin `validation` and a withheld message; `escalate_validation` then records a handoff for signed-in saved cases. Action outcomes are never model text: decision sections use fixed wording and cite the ledger receipt. These checks do not prove factual correctness.
- **Inputs / outputs:** In: model draft + evidence. Out: `AgentResult` (validated or withheld); `source_check: basic_checks_passed|rejected` and `response_origin: ai|safety|validation` on the reply.
- **Dependencies:** ↑ Agents. ↓ Knowledge (facts), Action Service (`handoff.create` via the orchestrator), Audit Log Service.

### 5.9 Conversation Store
- **Category:** Durable conversation and workflow state.
- **Responsibility & boundaries:** Redacted messages and saved turns, guest access, cases, LangGraph checkpoints and workflow rows. Customers read their own history under RLS; guest capabilities expire after 24 hours. The guarded question is recorded before any provider work; failures are stored explicitly; turns can be cancelled.
- **Inputs / outputs:** `conversation.sessions`, `messages`, `turns`, `cases`, `guest_access` (migrations `0013`/`0014`); `conversation.checkpoints`, `checkpoint_writes` (`0015`/`0016`); `conversation.agent_workflows`, `actions.workflow_commands` (`0020`–`0022`). Routes: `/conversations`, `/conversations/{id}`, `/conversations/{id}/turns/{turn}/cancel`.
- **Infrastructure / pattern:** Postgres via the Data API; HMAC-signed checkpoint envelopes (`CHECKPOINT_SIGNING_KEY`).
- **Dependencies:** ↑ `/chat`, Orchestrator, action confirm/resume. ↓ Operational Database.

### 5.10 Background Worker
- **Category:** Transactional-outbox poller + reminder scheduler.
- **Responsibility & boundaries:** Polls `events.outbox` (`FOR UPDATE SKIP LOCKED`) and delivers each event through the Notification Service; each call commits delivery, event outcome and audit together. A scheduler inside the worker calls the idempotent recorded-deadline producer every minute: one compliance reminder per case/due date/day, from today to seven days ahead (Asia/Singapore), only while checklist items remain outstanding. **It does not start orchestrator turns**, and pg_cron is not installed.
- **Inputs / outputs:** In: outbox rows (ids only). Out: inbox rows; event marked delivered, retried or dead-lettered.
- **Infrastructure / pattern:** Python loop (`app/worker.py`, `core/lifecycle.py`); dedicated LOGIN role that is a member of `demobank_worker`, checked at start-up for excess privileges. Never the postgres owner, service-role key or an app JWT. Unknown SQL failures stop the worker and show in health.
- **Dependencies:** ↑ Action Service (outbox). ↓ Notification Service, Operational Database, Audit Log Service.

### 5.11 LLM Gateway
- **Category:** Model abstraction + prompt registry.
- **Responsibility & boundaries:** `llm_gateway.call` is the one client for planner and agents: loads versioned prompts (`prompts.load(agent, version)`), enforces strict JSON, timeout per attempt, retries on timeouts/429/5xx only, per-request token budget, `llm` span + `llm_call` log and `agent.llm_call` audit event. Raises `LLMError`; failures are reported, never replaced with sample answers. `reasoning=False` asks OpenRouter to turn the model's hidden reasoning off for one call: those tokens count against `MAX_TOKENS` and vary per call (1,300–2,700 on the RM `today` answer, which truncated about half the time). The CLI transports cannot turn thinking off and other providers keep their default, so the span records `reasoning` as `off` only where it took effect. The RM assistant sets it per intent in one code table (`rm/handlers.REASONING`), off for every RM answer: with reasoning on, 7 and 9 of 11 QA1 RM cases hit the limit in two runs and 3 of 5 call notes (R13) did; off, all passed in about a third of the time. An intent turns it back on only with evaluation evidence and output headroom. The RM planner and the open loop's steps keep the default (short outputs).
- **Inputs / outputs:** In: `{prompt, system, history, schema}`. Out: parsed object + `{model, tokens, latency}`.
- **Infrastructure / pattern:** LangChain → OpenRouter (`CHAT_TRANSPORT=api`); local Codex CLI (`CHAT_TRANSPORT=codex`, `services/codex_cli.py`) with no app keys and no coding tools. Focused real-provider checks use `dots-studio/dots-3-note-preview:free`.
- **Dependencies:** ↑ Orchestrator, Agents, v1 answer flow. ↓ OpenRouter, Tracing.

### 5.12 Tracing & Evaluation
- **Category:** Tracing, structured logs, evaluation harness.
- **Responsibility & boundaries:** One LangSmith root run per request with nested runs per node/agent/tool/LLM call (redacted before upload), JSON request logs with ids and counts only, offline and live evaluation sets. Not the compliance record — that is the Audit Log Service.
- **Infrastructure / pattern:** LangSmith (`core/observability.py`, `core/langsmith_client.py`), `backend/evaluation/` (`run.py`, `--live --suite focused`, `langsmith_sync.py`). `ops.traces`/`ops.spans` (`0001`) are retired history; `0074` drops `log_trace`. Each root run carries a per-request summary in its metadata (`request_facts`: agents and their worst status, model calls, tokens, prompt ids; plus the route's time to first section and offers) so the admin dashboard (`api/admin.py`, admin role only) reads one run per request; `services/quality.py` does the bounded LangSmith reads and `services/insights.py` the arithmetic (overview, failures and debug cards, evaluation timelines and comparisons, agent scorecards, prompt versions, latency). Read-only; it never writes to LangSmith.
- **Dependencies:** ↑ all backend modules. ↓ LangSmith.

### 5.13 Agent tools

In the build a "tool" is a permission name checked by `require_permission`, not a function the model can call. The agent code calls the function directly with `ctx.agent` set.

| Tool | Backend function / route | Agents (allow-list) |
|---|---|---|
| `kb.search` | `kb.search`, `kb.get_article`, `kb.get_section` | Complex Banking Needs, Compliance Review, Relationship; public for everyone |
| `customer.get_profile` | `customer_data` | Complex Banking Needs, Relationship |
| `customer.get_accounts`, `customer.get_loans` | `customer_data` | Complex Banking Needs |
| `customer.get_transactions`, `customer.get_credit_cards` | `customer_data`, `fraud_records` | Complex Banking Needs, Fraud & Security |
| `compliance.get_case`, `compliance.workspace` | `customer_data`, `prechecks.workspace` | Compliance Review (+ Document Check for workspace) |
| `compliance.update_checklist_item` | Action (auto) | Compliance Review allow-list; used from Records |
| `docs.precheck` | `prechecks.py` | Document Check |
| `rm.get_assignment`, `rm.get_introduction`, `requests.list`, `appointments.find_slots`, `appointments.list`, `consent.check`, `consent.records` | `relationship_records`, `consent` | Relationship |
| `agenda.save`, `consent.request` | Action (auto) | Relationship allow-list; used from forms |
| `actions.propose` | Action (confirm) | Fraud & Security (`card.temporary_block`); Relationship allow-list, not used from chat |
| `handoff.create` | Action (auto) | All personal agents; Orchestrator; "Talk to a person" in the UI |

---

### 5.14 Data

#### Operational Database
- **Category:** PostgreSQL with Row-Level Security, one schema per area.
- **Responsibility & boundaries:** System of record: `banking`, `compliance`, `rm`, `consent`, `conversation`, `actions`, `support`, `notify`, `events`, `kb`, `audit`, `ops`. RLS on every customer table plus `customer_id` predicates in code and SQL. `events` is not exposed through the Data API. Migrations are numbered (`0001`–`0063`), never edited after release.
- **Infrastructure / pattern:** Hosted Supabase Postgres; GiST exclusion for bookings; keyword search for knowledge.
- **Dependencies:** ↑ all services, Identity Provider, Background Worker.

#### Document Storage
- **Category:** Private object storage.
- **Responsibility & boundaries:** Bucket `demobank-compliance-private`, objects at `customer_id/case_id/document_id`. PDF, DOCX, JPEG and PNG up to 10 MiB. Flow: audited preparation (`POST /documents/uploads/prepare`) → browser uploads with the user's JWT → automatic `document.register` action (receipt, audit and outbox together). No customer overwrite or delete; owned registered objects get 60-second download links.
- **Infrastructure / pattern:** Supabase Storage with storage RLS (migrations `0051`–`0052`).
- **Dependencies:** ↑ Web Client, Document Check (inspection reads).

---

## 6. Audit Log Service

### 6.1 Purpose and boundary
A tamper-evident, append-only record of **who did what, to which resource, when, and with what outcome** — for every user, agent, system and staff action. It answers "what happened in this case?" without exposing message text or customer records. Traces (§5.12) answer "why was it slow / wrong?".

### 6.2 Five-field summary
- **Category:** Append-only audit trail.
- **Responsibility & boundaries:** Accepts events from every module and from database triggers; stores them immutably. Does not store raw chat text or full customer records.
- **Inputs / outputs:** In: `AuditEvent` batches (`public.audit_record_batch`) and in-transaction SQL calls. Out: chain verification (`scripts/verify_audit.py`).
- **Infrastructure / pattern:** `audit.events`; insert only through `audit.record(...)` (`SECURITY DEFINER`); `UPDATE`, `DELETE`, `TRUNCATE` revoked; each row stores `prev_hash` and `hash` (migration `0005`). Row triggers on audited tables (`0006`). Canonical UUID tokens allowed in details (`0054`).
- **Dependencies:** ↑ every backend module, SQL executors, worker, triggers. ↓ Operational Database.

### 6.3 AuditEvent schema

As sent by the API (`schemas/contracts.AuditEvent`); `event_id`, `occurred_at`, customer, hashes and (unless an agent is named) the actor are filled in by the database from the caller's JWT.

```json
{ "category": "auth|chat|agent|data|policy|action|row|consent|notification|handoff|document|admin",
  "action": "chat.message_received", "outcome": "success|denied|failed",
  "resource_type": "message", "resource_id": "uuid",
  "details": {"history_len": 4},
  "case_id": "uuid|null", "session_id": "uuid", "request_id": "uuid",
  "actor_type": "agent|null", "actor_id": "fraud|null" }
```

`details` holds ids, enums, counts and changed field names only (`audit.is_safe`).

### 6.4 What is recorded

| Category | Events (examples) | Captured by |
|---|---|---|
| auth | `auth.session_started`, `auth.session_ended` | Gateway, `/auth/session-end` (migration `0007`) |
| chat | `chat.message_received`, `chat.reply_sent`, `chat.workflow_review_ready` | `/chat` |
| agent | `agent.llm_call` (model, prompt version, tokens, ms), `agent.failed` | `/chat` from LLM spans, Orchestrator |
| data | `data.read` (tool, counts) | Customer Data and Knowledge SQL reads |
| policy | `policy.decision` (tool, reasons, policy version) | `require_permission` |
| action | proposed, confirmed, rejected, executed, failed, expired | SQL action functions, same transaction |
| row | insert / update / delete on audited tables | Generic row trigger |
| consent, handoff, document, notification | grants/revocations, handoff created/claimed/closed, upload/register/submit, delivery/read | Respective SQL functions |

### 6.5 Write rules
- **State changes:** audit row in the same transaction as the change.
- **Everything else:** events collected per request and written in one batch before the `answer` (or `workflow_paused`) SSE event.
- **Audit write failure:** the request fails (`audit_unavailable`) rather than continuing unaudited.
- **Retention:** keep all rows for the demo.

---

## 7. End-to-end execution flow

### 7.1 Representative scenario
Workflow 1, the client (Michelle, customer 1002), detailed in [workflows/workflow-1-client.md](workflows/workflow-1-client.md). Configuration: `PIPELINE=v2`, `CONVERSATION_STORE=database`, `PRECHECK_MODE=template`, signed in (`personal` mode). Two parts of one visit:

**Part A – upload a requested document.** On the Compliance review page Michelle uploads her utility bill against *Proof of residential address*. It is checked against the proof-of-address template and, because nothing is missing, sent to the bank.

**Part B – one chat message with two questions.**

> "When is my review due? Also, should I invest my bonus in the new bond fund?"

| Task | Priority | Handled by |
|---|---|---|
| t1 Review deadline | **P2** (compliance deadline) | Compliance Review agent |
| t2 Investment question | **P3**, marked `handover` | No agent: fixed polite reply, handed to her relationship manager |

### 7.2 Step table
S = synchronous, A = asynchronous.

**Part A – upload, check and submit (no model call)**

| # | Caller → callee | Mode | Result |
|---|---|---|---|
| 1 | Web → `POST /documents/uploads/prepare` | S | Gateway verifies the JWT; SQL checks the case and item belong to Michelle and returns the private Storage path |
| 2 | Web → Supabase Storage | S | File uploaded with Michelle's own JWT (Storage RLS; no service key) |
| 3 | Web → Action Service `document.register` | S | Automatic action: document row, receipt, audit and outbox in one transaction |
| 4 | Web → `POST /documents/{id}/precheck` | S | `prechecks.run` reads the item's notice and policy, downloads the bytes and parses them in a separate child process (OCR only for images) |
| 5 | Document templates | S | `template_for` picks *proof of address*; `check` looks for her name, a Singapore postal code, a known issuer and a date within 3 months → no findings |
| 6 | Backend → `document_precheck_record` | S | Report signed with `DOCUMENT_PRECHECK_SIGNING_KEY`; SQL verifies the signature, scope and notice hash before saving |
| 7 | Web → `POST /documents/{id}/submit` | S | Only a fresh `no_issues` report is accepted; `document.submit` sets the item to *submitted – awaiting review* with a receipt |
| 8 | Web | S | Page reloads its record and shows "Sent to the bank on … – waiting for review". Nothing is sent to the chat |
| 9 | Background Worker → Notification Service | A | Outbox event → Inbox message |

**Part B – chat with a handover**

| # | Caller → callee | Mode | Result |
|---|---|---|---|
| 10 | Web → Gateway `POST /chat` | S, SSE opened | Message, `conversation_id`, JWT; `RequestContext` for customer 1002 |
| 11 | `/chat` → Conversation Store + Guardrail | S | `assess_request` redacts and checks the text (no fixed safety reply); the redacted question is saved before any model work |
| 12 | *plan* → LLM Gateway | S | Two questions in one message → one planner call → two issues, re-checked by routing rules |
| 13 | *prioritise* | S | t1 compliance P2 (deadline wording); t2 matches the handover rules (`rm_handover.needs_person`) and is marked `handover` |
| 14 | *dispatch* | S, parallel | t1 and t2 run at the same time, each bounded by `AGENT_TIMEOUT_S` |
| 14a | Compliance agent → Customer Data | S | Reads Michelle's review case and requested items (JWT + RLS); `data.read` audited |
| 14b | Compliance agent → LLM Gateway | S | One call with prompt `compliance/v3`: short plain answer citing the case ("due 30 Nov 2026") |
| 14c | Response Validator | S | Sources exist and every figure and date matches the record → section released |
| 14d | t2 | S | No model call: fixed reply "That's something I'm not able to help with here…" |
| 15 | *dispatch* → *act* | S | A `handover` task, so the action nodes for proposals are skipped and nothing pauses |
| 16 | *act* → Action Service | S | `handoff.create` (`general_support`, summary = her redacted question); receipt, audit and outbox in one transaction |
| 17 | Database trigger | S | Opens an `rm_handover` ticket owned by *Your relationship manager* (migration 0073) |
| 18 | *act* → Customer Data | S | Reads the new ticket's `TCK-` reference and adds "I've passed your question to your relationship manager. Your reference is TCK-…" |
| 19 | *compose* → Web | S stream | Two sections in priority order; audit batch flushed; SSE `answer`, `done`; reply saved on the turn |
| 20 | Background Worker → Notification Service | A | Ticket notice → Inbox |

**Failure branches**
- *The check finds a problem* (old bill, no postal code) → `issues_found`; the document is not submitted; the page lists what is missing and offers *Explain in chat*.
- *No readable text* (a photo without OCR) → "upload the PDF from the provider"; no pass or fail.
- *Signing key missing* → 503 "Document checks are not switched on yet"; the upload stays saved.
- *Planner call fails* → the request fails with an explicit error (no guessed plan).
- *The compliance agent fails, times out or its answer fails validation* → that section is withheld and handed to her relationship manager the same way as step 16.
- *A guest asks the investment question* → fixed reply asking them to sign in; no handoff (guests have no case).
- *"I lost my card"* → fixed lost-card reply from the Guardrail; the orchestrator does not run.

### 7.3 Sequence diagram — upload, check and submit
`->>` synchronous call, `-->>` return, `-)` asynchronous.

```mermaid
sequenceDiagram
  autonumber
  actor M as Michelle
  participant WEB as Web Client
  participant GW as API Gateway
  participant ST as Supabase Storage
  participant ACT as Action Service
  participant PC as Pre-check (templates)
  participant DB as Operational Database
  participant WK as Background Worker

  M->>WEB: Choose file for Proof of residential address
  WEB->>GW: POST /documents/uploads/prepare
  GW->>DB: Check owned case and item
  GW-->>WEB: Private path
  WEB->>ST: Upload with Michelle's JWT
  WEB->>ACT: document.register
  ACT->>DB: Document row, receipt, audit, outbox
  WEB->>GW: POST /documents/{id}/precheck
  GW->>PC: Parse file, pick template, check fields
  PC->>DB: Signed report (signature verified in SQL)
  GW-->>WEB: no_issues
  WEB->>GW: POST /documents/{id}/submit
  GW->>ACT: document.submit
  ACT->>DB: Item submitted, receipt, audit, outbox
  GW-->>WEB: Receipt
  WEB-->>M: Sent to the bank on 29 Oct 2026 – waiting for review
  WK-)DB: Poll outbox
  WK-)WEB: Inbox message
```

### 7.4 Sequence diagram — chat turn with a handover to the RM

```mermaid
sequenceDiagram
  autonumber
  actor M as Michelle
  participant WEB as Web Client
  participant GW as API Gateway
  participant GR as Guardrail
  participant ORC as Orchestrator
  participant LLM as LLM Gateway
  participant CA as Compliance agent
  participant CD as Customer Data
  participant ACT as Action Service
  participant DB as Operational Database

  M->>WEB: When is my review due? Also, should I invest my bonus?
  WEB->>GW: POST /chat (SSE)
  GW->>GR: assess_request (redact, no fixed reply)
  GW->>ORC: Owned turn
  ORC->>LLM: plan (two questions)
  LLM-->>ORC: t1 compliance, t2 investment
  ORC->>ORC: prioritise: t1 P2, t2 handover
  par t1
    ORC->>CA: dispatch t1
    CA->>CD: Read review case
    CA->>LLM: Answer (compliance/v3)
    CA-->>ORC: Validated section
  and t2
    ORC->>ORC: Fixed "not able to help here" reply
  end
  ORC->>ACT: handoff.create general_support (her question)
  ACT->>DB: Handoff, receipt, audit, outbox
  DB->>DB: Trigger opens rm_handover ticket
  ORC->>CD: Read ticket reference
  ORC->>ORC: compose two sections
  GW-->>WEB: answer, done
  WEB-->>M: Due 30 Nov 2026 · passed to your RM, reference TCK-…
```

### 7.5 Sequence diagram — the relationship manager answers
The RM view (Workflow 2) is not built yet; today the RM's answer is recorded with one SQL call by the owner role.

```mermaid
sequenceDiagram
  autonumber
  actor R as Relationship manager
  participant DB as Operational Database
  participant WK as Background Worker
  participant WEB as Web Client
  actor M as Michelle

  R->>DB: support.set_ticket_status('TCK-…','resolved','Your RM called you.')
  DB->>DB: Ticket event, audit, outbox
  WK->>DB: Poll outbox
  WK-)WEB: Inbox message
  M->>WEB: Open My requests
  WEB-->>M: Resolved, with the RM's note
```

## 8. Latency budget

Still **estimates, not measurements**; latency and report checks remain outstanding (README). Assumes backend on a laptop, hosted Supabase in Singapore.

| Step | Hosted Supabase | Notes |
|---|---|---|
| Gateway (cached JWKS, rate limit) | 1–5 ms | |
| Guardrail | < 5 ms | Pure Python |
| Conversation prepare + checkpoints | 100–400 ms | One Data API call per node with synchronous durability |
| Plan | 0 ms (rules) / 0.5–1.5 s (LLM) | LLM only when no rule fits |
| Reads per agent | 80–300 ms | Agents run concurrently |
| Agent LLM call | 1.5–4 s | One per task; concurrent tasks cost the slowest |
| Action proposal + audit batch | 50–150 ms | |

| Request type | Expected total |
|---|---|
| Fixed safety reply | 50–200 ms |
| Single-intent question | 2–5 s |
| Multi-intent | 3–7 s |
| Free OpenRouter models | add 2–15 s, plus rate limits and occasional failures |
| Codex CLI transport | add several seconds per call |

At most two **sequential** model calls per turn (planner, then the slowest agent). Final text is released after validation, so token streaming is not used.

---

## 9. Local development and deployment

**As built:** processes are started directly (Docker Compose is deferred).

| Process | Command |
|---|---|
| `web` | `npm run dev` in `frontend/` (Node 22) |
| `api` | `uvicorn app.main:app` in `backend/` (Python 3.12, `.venv`); add `PIPELINE=v2` for the multi-agent flow |
| `worker` | `python -m app.worker` with `WORKER_DATABASE_URL`, or `RUN_WORKER_IN_API=true` |

Supabase: the **hosted project** (decision 4). Migrations are applied in prepared batches (`scripts/prepare_hosted_migrations.py`); README records which migrations are active there. Native tests run unchanged migrations against temporary PostgreSQL and a pinned PostgREST. Secrets come from a git-ignored `.env`.

Free hosting of the frontend and API (Vercel/Netlify, Render/Hugging Face) remains a later option; it is not set up.

---

## 10. Differences from the v3.1 proposal

### 10.1 What changed

| # | Area | v3.1 proposal | As built | Why / effect |
|---|---|---|---|---|
| 1 | **Default pipeline** | The orchestrator design *is* the system | Opt-in with `PIPELINE=v2`; `v1` (single `make_answer` path) is still the default. Personal v2 needs `CONVERSATION_STORE=database` | v1 kept as a stable baseline while v2 was built |
| 2 | **Knowledge and conversation storage** | Postgres `kb` and `conversation` tables | Knowledge is database-only since `0102`; saved conversations are switched on per setting (`CONVERSATION_STORE=off` by default) | Same reason |
| 3 | **Orchestrator graph** | 4 nodes: plan → prioritise → dispatch → compose | 8 nodes: adds resume_pending, propose_actions, review_actions and act, with conditional edges | Action recording, pause, outcome wording and validation handoffs are separate checkpointed steps |
| 4 | **Parallel dispatch** | LangGraph `Send` | `asyncio.TaskGroup` inside one node, per-task timeout | Simpler; same "slowest, not sum" latency |
| 5 | **Planner output** | `{domain, intent, entities}` with entity extraction | `{domain, question}` per issue; `intent: answer_question` | Agents read records themselves; extracted entities were not needed |
| 6 | **How agents work** | Tool-calling agents choose tools, with a cap on tool calls | Agent code reads data, then one model call; the model never chooses a tool | Fewer model calls; no prompt-driven data access |
| 7 | **Who proposes actions** | Fraud & Security and Relationship agents call `actions.propose`; Compliance updates checklists | Only Fraud & Security (temporary block). Relationship, Compliance and documents changes start from forms | Customer reviews exact fields before any change; no AI drafting |
| 8 | **Fraud block decision** | The model fills a playbook template | The model writes the description; code chooses the card to offer, and fixed wording reports the outcome | Outcome text never comes from the model |
| 9 | **Response Validator placement** | One step after compose | Per section inside each agent, plus the `act` node | Failed sections are withheld before composing |
| 10 | **Planner failure** | Fraud task still runs; others get "please resend" | Request fails with an explicit error | Not implemented |
| 11 | **SSE events** | `action_required`, `status {stage}` | `workflow_paused` (actions + full preview), `conversation`; `status` is text | Preview lets the customer see every section while deciding |
| 12 | **Confirm and resume** | `/commands/{id}/confirm` resumes the graph | Same, plus `/reject` and a retry route `/workflows/{turn}/resume`; the final reply is read from the saved turn | Resume can fail after a recorded decision without losing it |
| 13 | **Action execution** | Python executor files run inside `run_action()` | Python files define typed targets; SQL executors (private registry) do the writes; DB sets state and expiry | Ownership and confirmation are enforced again in the database |
| 14 | **Action types** | Card block, unblock, cancel, replacement; handoff claim/close; appointments; records | Adds `document.register`, `document.submit`; staff types in a separate path; `card.order_replacement` not built | Document upload became an action |
| 15 | **Second entry point** | Changes come from agents | Many REST routes for records and forms (`/customer`, `/relationship`, `/documents`, `/consent`, `/actions/cases`, `/relationship/holds`) | Web Client is a full banking site, not only a chat |
| 16 | **Document Check** | `docs.precheck` uses Storage + LLM | Rule-based prechecks (source-quoted requirements, calendar/page checks, optional local OCR) and credential-free parser process; the model only explains recorded findings | Document contents never enter model evidence; no AI verdicts |
| 17 | **Background Worker** | pg_cron + outbox poller + rules; starts orchestrator turns (RC1, CR7) | Outbox poller + in-worker reminder scheduler; no pg_cron; no worker-started AI turns | Not needed for the demo flows yet |
| 18 | **Notification Service** | Quiet hours, dedup, Realtime | Templates, preferences, retry/dead-letter, Realtime; English only; no email | As proposed for the demo scope |
| 19 | **Staff desk** | Seeded script stands in | `scripts/staff_support.py` with staff role, `aal2` and claimed-handoff ownership | Stricter than proposed; hosted MFA provisioning pending |
| 20 | **Relationship handover note** | Agent drafts summary/agenda | Fixed-code draft (`handover.py`), customer edits; AI drafting pending | No model-written content shared with the RM |
| 21 | **Checkpoint security** | LangGraph Postgres checkpointer | Owned Data API saver, HMAC-signed envelopes, closed JSON codec | Checkpoints cannot be forged or carry code |
| 22 | **Tracing schema** | `observability` | `ops.traces`, `ops.spans` | Name only |
| 23 | **Deployment** | Docker Compose first | Processes started directly; Docker dropped | Not needed |
| 24 | **Contracts** | §4 models | Same, plus `RequestContext.agent`, `AgentResult.question`, more redaction types, more audit categories (`row`, `document`) | Additive |

### 10.2 Built but not in the proposal
- Michelle's compliance workspace: notice-based prechecks, correction uploads, submission to awaiting review, scoped support context (migrations `0056`–`0061`).
- RM-change records (approved transition notices, meeting options; `0062`) and relationship managers for every customer (`0063`). Their client pages were removed on 28 Oct 2026; Workflow 2 (relationship manager) will use them.
- Handover to the relationship manager (`0073`, `services/rm_handover.py`) and template document checks (`services/document_templates.py`).
- Private document uploads, 60-second download links, technical inspection.
- Consent requests with fixed expiry, agenda drafts and confirmed sharing snapshots.
- `/capabilities` so the Web Client knows whether saved conversations are on.

### 10.3 Still pending
Worker-started AI turns, pg_cron, email, replacement-card ordering, AI agenda drafting, agent-initiated changes other than the card block, hosted staff MFA provisioning, the RM view (Workflow 2), measured latency, combined real-provider acceptance and broad accuracy evaluation.

### 10.4 Removed or merged in v3 (unchanged)

| v2 item | Decision |
|---|---|
| Separate microservices, separate gateway proxy | One FastAPI process |
| CQRS read model, per-turn cache | Plain read functions |
| Redis | In-process rate limit; Postgres for holds and dedup |
| Event producers + event bus + rules engine | One Background Worker over an outbox table |
| Separate Response Composer | Orchestrator node |
| Separate Case Escalation, Appointment, Card Adapter services | Executors behind one `run_action()` |
| Policy decision log, data-access audit, action ledger | Unified Audit Log Service |
| Embeddings, vector search, re-ranker | Keyword search in Postgres; optional hybrid keyword + pgvector search (`0099`); no re-ranker |
| OPA | Python rules |

---

## 11. Failure modes

| Failure | Behaviour |
|---|---|
| Agent LLM timeout / provider error | That section withheld (`origin: validation`); fraud keeps fixed urgent steps; handoff for saved signed-in cases; error never faked |
| Planner LLM failure | Request fails with an explicit error |
| Tool denied by policy | `PermissionError` withholds the section; `policy.decision` audited as `denied` |
| Validation fails | Section withheld, origin `validation`, handoff |
| Confirm after expiry | No execution; fixed "expired" wording |
| Duplicate confirm | Same receipt returned |
| Resume fails after decision | Decision kept; retry with `/workflows/{turn}/resume` |
| Worker crash | Uncommitted event lock rolls back; picked up on restart |
| Audit write fails | Request fails; no unaudited change |
| Saved conversations off with personal v2 | `personal_unavailable` error |

---

## 12. Build status

| Proposed phase | Scope | Status |
|---|---|---|
| 0 | Gateway, Guardrail, single-path answer, keyword knowledge, validator, traces | Built (v1, still the default) |
| 1 | Audit Log Service, conversation store, auth session events, `kb` tables | Built (`0005`–`0016`) |
| 2 | Orchestrator + Retail agent; sections in SSE | Built (eight nodes) |
| 3 | Customer Data, policy, Action Service (`cards`, `handoffs`), Fraud | Built (`0017`–`0022`, `0028`–`0033`) |
| 4 | Worker + inbox; Compliance and RM agents; `appointments`, `records` executors | Built, except worker-started AI turns and pg_cron (`0023`–`0049`) |
| 5 | Document Check + Storage; consent workflow | Built as rule-based checks (`0037`–`0061`) |

Hosted Supabase status, test commands and known limits are in the README.

## 13. Decisions

| # | Question | Status | Decision |
|---|---|---|---|
| 1 | Worker database access | Closed | Dedicated LOGIN role in `demobank_worker`, privilege-checked at start; never the service-role key |
| 2 | Which actions run without confirmation | Closed | `AUTO_TYPES`: `handoff.create`, `appointment.hold`, `compliance.update_checklist_item`, `agenda.save`, `consent.request`, `document.register`, `document.submit`. Everything else needs confirmation; staff types only through the staff path |
| 3 | Customers viewing their own audit trail | Deferred | Not in the UI; Actions shows receipts and a CSV export of loaded receipts |
| 4 | Hosted or local Supabase | Closed | Hosted for development and the demo; native PostgreSQL/PostgREST for automated tests |
| 5 | Default pipeline | Open | `v1` stays the default; persona workflows run with `PIPELINE=v2` |
| 6 | Agents choosing tools | Closed for v4; reopened for v5 (§14) | Agent code performs reads; one model call per task; model output never executes. v5: a bounded tool loop chooses reads from the agent's spec; model output still never executes |

## 14. Target architecture (v5 redesign, agreed 3 Nov 2026, not built yet)

This section records the agreed redesign. Sections 1–13 describe what is built. The stack (FastAPI, LangGraph, Supabase, OpenRouter) and the Input Guardrail are unchanged; the orchestrator, agent definitions, tool layer and confirmations change. The target agents and their data boundaries are in §5.6.1.

### 14.1 Why

Response time comes from sequential model calls, a synchronous checkpoint write after every node, and waiting for the slowest agent before anything is shown, not from node count. The v4 graph also runs every node on every turn, keeps a LangGraph pause that chat no longer uses (0081), and lets agents read only what their code fetches in advance.

### 14.2 Pattern and naming

**Orchestrator–workers** (planner–executor): a single-pass `planner` decides *what* happens; the graph (code) decides *how and when*. The planner is not a supervisor: it does not get control back after each agent, run agents one after another or write the final reply.

### 14.3 One chat turn

```mermaid
flowchart TD
  IN(["RiskAssessment<br/>(fixed safety replies never get here)"]) --> PL["planner<br/>rules → 1 model call → rules → plan check"]
  PL -->|confirms an open offer| ACT["act<br/>(no model)"]
  PL -->|tasks| W1["agent × N in parallel<br/>(Send)"]
  W1 --> GA["gather<br/>(join, next_wave)"]
  GA -->|a condition, input or for_each is ready<br/>(wave 2 at most)| W1
  GA -->|nothing left| CO["compose<br/>(no model)"]
  ACT --> CO
  CO --> OUT(["AnswerEvent + offers"])
```

| Node | What it does | Model? |
|---|---|---|
| `planner` | Rules first: replies to our own question (offer confirmation, which card, meeting time), urgent card and login wording, explicit RM requests, greetings. Otherwise one structured call on a fast model returning `PlanTask`s. Rules then set priority (P0 urgent card, P2 compliance deadlines or RM handover, P3 the rest; no Security agent and no P1 since build plan 2b) and RM handover flags, and code checks the plan against the agent catalogue. A confirmation goes straight to `act` | Only when rules cannot decide |
| `agent` | One run per task through LangGraph `Send`. Rules-first answers, then the bounded tool loop (§14.5), then the Response Validator against that agent's own evidence. Returns an `AgentOutput` | Yes, per agent |
| `gather` | Join node: runs once per wave after all parallel agents finish, merges their outputs, and `next_wave` either sends wave 2 or goes to `compose` (a conditional edge on `agent` itself would fire once per `Send`) | No |
| `act` | Confirmation turns only. Executes confirmed offers through the Action Service in dependency order; a later offer may use an earlier receipt | No |
| `compose` | Sections in priority order, a dependent section straight after the one it depends on; fixed "skipped" lines; offers shown with Confirm/Cancel | No |

After each wave the `gather` node runs a code router (`next_wave`) that evaluates dependencies and either sends the next tasks or goes to `compose`. State holds outputs, exports and offers keyed by task id with merge reducers, so parallel runs do not overwrite each other. **No checkpointer** is used for chat turns: the saved conversation and the open offers are the turn's state. A crash mid-turn marks the turn failed and the client retries; offers are idempotent.

The web client receives a `section` SSE event as each agent finishes, then the final ordered `answer`, so urgent sections appear first.

### 14.4 Components

| Layer | Component | Change from v4 |
|---|---|---|
| Edge | Web Client | Streams sections; Confirm/Cancel buttons send an `offer_id`; card-block and dispute dialogs removed |
| | API Gateway, Input Guardrail | Unchanged |
| Orchestrator | `planner`, `agent`, `gather`, `act`, `compose` | Replaces the eight nodes of §5.5 |
| Agent runtime | **Agent catalogue** (`AgentSpec` per agent, `agents/registry.py`) | New. Single source for policy, tool field views, the planner prompt and plan checks |
| | **Tool loop runner** | New. Shared bounded loop for all agents |
| | Response Validator | Unchanged rules; exports count as evidence with provenance (§14.6) |
| Tools | Internal tools (`services/tools.py`) | Return a **field view** per agent and enforce per-call limits (date window, rows) |
| | **External tool gateway** | New: `public_holidays` (any country), `fx_rate`. Allow-listed hosts, codes only (country, currency, year), timeout, 60 s cache, audit; a failure is reported in that section |
| Shared services | Customer Data, Knowledge, Action Service, Audit, Notifications | Unchanged |
| | **Offers** | Proposed commands on the existing Action Service ledger (`run_action(confirmed=False)`), plus `offer_group` and `group_order` for chains; owned under RLS, 30-minute expiry. No new table |
| LLM Gateway | Two model settings | `PLANNER_MODEL` (defaults to the agent model until measured) and the agent model. Same strict JSON check, retries and token budget |

### 14.5 Agents and the tool loop

The agents are the target set in §5.6.1 (Guide, Accounts & Cards, Compliance & Documents, Relationship & Service; RM Client, RM Requests and RM Calendar since build plan 3a; Security was removed in build plan 2b). Inside one agent:

1. **Rules first.** Records answers that must not be withheld (card figures, which card, meeting times, request status) return without a model call.
2. **Tool loop.** A strict-JSON step through `llm_gateway` (`calls` or `answer`; not native function calling, so any model and the Codex transport work) picks the next tool from its spec, at most **4 tool calls**, 30 s per agent, 60 s per turn; independent calls in one step run in parallel. Each call goes through `tools.call` (policy, caller JWT + RLS, audit) and returns only the agent's field view. The model sees results as untrusted evidence.
3. **Answer.** One strict-JSON answer validated against all evidence gathered in the loop; one repair attempt as today; otherwise the section is withheld.

Agents never write. They return **offers**; nothing changes until `act` runs on a later turn.

### 14.6 Agent interactions

Agents never call each other. They pass small typed values through the graph, and code decides what runs next.

```python
class AgentSpec(BaseModel):
    name: AgentName
    exports: dict[str,type]        # e.g. {'due_in_window': bool, 'due_dates': list[date]}
    imports: frozenset[str]        # exports this agent may receive
    hands_to: frozenset[AgentName] # agents it may pass a task to
```

| Interaction | Planner writes | Router does | Example |
|---|---|---|---|
| **Conditional** | `depends_on`, `condition` (a `bool` export) | Runs the task only if the export is `true`; `false` or unknown skips it with a fixed line | "If a payment falls due while I'm away, move my meeting"; "if I'd have less than 5k in savings after paying my cards, book a time with my RM" (`below_threshold`); "if my address proof failed its check, tell my RM" (`check_failed`) |
| **Input** | `depends_on` | Passes only the exports in the receiver's `imports` | Tax self-certification listing `account_refs` (type, currency, last 4; no balances) |
| **Fan-out** | `depends_on`, `for_each` (a list export) | One task per item, **at most 5**; "showing 5 of N" | RM: a reminder draft per client with a review due |
| **Handoff** | — | An agent returns `out_of_scope` with a `suggested` agent from `hands_to`; the task is reassigned **once**, question only, no planner call | Fee waiver sent to Accounts & Cards, passed to Relationship & Service |
| **Confirmation chain** | — (offers carry `after_offer`) | `act` executes in order; an earlier receipt fills a later offer; if the earlier one fails, the later one is not executed | Fraud report, then an RM note quoting its `TCK-` reference |

Rules:
1. **Exports are computed by code** from the agent's records (never written by the model), typed and small: booleans, dates, references, labels. Never raw rows, balances or free text.
2. **Exports carry provenance** (the source ids they came from), so the receiving agent's validator accepts a passed date or reference as evidence.
3. **The plan is checked before anything runs.** Every `depends_on`, `condition`, `for_each` and import must exist in the specs; no cycles; at most 2 waves (a reroute counts as a wave).
4. **Unknown means no.** A failed, withheld or missing upstream export skips the dependent task, and the client is told in fixed wording (for example "I couldn't check your due dates, so I haven't changed your meeting").
5. **No crossing roles.** Client exports never reach RM agents in the same turn, or the other way round. Guests get no dependent tasks.
6. **Audit records names and counts**, never values: `export.passed {from, to, name, count}`.
7. **More than one upstream (build plan 3b).** A dependant may name further wave-1 upstreams in `PlanTask.inputs_from`, for inputs only (no condition or fan-out). Each one passes the same link checks and must export something the receiver imports. `gather` skips the dependant if any upstream is not ok, and records `agent.export_passed` per upstream.
8. **Rules take part too (batch 3).** An agent's rules (`agents/rules.py`, which answer before the runner's model) receive the same `inputs` and may return exports through `rules.out`, released only with an `ok` answer and only for names the spec declares. Accounts exports `account_refs` from the records its rules answered from (`retail-accounts`); Compliance adds imported values to its evidence (`input-account_refs`, citable under compliance v7) and exports `check_failed` from the `document-prechecks` rows of the document the question names (`derived.check_failed`: issues found or unable to assess is true, no issues is false, no current check or no single match is unknown).
9. **The export decides the upstream's intent.** When a condition or `for_each` export is computed by one intent only (`registry.EXPORT_INTENT`: `due_in_window`/`due_dates` → `trip_due_dates`, `below_threshold` → `savings_after_cards`, `check_failed` → `document_findings`), `orchestrator.export_intents` gives the upstream task that intent before the link check, and the planner span records it as `intent_set`.
10. **Client exports (batch 3).** Accounts: `due_in_window`, `due_dates`, `account_refs` and `below_threshold` (`savings_after_cards`: her active SGD savings minus every card's outstanding balance, against the planner's `threshold` entity; another or unrecorded currency is not checked, and a missing amount is a fixed question). Compliance: `check_failed`; it imports `account_refs`. Relationship imports `due_dates`. The planner's agent list shows each agent's exports and imports (planner v10).

```python
def next_wave(state: Turn) -> list[Send] | str:
    if state['wave']>=2:return 'compose'
    sends=[]
    for task in state['plan'].waiting():
        up=state['outputs'].get(task.depends_on)
        if up is None or up.status!='ok' or (task.condition and task.condition not in up.exports):
            skip(task,'upstream_unavailable');continue
        if task.condition and up.exports[task.condition] is not True:
            skip(task,'condition_false');continue
        inputs={k:up.exports[k] for k in SPECS[task.agent].imports & up.exports.keys()}
        items=up.exports[task.for_each][:5] if task.for_each else [None]
        sends+=[Send('agent',{'task':task.for_item(item),'inputs':inputs}) for item in items]
    sends+=[Send('agent',{'task':o.task.reassign(o.suggested)}) for o in state['outputs'].values()
        if o.status=='out_of_scope' and not o.task.rerouted and o.suggested in SPECS[o.task.agent].hands_to]
    return sends or 'compose'
```

### 14.7 Confirmations as chat turns

There is no LangGraph pause. On the first turn, agents return offers and `compose` shows each one: what would be sent, **Confirm** and **Cancel**, and "or reply *yes*". Nothing is recorded.

On the next turn the planner's rules recognise the confirmation and go straight to `act`, with no model call and no agents:
- the button sends its `offer_id`;
- chained offers form one group with one Confirm button;
- a typed "yes" (English yes/no words only) counts only if exactly one offer group is open; with several, buttons only; "no" cancels; a new topic lets the offer lapse;
- the stored offer (a proposed command: type, exact target, 30-minute expiry; an expired one is offered again only if she asks) is executed, never text re-read from the earlier reply;
- `offer_id` is the idempotency key, so a double click or a repeated "yes" records once.

### 14.8 Contracts (`schemas/contracts.py`)

| Contract | Fields |
|---|---|
| `AgentSpec` | `name`, `role` (public/client/rm), `scope` (public/own_customer/rm_book), `answers` (what it handles; builds the planner prompt), `tools` (tool → field view and limits), `external` (hosts), `exports`, `imports`, `hands_to`, `offers` (action types), `prompt` |
| `PlanTask` | `id`, `agent`, `question`, `entities`, `priority`, `depends_on`, `condition`, `for_each`, `inputs_from` (3b) |
| `AgentOutput` | `section`, `evidence`, `exports` (with provenance), `offers`, `status` (`ok`, `needs_clarification`, `needs_human`, `out_of_scope`, `error`), `suggested` |
| `Offer` | `offer_id`, `type`, `target`, `summary`, `expires_at`, `after_offer` |

`TaskPlan`, `AgentResult` and the SSE events are replaced or extended by these. Contract changes no longer need a separate all-developer review (decided 3 Nov 2026; AGENTS.md is updated in phase 1).

Build plans 2b and 3a: `TargetAgent` is `guide`, `accounts`, `compliance`, `relationship`, `rm_client`, `rm_requests`, `rm_calendar` (`security` removed; the two RM agents split into three). `Priority` keeps P0–P3, but P1 is no longer assigned: P0 urgent card, P2 compliance deadline or RM handover, P3 the rest.

### 14.9 Data and migrations

- **New:** `0097` adds `offer_group`, `group_order` and `turn_id` to `actions.commands`, an `action_offer` RPC with a TTL (`action_propose` keeps its 5 minutes), a `customer_open_offers` read, and expires turns still paused for a decision; `0098` adds the `travel_notice` request category. `offer_id` is the command id and its idempotency key.
- **Kept, never edited:** the checkpoint and workflow tables (`0015`–`0016`, `0020`–`0022`). Chat stops writing to them.
- **Rules in code:** trip window, due dates inside it and bank-holiday effects are computed like `retail_checks`, never by the model.

### 14.10 Removed

`propose_actions`, `review_actions`, `interrupt` and `WorkflowPausedEvent`; `/workflows/{turn}/preview|resume`; the card-block and dispute dialogs; the `smalltalk` domain; the separate Document agent; the `linked: card_fraud` flag (login moves into Security); in build plan 2b, the Security agent itself (login help to Guide, urgent card cases to fixed rules and the RM); `resume_pending` and `prioritise` as separate nodes (their rules move into `planner`).

### 14.11 RM side

The same graph serves the RM, with the agent catalogue chosen by role: a verified `rm` login gets the RM agents; a client gets the client agents. This replaces the separate `services/rm/assistant.py` pipeline and lets RM fan-out use the same waves.

Since build plan 3a the RM side has three agents split by the data they read (`registry.RM_SPECS`):
- **Client** (`rm_client`) reads profiles, meeting notes, reviews and book facts, plus what a client shared under consent: transaction insights, talking points, large cash movements and the handover note. It proposes nothing. Talking points arrive as list values only (`TalkingPoint`: topic, stage, timeframe, concerns, origin, dates; never her words), with `pending` as a gap (0108).
- **Requests** (`rm_requests`) reads tickets, follow-ups, request types and approved guidance. It prepares message drafts, interaction records and follow-up tasks or service requests.
- **Calendar** (`rm_calendar`) reads her appointments, availability and clients' meeting requests. It prepares answer cards (accept a client's time, propose a free time, decline with a reason) and availability cards (block, open again, weekly hours) from the records, without a model.

The planner (`rm_planner/v5`) maps each intent to its agent. A question no intent covers becomes an `open` task on the agent whose records answer it. That agent runs the shared bounded loop (`runner.loop`, at most `AGENT_TOOL_CALLS` reads):
- clients are named as in her book and resolved by code;
- free-text records are shown to the step model only as id and title;
- a loop that stops at the limit says so in the reply.

Four links are declared:
- reviews due → one Requests draft per client (`clients_due`, at most 5);
- a brief → follow-ups from its latest note (`latest_note`);
- a handover brief → the shared note under consent (`consent_active`);
- reviews due → an `open` Requests task on those clients' tickets (`reviews_due`).

Build plan 3b adds the RM's priority score in code (`services/rm/priority.py`): urgent 50, past target or overdue 30, respond-by passed 30, review within 14 days 20, meeting within 2 days 20, and 1 a day waiting, up to 10. Each item carries its score parts and ties are fixed (score, date, kind, client, key). The model only explains the parts, and code keeps its items in rank order. Since the prompt writes one item per record, the answer's length follows her queue, so the model is given only the top 10 ranked records (`handlers.RANKED_SHOWN`) and, for `priorities`, the first 5 other follow-ups (`FOLLOWUPS_SHOWN`); code ends the reply with one fixed *Not listed here* line counting the rest by kind and pointing to the Today, Ticket inbox and Follow-ups pages (`not_listed`). In `today`, whose task file asks for every section, a category whose records all ranked lower is a count record of its own (`meeting-requests-ranked-lower`, `meetings-ranked-lower`, …), so the model says they are not listed instead of guessing that there are none, and the span records `ranked_shown`, `not_shown` and `followups_not_shown`. `today` (Requests) and `priorities` are linked by code, never by the planner (`graph.FEEDS`): code reuses an independent task of the fed intent or adds a feed task that only does fixed reads (no model, not shown). `today` gets the Calendar's `meetings_next_7d` and `requests_to_answer` and the Client's `reviews_due`; `priorities` gets `requests_to_answer`. The Today page reads the same ranking from `GET /rm/priorities`.

Since the RM answer speed slices (6 Nov 2026, [rm-answer-speed.md](planning/rm-answer-speed.md), `rm_assistant/v6`), the client reports follow the same rule: code writes what is already a record, and the model writes only what needs judgement. On the Claude Code transport a call's time follows its output tokens, so this keeps each report's model call small.
- The meeting brief, client overview, handover brief and meeting history are built by `services/rm/brief_facts.py` from the reads the handler already makes, run together (`asyncio.gather`). Each item cites its record id, list sections stop at 5 items and add a count line, and consent gaps come from the gated reads as before (with `ask_cards`).
- The model gets a bounded extract with the full records' ids (`brief_facts.facts`): the latest note only (the newest 3 for a handover, 5 for a history), the first 3 open tickets in the record section's order (hers first) however many are open, list records cut to 3, the 3 largest movements, ticket summaries cut to 200 characters, no account numbers. It writes the summary and a few points: conversation starters, discussion points and questions for a brief; up to 3 points worth raising for an overview; introductory topics and outdated records for a handover; gaps for a history. `checks.check_sections` checks those points against the extract; the reply's sources are the full records.
- An overview request is a report, and a question about the client ("When is her review due?") is answered by the model from the full records (`handlers.wants_report`: a button, report wording without a question mark, or no topic beyond names). A question about a meeting is answered the same way.
- Follow-ups from a note (`actions_from_note`, the brief's second wave) send the model the selected note, the request types and the open work counted by category (`handlers.open_by_category`); code lists the note's items and its date and format, and `documentation_reply` checks each action against every open ticket and task as before.
- A one-request message ("brief me on Michelle", "overview of David", "plan my day"), or a brief followed by follow-ups from the latest note (the declared `latest_note` link, R22), is planned by a rule when the words taken as the client are only her book name and nothing else is asked (`graph.rule_plan`, plan source `rule`); anything else goes to `rm_planner/v5`.
- `POST /rm/assistant` streams `section` events (task id, sections, sources) between `status` and `answer`: code sections once the records are read, then the checked points. Only checked text is streamed, and `answer` stays the reply of record. The trace sets `first_section_ms`, which Admin → Latency reads. The UI shows the parts in the pending reply (`lib/rm.js` `partialReply`).
- On the Claude Code transport the `llm` span also records `num_turns`, `api_ms` and `answer_chars` (the CLI reports no separate thinking count, so the gap between output tokens and answer size shows the thinking).

Calendar cards are approved one by one through the 2a routes (`/rm/meeting-requests/{id}/decide`, `/rm/availability/blocks`, `/rm/availability/hours`). The assistant writes nothing.

### 14.12 Evaluation

- Trajectories name `planner`, `agent` (each agent and its wave), `act` and `compose`, plus tools per agent, external calls and the order `act` ran in.
- A **planner-only dataset**: message → expected agents, entities and dependencies, with no agents run.
- New cases: client C23 travel and reschedule (conditional + input + external API), C24 tax self-certification (input), C25 affordability then a meeting (conditional), C26 failed document check then the RM (conditional + input), C27 fraud report then an RM note (confirmation chain); RM R21 review reminders (fan-out), R22 brief and ticket drafts (input), R23 inherited client and consent (conditional); and a fee-waiver handoff case. Each has a failure variant: external API down, upstream agent withheld, or an earlier receipt failing.
- LangSmith spans per wave and per tool call; time to first section and total time compared before and after.

### 14.13 Build order

The phased plan, with files, tests and done criteria per phase, is [planning/v5-build-plan.md](planning/v5-build-plan.md). One commit per phase on one branch; phases 1–4 add unused parts, phase 5 switches the backend over in one commit and keeps the `answer` event compatible until the UI follows in phase 6.

| # | Slice | Client-visible? |
|---|---|---|
| 0 | Baseline: commit docs, record pre-existing test failures and current latency | No |
| 1 | `AgentSpec` catalogue, tool field views, one tool-to-permission name map (fixes `docs.precheck`) | No |
| 2 | External tool gateway (`public_holidays`, `fx_rate`) | No |
| 3 | Tool loop runner, first for Accounts & Cards | No |
| 4 | Offers on the Action Service (`0097`) | No |
| 5 | Switch-over: five-node graph, restructured agents, confirmations as chat turns, section streaming; pause and its endpoints removed | Yes |
| 6 | Web client: placeholders, Confirm/Cancel per offer group, dialogs removed | Yes |
| 7 | Dependencies: exports, waves, fan-out, handoff, ordered `act`, `travel_notice` (`0098`) | Yes |
| 8 | Eval cases (C23–C27, handoff) and planner dataset; docs | No |
| 9 | RM pipeline onto the same graph (R21–R23), later | Yes (RM) |

## 15. Meeting lifecycle seam (RM copilot target, hybrid slice implemented)

The next target centres the product on the relationship manager: a client's meeting request, a consent-gated pre-meeting brief, the meeting itself, and RM-approved minutes. The meeting lifecycle runs as its own LangGraph graph (durable, one thread per meeting, pauses for the RM's approvals), separate from the chat graphs, because it is started by events and lives for days. Migration `0105` and the shared files below define the original seam. The opt-in hybrid implementation in §15.3 provides the minutes and follow-up slice; the complete durable pre-meeting graph remains a target.

| Part | Where | Rule |
|---|---|---|
| Shared models | `schemas/meetings.py` | `MeetingRef`, `MeetingEvent`, `BriefCard` (items labelled record, client said, AI suggestion or gap; every non-gap item cited), `MinutesDraft`, `ActionItem` (supporting quote required), `Decision`, `MeetingStatus`. Additive changes only |
| Public API | `services/meetings/__init__.py` | `on_event`, `prepare`, `decide`, `status`. `MeetingsUnavailable` until built and while `MEETINGS_ENABLED=false` |
| Event queue | `meetings.events` (0105) | Private schema, not in the Data API. `enqueue` (payload of ids, codes and dates; `dedupe_key` for provider or command ids), `claim_next` (10-minute lease), `finish` (backoff, failed and audited after 5 attempts). Role `demobank_meetings` for the runner |
| Consent by category | `rm.consent_active`, `rm_consent_status` (0105) | Client in the RM's book and an active `rm_brief` grant to that RM covering `transaction_insights`, `chat_topics` or `meeting_recording`. Checked inside SQL reads, so a gated reader returns nothing, never filtered in the prompt |
| Identity | — | Steps that read client records run with the RM's JWT (`prepare`, `decide`, `status`). The runner has no user JWT and reads no client records |
| Ownership | — | Meeting side: `services/meetings/**`, `api/meetings.py`, its prompts and UI, migrations 0120–0139. Client, consent and copilot side: the rest, migrations 0106–0119 |

### 15.1 What the client and copilot side provides (0106, 0107, build plan phase 1)

Agreed additions to the seam ([build plan](planning/rm-copilot-build-plan.md), phase 1a), built in `0106_meeting_requests.sql` and finalised in `0107_talking_point_contract.sql` (1d):

| Part | Where | Rule |
|---|---|---|
| Meeting requests | `rm.meeting_requests`; client actions `meeting.request`, `meeting.withdraw`, `meeting.counter_reply` | The client asks with a purpose (her words, redacted, 1–200 characters), 1–5 topic codes (`rm.meeting_topics()`: `home_loan`, `investments`, `fixed_deposit`, `insurance`, `term_sheet`, `compliance_review`, `account_review`, `card_review`, `other`), a format and 1–3 preferred times. Status `requested` → `scheduled`, `countered`, `declined` or `withdrawn`; `respond_by` is one business day later and a late request reads as `overdue` (it stays open). At most 3 open requests per client |
| RM decisions | `public.rm_meeting_request_decide(decision_id, request_id, outcome, starts_at, reason)` | `accept` one of the preferred times (creates the confirmed meeting with purpose and topics), `counter` with another time (a held meeting the client accepts or declines within two business days; an unanswered proposal can be decided again) or `decline` with `answered_by_message`, `redirected` or `not_needed`. Book check, working hours, blocks, overlap and the weekly limit (`rm.slot_problem`). Idempotent on `decision_id`; the client gets an Inbox notice. Called by `POST /rm/meeting-requests/{id}/decide` and `POST /rm/appointments/{id}/reschedule` (2a); a refused time is 409 `rm_slot_unavailable` |
| Reschedule | `public.rm_meeting_reschedule(change_id, appointment_id, starts_at, reason)` | An upcoming confirmed meeting moves to another free time; reason `client_asked`, `rm_unavailable` or `other` |
| Events we queue | `meetings.enqueue` from our SQL, same transaction as the change | `meeting.scheduled` when a meeting becomes confirmed (RM accept, client accepts a proposed time, or a direct booking), `meeting.rescheduled`, `meeting.cancelled` (the client cancels a confirmed meeting). `dedupe_key` is `decision:<id>`, `command:<id>` or `change:<id>`. Payload: `starts_at`, `ends_at` (UTC), `format`, `topics`, `request_id`, `by` (`client` or `rm`), and `reason` on a reschedule; never the purpose. `brief.requested` and `transcript.ready` are the meeting side's |
| Purpose and topics | `rm.appointments.purpose` (0089), `rm.appointments.topics` (0106) | `MeetingRef` is filled from the row. Direct bookings have no purpose and no topics |
| Readers the brief calls | `rm_client_profile`, `rm_review_status`, `rm_tickets`, `rm_meeting_notes` (exist); `rm_transaction_insights(customer, purpose, days)` (0106; `purpose` since 0107), `rm_talking_points(customer, purpose)` (0106; final shape 0107) | All with the RM's JWT. `purpose` is `meeting_prep` (the brief) or `client_view`: set by code from the intent, never by a model, and audited. Transaction insights return `{"shared": false, "category": ..., "data": null}` when `rm.consent_active` is false, audited as `policy.rm_consent_gap`; when shared, the data with `grant_id`, audited as `data.read` with category, grant id and purpose. **Talking points are the one exception to that shape:** `data` is always a list of `TalkingPoint` (`schemas/meetings.py`: topic, stage, timeframe, concerns, origin, first and last seen; no text field) and is filled since 0108 (build plan phase 4). With the `chat_topics` grant, `data` holds her confirmed points plus inferred points that are past their 24-hour hold and not paused (audited `data.read` with grant id and purpose). Without it, `shared: false` with the consent-gap audit, and `data` holds only topics she shared herself (`origin: confirmed`, audited `data.read` with basis `client_shared` and the share command ids). An additive `pending: true` says some inferred topic is on hold or paused, never which or why; the copilot shows it as a gap. Render each point with `TalkingPoint.line()` and label it with `brief_label()` (inferred → `ai_suggestion`, confirmed → `client_said`). `rm_cash_events` has the `transaction_insights` gate (audited as `client_view`). Tools of the same names in `services/tools.py`; `rm/evidence.py` turns a gap into consent evidence and drops (and counts) any point that fails the contract |
| Talking points (0108, build plan phase 4) | Private `insights` schema (not in the Data API; RLS on every table): `talking_points`, `talking_point_evidence` (her quotes; no RM role has a grant and no `rm_*` function reads it), `topic_mutes`, `opportunity_pauses`, `share_prompts`; `rm.topic_shares` | **Inferred (lane 1):** after the `answer` event of a signed-in saved chat, a background task runs consent and pause checks, a code pre-filter, one `talking_points/v1` call (`TALKING_POINTS_MODEL`, no retry) and validators (exact quote of her words with a word from the topic's list, no sensitive word, not muted, amounts masked), then `customer_record_talking_point` under her JWT (rechecks the grant, idempotent on turn and topic, at most 3 quotes, stage only moves up). A care signal calls `customer_pause_suggestions` (60 days, nothing stored). **Shared (lane 2):** a compose rule offers `insight.share` (confirmed); it makes a confirmed point and sends an RM notice that names no topic (at most 20 per RM per day). **Her controls:** `insight.delete`, `insight.mute`, `insight.resume` (automatic, with receipts); `customer_read_talking_points`, `customer_topic_status`. **Today:** `rm_shared_topics()` lists confirmed shares only (book, 30 days, latest per point, at most 20). **Withdrawal:** trigger `forget_chat_topics` on `consent.grants` deletes inferred points and all evidence when no `chat_topics` grant to her current RM remains; confirmed points stay until she deletes them or they expire (90 days after last seen). Seed: Michelle's inferred home-loan point, Mei Ling's confirmed investments share; `rm.reset_meeting_demo` re-seeds both |
| Client-written text | `rm.meeting_requests.purpose`, ticket messages | Shown to the RM as written and never put in `meetings.events`. RM-side models, the meeting side's `brief_writer` included, get it only as quoted data, never as instructions; `topics` (codes) drive the brief |
| Writes after minutes | `rm_create_tasks`, `rm_create_service_request`, `rm_send_client_update` (0089) | The meeting side uses these existing approved RM writes for action items and the recap; no new write paths |
| Recording consent | `meeting_recording` category | Only the seeded grant until consent v2 (§15.2): recording is then on by default with a notice, and the client can revoke the transcript only |
| Demo data | 0106 seed | Michelle (1002) shares all three categories with Sophie; her request "Home loan options" (topics `home_loan`, `term_sheet`, `compliance_review`) is a confirmed video meeting on the next free Thursday at 15:00, with its `meeting.scheduled` event queued. Mei Ling (1007) has an open fixed deposit request and no category grant, so her gated reads show the gap. The dates are fixed on the day the seed runs; `rm.reset_meeting_demo()` (0107, owner only; hosted `34_reset_meeting_demo.sql`) removes both clients' meeting requests, their meetings and events, and seeds them again from today |


**Client side (2a).** The client chat drafts a request (`agents/meeting_answers.py`) and her confirmation records it through `action_offer`; the Meetings tab proposes and confirms `meeting.request`, `meeting.withdraw` and `meeting.counter_reply` directly. `AnswerEvent.care` (from `guardrail.CARE`) switches the UI's suggestions to a person.

**Copilot side (3a).** The RM Client agent calls the consent-gated readers with a purpose set by the intent (`client_view` for an overview, `meeting_prep` for a brief), never by a model. Without consent the evidence records a gap, and the answer states what the client has not shared. The Calendar agent's decision and availability cards use the 0106 routes and `rm.slot_problem`, so a time SQL refuses is shown as not acceptable. Recording a decision enqueues the meeting events as in 2a.

### 15.2 Consent v2 (agreed 5 Nov 2026, build plan phase 6, 0109–0114; built 5 Nov 2026 except 6c)

**Built 5 Nov 2026 (all but 6c).**
- **0109:** grants with a scope and an end: a date up to 12 months ahead, until a meeting ends, or until a meeting request is answered (then until the meeting it becomes). Also `consent.categories`, scope-aware gated readers, the renewal notice, notice-based recording, reassignment ending access, and `customer_read_consent_sharing`.
- **0110:** soft delete with `insight.restore` (Undo for 7 days). The worker purges through `events.purge_removed_topics`, audited `data.insight_purged`, and keeps a tombstone of ids, dates and reason. `rm_point_status` serves the meeting side's re-check.
- **0112:** `my_consent_access_log(p_customer_id)`, built from existing audit events.
- **0113:** `consent.asks` and `rm_ask_consent`, with fixed reason codes, one open ask per client, RM and category, a 14-day lapse and `consent.decline_ask`. Also `data_deletion` with a 10-business-day target.
- **0114:** offer switches through `offer_group_confirm(group, include)`. A switch is a `consent.grant` until `request` after the first step, and stays off unless she turns it on; a typed yes includes none.
- **Chat:** offers come from rules in `agents/consent_answers.py`; `relationship/v7` only explains.
- **Not built:** 0111 `transcript.revoke` (6c), and on the meeting side the brief references, `grants_used` and the *Removed by client* rendering.
- **Hosted:** batches `36`–`40` are prepared but not applied.

The client decides per category what her RM may see, for how long, and can take it back. Grants are per RM (recipient = `advisor_id`) under the `rm_brief` purpose; `rm.consent_active` stays the only check, made scope-aware. Revoking or expiry **stops access at once**; anything that must be deleted is soft-deleted and purged within 7 days.

| Category | Default | Granularity | Revoke or delete | Expiry |
|---|---|---|---|---|
| `transaction_insights` | Off | Accounts (all or chosen) and a look-back of 3, 6 or 12 months | Revoke hides the data at once. Nothing is deleted: the insights are computed from her own records on each read | An end date of at most 12 months, or *until after my next meeting* (`until_meeting_id`, closed when that meeting's minutes are approved). Inbox renewal notice 7 days before; a renewal is a new grant |
| `chat_topics` | Off | Per topic area (fixed list of about 6–8 areas, e.g. home and property, investments, deposits, insurance, cards, everyday banking). Sensitive areas (health, family, legal) are never stored | Delete one topic: hidden at once, *Undo* for 7 days, then purged. Revoke: stops extraction and soft-deletes every inferred topic (same 7-day purge). Topics she shared herself stay (each yes was consent for that one disclosure) | Each topic expires 90 days after it was last seen; the grant itself up to 12 months |
| `meeting_recording` | **On**, notice-based: the invite carries a recording notice and its `notice_version` | Per meeting | The client can revoke **only the transcript** (`transcript.revoke`): soft-deleted, purged after 7 days. Approved minutes are the bank's record of the meeting and have no client delete path. Revoked before drafting: the RM documents the call by hand. Revoked after drafting, before approval: the draft stays but cannot be regenerated | Without revocation, the raw transcript is deleted 30 days after the minutes are approved |

**Briefs and what was already shown.** A brief stores references and `grants_used`, never copies of gated data, and re-checks consent and deletions when it is opened. A removed item renders as *Removed by client on <date>*; an AI suggestion that rested only on removed items is dropped. Shared client packs and approved minutes are not rewritten; if the topic behind a pack was removed, the RM sees a flag.

**Deletion practice.** Access stops at once; purge within 7 days by a worker job (audited `data.insight_purged`, and `transcript.purged` with 6c; ids only). A *delete all my data* request becomes a ticket with a 10-business-day target. Background: PDPA asks for a "reasonable time" (the PDPC consulted on 10 business days in 2018, not a fixed rule); GDPR allows one month; the Australian CDR says as soon as practicable.

| Part | Where | Rule |
|---|---|---|
| Grants with scope | `0109`: extends the 0037 `actions.normalise_consent_*` and executors; `consent.categories` catalogue | One grant per category and RM; `scope` (accounts, `lookback_months`, topic areas), `expires_at` (≤ 12 months) or `until_meeting_id`, `notice_version`. `rm.consent_scope(customer, category, advisor)` returns the scope; gated readers filter by it in SQL |
| Renewal and expiry | `0109` | Inbox notice 7 days before expiry; `until_meeting` grants close on `minutes.approved` |
| Topic deletion | `0110`: `deleted_at`, `purge_after`, `deleted_reason` on talking points; actions `insight.delete`, `insight.restore` | Revoke cascades a soft delete of inferred topics; deleted rows never reach `rm_talking_points`; purge by the worker |
| Transcript revoke | `0111`: action `transcript.revoke`; `revoked_at`, `purge_after` on the meeting side's transcript, agreed in `schemas/meetings.py` (additive) | The meeting side refuses regeneration from a revoked transcript; minutes have no client delete |
| Access log | `0112`: `public.my_consent_access_log()` | The client's view of RM reads of gated categories (who, when, category, purpose, meeting), from the `data.read` audit events; ids, codes and dates only |
| RM asks to share | `0113`: request category `rm_consent_request` (0071 ticket pattern) | Sophie asks with a reason and optional meeting; lapses after 14 days; the client answers in Data sharing or chat |
| Client offers | Relationship & Service (`relationship/v7`) | `consent.grant` (with scope), `consent.revoke`, `insight.delete`, `transcript.revoke`, all confirmed as v5 offers. The meeting request card shows inline switches as a chained offer group |

### 15.3 Hybrid meeting lifecycle (implemented, explicit runtime opt-in)

`MEETINGS_ENABLED` keeps the existing client and RM chat graphs independent. The existing client action ledger and RM Calendar own scheduling. `api/meetings.py` uses the same verified gateway and caller-JWT Data API; it never uses a service-role key or lab identities. Shared React meeting cards reuse the product's form/button/theme tokens in client Meetings and RM Schedule.

Meeting-owned migrations 0120–0124 add fenced queue claims, per-meeting occurrence/revision state, a private evidence store, exact-version/hash/permission-epoch approvals and separate task/recap effects. `services/meetings/graph.py` is the separate Minutes Writer LangGraph. Its state contains IDs; the runtime carries only the selected redacted transcript or explicit RM notes. It cannot read bank records or send a client message. SQL validates quotes, speaker ownership, conditions and dates again at persistence and approval. The restricted worker never receives the RM's JWT. Restart recovery reuses an already persisted draft; SQL phase state is durable, while model calls are not automatically recoverable mid-call.

Both current participants acknowledge the versioned meeting notice before transcript import. Withdrawal immediately blocks new generation; existing drafts require fresh review, approved minutes remain, and raw transcript cues are purged within seven days. Otherwise raw cues expire thirty days after minutes approval. The restricted worker runs audited retention. These are meeting-side provisions pending the team's 0109–0113 consent-v2 integration; no existing broad grants are expanded.

Zoom currently supplies scheduling/link management through a configured private own-user OAuth grant. Call times are explicitly RM-documented. TXT/VTT intake is bounded at 1 MiB raw input, 100 cues and 16 KB redacted evidence, with no truncation. Speaker mapping and coverage are RM assertions. Transcripts queue a worker job; explicit typed notes can be drafted on an RM request. Creating follow-ups and sharing a recap require separate approvals after exact minutes approval. Each participant completes only their own follow-ups with evidence.

Automatic provider webhooks/capture, caption plugin installation, longer-call chunking, pre-meeting Brief Writer and client-safe Proposal Drafter remain separate work. `prepare` therefore continues to report unavailable; the existing brief seam is not replaced by a notes summary. No hosted migration is applied automatically, and no credentials are included in source.

## Appendix: knowledge base

How the approved banking articles are stored, searched and cited, and which articles each story needs.

### Design

Status: **built**, with changes (4 Nov 2026). Detailed design of the Knowledge Service in §5.7. The articles are in the database and the in-memory library (`knowledge.py`) was removed (`0102`). This appendix keeps the original proposal: `kb.facts` and `get_fact` below were built and later dropped, because figures are checked against the section text.

#### 1. Summary

- **Retrieval-augmented generation (RAG):** agents retrieve approved sections, and the LLM answers only from them, with citations.
- **Keyword retrieval:** Postgres full-text search over article sections. Hybrid search (`0099`, on whenever an embedding key is set; keyword fallback) adds embedding similarity in the same database, fused with the keyword ranking.
- **No new infrastructure:** everything lives in the hosted Supabase Postgres that the app already uses.
- **Three tables, one search function.** All content is public guidance; the app can only read it.
- **No content workflow:** whatever is in the tables is live. Content changes arrive only through migrations.

```mermaid
flowchart LR
  AG["Agents / Validator"] -->|search, get_fact| KS["Knowledge Service<br/>knowledge.py"]
  KS -->|RPC| FN["kb.search()<br/>full-text"]
  FN --> SEC[("kb.sections<br/>text + tsv")]
  SEC --> ART[("kb.articles")]
  KS -->|exact lookup| FACT[("kb.facts")]
  SEED["Migration files<br/>in git"] --> ART & SEC & FACT
```

#### 2. Data model

| Table | One row is | Key columns |
|---|---|---|
| `kb.articles` | One topic, e.g. "Pending transfers" | `article_id`, `version`, `category`, `type`, `title`, `summary`, source fields |
| `kb.sections` | One searchable part of an article, e.g. "What pending means" | `article_id`, `anchor`, `position`, `heading`, `body`, `tsv` (generated) |
| `kb.facts` | One exact figure, e.g. a card's annual fee | `fact_id`, `value`, `unit`, `article_id` |

```sql
create schema kb;

create table kb.articles (
  article_id text primary key,
  version int not null default 1,
  category text not null,          -- accounts, cards, loans, payments, security, support, compliance, relationship
  type text not null,              -- faq, how_to, troubleshooting, product
  title text not null,
  summary text not null,
  publisher text not null default 'demobank',
  reference_url text, checked_on date, review_due date,
  check (publisher = 'demobank' or (reference_url is not null and checked_on is not null and review_due is not null)));

create table kb.sections (
  article_id text not null references kb.articles on delete cascade,
  anchor text not null,            -- stable id used in citations, e.g. 'check-status'
  position int not null,
  heading text not null,
  body text not null,
  tsv tsvector generated always as (to_tsvector('english', heading || ' ' || body)) stored,
  primary key (article_id, anchor));
create index on kb.sections using gin (tsv);

create table kb.facts (
  fact_id text primary key,        -- e.g. 'card.platinum.annual_fee'
  value numeric not null,
  unit text not null,              -- 'SGD', 'percent_pa', 'days'
  article_id text not null references kb.articles);
```

RLS is enabled on all three tables with a read-only policy for `anon` and `authenticated`; no app role can insert, update or delete. The allowed-domain rule for external publishers (`www.moneysense.gov.sg`, `www.abs.org.sg`) stays in the Python validator, where it already lives (`services/source_checks.py`).

#### 3. Writing sections

A section is the unit the model sees and the unit a citation points to, so its shape matters more than the article's.

| Guideline | Why |
|---|---|
| One idea per section, roughly 50–300 words | Small enough to be precise, large enough to stand alone |
| Heading says what the section answers ("Why a transfer stays pending") | The heading is part of the searchable text |
| Use the words customers use ("stuck", "won't go through") as well as banking terms | Keyword search only matches words that appear |
| Steps as a numbered list inside one section | The agent can quote the steps in order |
| Figures only as a reference to a fact (`card.platinum.annual_fee`), not typed into the body | One place to change a fee; the Validator checks figures against `kb.facts` |
| Never mention a specific customer | Customer details come from Customer Data, not from knowledge |

Example: article `pending-transfers` (type `troubleshooting`, category `payments`):

| anchor | heading |
|---|---|
| `what-pending-means` | What a pending transfer means |
| `how-long` | How long transfers usually take |
| `check-status` | How to check a transfer's status |
| `avoid-duplicates` | Why you should not resend a pending transfer |
| `contact-bank` | When to contact the bank |

Citation format: `article_id@v<version>#anchor`, e.g. `pending-transfers@v2#avoid-duplicates`.

#### 4. Search function

```sql
create function kb.search(q_text text, categories text[] default null, k int default 5)
returns table (cite text, article_id text, title text, heading text, body text, score float)
language sql stable security invoker as $$
  select s.article_id || '@v' || a.version || '#' || s.anchor, s.article_id, a.title, s.heading, s.body,
         ts_rank_cd(s.tsv, websearch_to_tsquery('english', q_text)) as score
  from kb.sections s join kb.articles a using (article_id)
  where s.tsv @@ websearch_to_tsquery('english', q_text)
    and (categories is null or a.category = any(categories))
  order by score desc limit k $$;
```

| Parameter | Meaning |
|---|---|
| `q_text` | The user's question; `websearch_to_tsquery` handles stemming ("transfers" matches "transfer") and ignores stop words |
| `categories` | Agent scope, e.g. the Compliance Review agent searches `compliance` and `support` |
| `k` | Results returned |

An unrelated question matches nothing and returns an empty list instead of the "least bad" section. The `kb` schema must be added to the API's exposed schemas in Supabase, or the function wrapped as `public.kb_search`.

#### 5. Knowledge Service (Python)

`backend/app/services/knowledge.py` becomes a thin client:

| Function | Does |
|---|---|
| `search(query, categories, k)` | Fixes common typos (as today), calls `kb.search`, returns `[{cite, title, heading, body, score}]`. Empty list means no approved content: the agent says so or hands off |
| `get_fact(fact_id)` | Exact lookup in `kb.facts`. Used by agents and by the Response Validator |
| `get_article(article_id)` | Article with its sections, for the sources panel (`GET /knowledge/{id}`) |

Behaviour rules:
- **No match or non-English query:** the `english` full-text setting will not match other languages. Keep today's behaviour of using the small default library when nothing is retrieved. Answers are still generated only from retrieved sections, never from sample text.
- **Logging:** query text is never logged; traces record the number of results, the cites returned and the scores.

#### 6. Content workflow

1. Write or edit articles and sections as a new migration file in `backend/supabase/migrations/` (e.g. `0005_knowledge_seed.sql`, then `0006_knowledge_cards_update.sql`).
2. Editing an existing article: bump its `version` (and `checked_on` / `review_due` for external sources).
3. Apply the migration to the hosted Supabase project.

**Migrations are the only way content changes.** The Supabase dashboard is for viewing. This keeps git and the live database identical, and makes evaluation runs reproducible.

#### 7. Evaluation

Retrieval is measured separately from answer quality.

| Metric | Meaning |
|---|---|
| Hit rate @5 | The expected article appears in the top 5 sections |
| MRR | How high the first correct section ranks (1 / rank, averaged) |
| No-match accuracy | Out-of-scope questions return no sections |

Add an `expected_article` (and where useful `expected_anchor`) to the cases in `backend/evaluation/`, including informal and misspelled rewrites of each question (F22 evaluation set). Misses on informal wording are fixed first by adding customer wording to section text; if they remain common, that is the evidence for adding vector search later.

#### 8. Tests

| Level | What |
|---|---|
| Unit (offline) | Small fixture of articles and sections; checks citation format, typo fixing and empty-result handling |
| Database | Migration applies; app roles can read but not write `kb` tables |
| Live evaluation | Retrieval metrics above against the hosted database |

#### 9. Not included (and why)

| Left out | Reason |
|---|---|
| Customer-only articles and audience filtering | All knowledge is general guidance; personal details come from Customer Data |
| Separate vector database (Pinecone, Qdrant, Weaviate) | About 60 sections; pgvector in the existing Postgres scans them exactly in a few milliseconds |
| Draft / review / publish workflow, version history | One author; migrations in git already give history |
| Re-ranker model | Small library; add only if evaluation shows ranking problems |
| Helpful votes, search-miss log | Can be added later without changing these tables |
| Internal (staff-only) articles | No staff desk in the demo |

### Requirements

Companion to the Knowledge Service in §5.7. Lists the approved content the agents need to answer every backlog story. Coursework demo: all content is synthetic or summarised from allowed public sources.

**Knowledge vs customer data.** Knowledge is approved content that is the same for every customer (how pending transfers work, what a review status means). Anything about one customer (their balance, their compliance case, their RM) is Customer Data and is not listed here.

#### 1. Knowledge articles

Status: **exists** = article in today's `knowledge.py`; **partial** = exists but misses part of the story; **new** = to write.

##### Everyday banking (CR2, CR12–CR16, F7)

| Story | Article(s) needed | Status | Current id |
|---|---|---|---|
| CR2 Account products | Account types and features, how interest works, account services | partial | `accounts`, `sg-accounts` |
| CR16 Loan Enquiry | Loan products, eligibility criteria, application steps, "only the bank decides" wording | partial | `loans` |
| CR14 Credit Card Enquiry | Minimum payment, interest, available credit, card fees, card not working | exists | `cards`, `sg-card-payments` |
| CR13 Failed or pending transaction | Failed vs pending, troubleshooting per type | exists | `transactions` |
| CR12 Suspicious charge and lost card | Fraud playbook steps, lost/stolen card steps | partial | `lost-card` |
| CR12 Replacement card | How a replacement card is issued and when it arrives | new | |
| CR2 Transfers | How to make a local / overseas transfer, cut-off times | new | |
| CR2 Update personal details | How to change address, phone, email | new | |
| CR2 Statements | Where to find and download statements | exists | `statements` |
| CR2 Online / mobile banking access | App login failing, account locked, password reset in the official app | new | |
| F7 Privacy | Never share PIN, OTP, password, CVV | exists | `security` |
| Human support | How to reach a person; what the bot cannot do | partial (update when handoffs exist) | `support` |

##### Compliance review (CR3–CR7)

| Story | Article(s) needed | Status |
|---|---|---|
| CR3 Document checklist | Approved guidance per document type: what counts as proof of identity, address, income, source of wealth; accepted formats; how to submit | new |
| CR4 Document upload | Allowed formats, size limits, tips for a legible scan | new |
| CR6 Milestone tracker | What each review status means (`not submitted`, `submitted – awaiting review`, `under review`, `complete`) and what happens at each stage | new |
| CR6 Information requests | How to respond to a clarification request and how to confirm it was received | new |
| CR7 Reminders (questions about a reminder) | Why reminders are sent and how to change preferences | new |

**Summary:** about 10 everyday-banking articles exist; the compliance guidance above is still to write. Approved wording for questions that go to the relationship manager comes from Workflow 2.

#### 2. Fact table (exact figures)

Typed rows the Response Validator checks figures against (`kb.get_fact`). Every row has `fact_id`, `value`, `unit` and the `article_id` it comes from.

| Group | Facts |
|---|---|
| Card fees | Annual fee, late payment fee, foreign transaction fee, cash advance fee |
| Interest rates | Savings rate, card interest rate, indicative loan rates |
| Limits | Daily transfer limit, ATM withdrawal limit |
| Card payments | Minimum payment formula, payment due period |
| Processing times | Typical time for local and overseas transfers to complete (for pending questions) |

#### 3. Fixed templates (shown as written, never searched)

| Template | Used by |
|---|---|
| Loan and fee disclaimers | Orchestrator *Compose* (F18) |
| AI notice at chat start | Web Client (F18) |
| Lost / stolen card urgent response | Input Guardrail (CR12) |
| Secret-detected warning | Input Guardrail (F7) |
| Notification templates (review reminder, card blocked, RM changed, booking reminder) | Notification Service (`notify.templates`) |

#### 4. Configuration (rules, not articles)

| Config | Used by |
|---|---|
| Document Check rules per document type: required pages, required fields, legibility threshold | Document Check agent (CR5) |

#### 5. Article and section fields

Each article has `article_id`, `version`, `category`, `type` (`faq`, `how_to`, `troubleshooting`, `product`), `title`, `summary` and `publisher` (`demobank` or an allowed external publisher). External-publisher articles also need `reference_url`, `checked_on`, `review_due` and an allowed domain (`www.moneysense.gov.sg`, `www.abs.org.sg`). Editing an article bumps `version` (and the dates for external sources).

Each article is split into **sections** (one idea each, about 50–300 words, a heading that says what it answers). Sections are what search returns and what citations point to (`article_id@vN#anchor`). Figures are not typed into sections; they live in the fact table. Writing guidelines and the full schema are in *Design* above.

#### 6. Where it is stored

| What | Where |
|---|---|
| Articles, sections and facts (runtime) | Hosted Supabase Postgres: `kb.articles`, `kb.sections` (with a full-text search column), `kb.facts`. Read-only for the app through RLS |
| Content changes | Migration files in `backend/supabase/migrations/`, committed to git; the only way content changes |
| Fixed templates | Versioned files in the repo, like prompts; notification templates seeded into `notify.templates` |
| Document Check rules | Config file in the repo |
| Offline unit tests | Small fixture, so tests run without the network |

Not Supabase Storage: that holds customer uploads only.

The articles were moved to the database (`0008`–`0099`); the hard-coded copy was removed in `0102`.
