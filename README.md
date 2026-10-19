# DemoBank customer-support chatbot

The chatbot improvements, including the banking knowledge and source-check changes, have been integrated into the main working copy with user approval. The two-customer live Supabase check and further complex-query model testing are deferred. This is a coursework demo, not an official bank product.

For another Mac, start with [handoff.md](handoff.md): CLI sign-in, private environment settings, startup, verification and the OpenRouter switch.

## Real AI update — 13 October 2026

Local update — 14 October: [Codex subscription setup](docs/codex-local-provider.md)
adds `CHAT_TRANSPORT=codex` for real GPT-5.6 Luna requests through the signed-in CLI.
Set `CHAT_TRANSPORT=api` and restart the backend to use the preserved OpenRouter
configuration. This switch does not require changing the browser. See that guide
for current browser evidence, including the Chinese timeout and successful rerun.

See [the review](docs/real-ai-review.md) for the code map, test evidence and remaining limitations. This update removes the fake browser model and all answer-generating test replacements. Offline checks feed sample strings directly to validators; browser checks use the normal backend and real provider. The application labels AI-generated replies separately from fixed safety guidance and withheld answers.

The ongoing [core completion checklist](docs/core-completion-plan.md) tracks the
next work on stories 6, 13–18. Local source checks now reject changed explicit
currency/percentage pairs and some external-policy attribution errors. Live
verification of the focused conversation and minimum-payment cases now passes
with Luna. OpenRouter still needs a separate recheck. The two earlier deferred
checks remain deferred.

The [14 October results review](docs/core-results-review.md) closes the focused
Luna round. It records an evaluation bug and its fix, 151 backend tests, 19 offline
checks, reviewed real answers and the remaining backlog limits. Run only the
small source-check set with `python -m evaluation.run --live --suite focused` from
`backend/`; the standard live set still includes deferred complex scenarios.

The tested model is `nvidia/nemotron-3-super-120b-a12b:free` on OpenRouter. To use the same setup, set `LLM_MODEL` to that value and `PROMPT_VERSION=v2` in your private environment, preserving the existing API key and URL. A provider error remains an error; no stored answer is substituted. One source-check failure can request one real-model revision. This adds latency and another provider request when used.

Conversation mode permits short greetings, clarifications without facts, and off-topic redirection without a banking citation. Banking facts still require sources and figure validation. These are basic checks, not a semantic guarantee; human review remains necessary. The two previously deferred checks remain deferred.

The validation figures dated 12 October below are historical, including the old deterministic browser results. Use the real AI review for the latest results.

## Interface refresh — 14 October 2026

The working React app now uses the UBS-inspired concept with DemoBank's own name
and no UBS logo: neutral surfaces, red accents, responsive topic cards and matching
sign-in, source and support panels. Suggestions fill the composer; they never
substitute a stored response. Keyboard focus stays inside open panels, Escape
closes them, and focus returns to the trigger. See [UI verification](docs/ui-refresh.md)
for current desktop/mobile and real-provider test evidence.

## Implemented features

| Area | Before | Current behaviour | Verification |
| --- | --- | --- | --- |
| General help | Login required before any question | Guests can ask general banking questions | Backend and browser passed |
| Long chats | The 16th user question can exceed the backend message limit | Send a bounded recent history and warn when older messages are omitted | 18 turns in browser; 50 turns in frontend unit test |
| Sources | Only fictional demo guidance | Four Singapore education summaries with original links, publishers and review dates, alongside demo guidance | Local backend and browser checks passed; live model recheck deferred |
| Private details | Chat input can contain common secrets | Withhold common credential/card-number patterns before sending; also check backend input/output | Backend and frontend passed; best-effort filtering only |
| Lost cards | Depends on model output | Immediate fixed guidance; clearly says the bot cannot block a card | Backend, browser, no model needed |
| New chat | No explicit reset | Cancel the current request and clear the conversation | Browser passed, including reset during a response |
| Human support | No usable handoff flow | Editable summary that must be reviewed before copying | Browser passed; no ticket is sent |
| Model response format | A valid source ID can accompany an invented figure | Validate source IDs and reject figures absent from the cited evidence | Local regression checks passed; meaning and attribution still need review |
| Customer separation | Queries depend on database row policies alone | Verify a unique linked profile and explicitly filter all customer tables | Local tests passed; two linked live customers remain unverified |
| Languages and complex questions | No repeatable acceptance set | Four answer-language choices and 11 synthetic evaluation cases | Sample live checks completed; repeatability and language quality still need broader review |

The library contains eight demo guidance articles and four general Singapore banking summaries from MoneySense and the Association of Banks in Singapore. General education is labelled separately from DemoBank rules. It does not supply real product fees or rates. Retrieval uses keywords, not embeddings or a vector database.

## Validation and limits — 12 October 2026

The knowledge/source-check changes were tested in an isolated copy before integration and the checks below passed again in the main working copy. No live Supabase or model checks were run in this round.

- 63 backend tests passed.
- 6 frontend utility tests passed.
- 10 browser integration tests passed with the real frontend and backend and a deterministic test model.
- Earlier isolated testing: 2 separate live browser checks passed: a real model answer with a source; real synthetic login, denial of unlinked personal access, general help and sign-out.
- The production frontend build passed. Desktop and 390-pixel mobile screenshots were inspected.
- Live sample questions covered statements, multiple questions, clarification, loan approval, unknown fees, instruction attacks, typos, Chinese, Malay, Tamil and a corrected follow-up. These are sample observations, not a claim of 100% accuracy. Some runs failed or produced answers needing improvement; these observations do not replace the deferred live checks.

The first browser run caught a React effect error. It was fixed and the suite rerun. Initial live model runs frequently ignored the required JSON format. Structured output improved this, but intermittent provider errors and weak complex answers were still observed. A passing source-ID check does not prove every statement in an answer is supported.

## Deferred checks and remaining work

1. **Two linked live customers have not been tested (deferred).** The supplied service key can manage test authentication users, but requests to the `banking` schema return `42501: permission denied for schema banking`. An authenticated test user can read the schema, sees zero rows while unlinked, and cannot insert a profile. No access policies were changed. A database owner must provide two synthetic linked profiles/accounts and test logins, or a narrowly controlled way to create and remove these fixtures. Do not solve this by disabling row-level security or putting a service key in the browser.
2. **Complex-query model reliability is deferred.** The final two rechecks returned `RateLimitError`. The unverified provider-side minimum source-list constraint was left out of this integration; the backend still rejects answers with no valid sources. Keep structured output and validation. Evaluate a fixed compatible model instead of accepting whichever free model is routed, then repeat the cases and review the answers. No model setting in the user's real `.env` was changed.
3. **Real product facts are missing from public guidance.** General Singapore education now has primary source references. Bank-specific fees, rates, eligibility and contact details still require approved product documentation. These are not supplied by the new general summaries.
4. **Language and safety testing needs expansion.** Fixed safety messages and interface labels are English. The language choice applies to generated answers. Pattern filtering is not comprehensive privacy protection or a guarantee against prompt injection.

One temporary synthetic authentication user was created for live verification, then deleted and verified absent. No banking rows could be created, no existing customer records were changed, and no database grants were changed.

## Examples

**Long chat**

Before: the next question after 15 exchanges can fail request validation.

After: the 18th question completes. The customer sees a notice if older context is no longer sent.

**Lost card**

Customer: “I lost my card. Please block it.”

After: the assistant directs the customer to contact their bank immediately through its official app or website, suggests an official freeze/block control if available, and says that this chatbot cannot block a card.

**Card problem**

Customer: “My card is not working.”

Expected answer: ask whether the problem happened at an ATM, online or in a shop, and what error appeared. Do not promise to inspect an account in guest mode. A live recheck produced this clarification, but broader repeatability is still pending.

**Human support**

After: the customer reviews and edits a draft, then copies it. The page explicitly says that copying does not send a ticket or contact staff.

## Foundation pipeline

`PIPELINE=v1` keeps the existing answer flow. `PIPELINE=v2` enables the initial
public LangGraph flow: plan, prioritise, dispatch and compose. `KNOWLEDGE_STORE=memory`
uses the approved in-memory library; `KNOWLEDGE_STORE=database` uses the read-only
knowledge RPCs after migrations `0008`, `0009` and `0011` are applied. Five real-provider domain agents return
validated sections with their own sources and prompt versions. Fixed safety
responses still bypass the model. Personal data, uploaded documents, persisted
checkpoints and confirmed actions are not yet connected to v2; unavailable
operations are reported explicitly. Supabase migration and live persona work
remain pending.

The database knowledge adapter validates citation keys, publisher URLs, review
dates and ranked results. Empty searches and missing facts remain empty; no
sample fee or unrelated article is substituted. Exact version/section URLs open
in the existing source dialog, including retired versions marked as no longer
current. Source details require successful audit storage. Native database/API
checks cover retrieval, facts, source details and v2 evidence; hosted deployment
and live AI/browser verification of database-backed answers remain pending.

Tool authorisation uses explicit rules in `services/authorization.py`. Customer
readers and v2 knowledge retrieval audit their decisions before access. Rules
check the trusted principal, customer scope and backend-assigned agent, and return
masking, confirmation and consent obligations. Staff/system access is unconfigured
and denied. Consent lookup uses JWT/RLS scope, exact purpose/recipient/category
coverage, revocation and wall-clock expiry. Signed-in users can open the read-only
data-sharing view to check basic-profile consent for their currently assigned
manager. This requires migration `0010` on the target database; hosted application
is still pending. Registry failures remain errors, and status is released only
after audit succeeds. Grant/revoke, sharing and transactional action enforcement
remain pending; the unused action stubs must not be connected to live workflows.

The data-sharing panel also opens read-only relationship requests and suggested
appointment times. Migration `0012` calculates thirty-minute suggestions from the
current advisor's hours, blocks, confirmed bookings and unexpired holds. The API
checks the customer's scope and audits each read; no other customer's booking
details are exposed. Native browser checks cover empty and populated records,
invalid dates and registry failures at desktop/mobile sizes. Hosted application
is pending. Suggestions do not hold or book an appointment.

Conversation storage APIs and migrations `0013`/`0014` provide owned session creation,
recent-chat listing, bounded chronological history and cancellation of interrupted
turns. Customers retain RLS protection; guests use a separate hashed access token
with a 24-hour expiry. Starting a turn stores its redacted question; finishing
stores a validated reply or explicit failure. Each transition and audit event
commit together, with revision conflicts and idempotency checks. Native tests
cover these boundaries. `CONVERSATION_STORE=database` connects `/chat` and the
saved-chat UI after these migrations are applied; the default `off` preserves
temporary browser history. Saved chats load server-owned history, reject stale
revisions, preserve sources and expose explicit cancellation of pending replies.
Guest browser access expires after 24 hours. Hosted application and real-provider
checks of saved follow-ups remain pending.

Saved v2 turns also use durable LangGraph snapshots after migrations `0015`/`0016` and
configuration of a private, stable `CHECKPOINT_SIGNING_KEY` (generation command in
`.env.example`). `services/checkpoints.py` scopes the public-key/JWT connector to
the owned turn; its module docstring describes signing and key rotation. Runtime
authentication stays outside graph state. Native tests cover pause/restart/resume,
pending writes, tampering, customer/guest isolation, cancellation and atomic audit
failure. The normal UI also preserves explicit unavailable sections across refresh.
This establishes checkpoint persistence; customer-facing action confirmation and
automatic recovery of interrupted requests still require the Action Service.

Run `npm run test:foundation` from `frontend/` for the v2 browser checks, which
call the real configured provider. Run `TEST_AGENTS_LIVE=1 ../.venv/bin/python -m
pytest tests/test_agents.py -q -k real_domain_agent` from `backend/` to check each
domain agent against that provider. These checks consume provider quota and
fail on invalid or unavailable answers. The normal backend suite skips them.

## Prompt versioning

Prompts live in `backend/app/prompts/<agent>/<version>/`, not in Python. The answer agent uses `prompts/answer/<version>/` (`system.md`, `format.md`, `public_only.md`) and `PROMPT_VERSION` (default `v2`) selects the directory; other agents are selected with `PROMPT_VERSIONS` (JSON, e.g. `{"planner":"v1"}`). Every model call goes through the LLM Gateway (`backend/app/services/llm_gateway.py`): strict JSON check, `LLM_TIMEOUT_S` per attempt, `LLM_MAX_RETRIES` on timeouts/429/5xx only, and a per-request `LLM_REQUEST_TOKEN_BUDGET`. Gateway failures are reported as errors, never replaced with a sample answer. To change wording, copy the latest directory to a new `vN`, edit it and set `PROMPT_VERSION=vN`; do not edit a released version. Each model-generated answer carries `prompt_version` (e.g. `v1+1a2b3c4d`, the version plus a content hash). Fixed no-model responses carry `null`. Evaluation reports record it too, so `--compare` can show before/after for two prompt versions.

## Tracing

Each `/chat` request is recorded as one trace: a session row plus timed spans (`route`, `retrieve`, `customer_context`, `llm`, `validate`). Only ids, counts, flags, enums, timings and token/cost numbers are stored, never chat text or customer records. A named `reject_reason` explains every withheld answer. Every request also writes one JSON `answer` log line.

Storage is opt-in: set `TRACE_STORE=1` (needs `SUPABASE_URL` and `SUPABASE_ANON_KEY`). The backend writes through the `public.log_trace` function from `backend/supabase/migrations/0001_trace_tables.sql`; the `ops.traces` and `ops.spans` tables themselves are closed to the public key by RLS. A failed or slow write is logged and never affects the chat. Unit and browser tests force `TRACE_STORE=0`. Known limits: the anon key is public, so anyone could add junk rows through `log_trace` (bounded by validation and a 10,000-per-day cap); model retries inside the provider client are not visible; cost is only as accurate as `LLM_PRICE_*_PER_M`. See `docs/tracing-plan.md`.

## Audit log

`audit.events` (migrations `0005`, `0006`) is the append-only, hash-chained record of who did what (`docs/system-architecture.md` §6). Rows are written only by `audit.record()`; app roles cannot update, delete or truncate it, and each row's `hash` covers the previous row's hash. A generic trigger records insert/update/delete on the business tables in the same transaction (changed column names; values only for enum-like columns). `POST /chat` collects its events in `app/services/audit.py` and writes them in one `public.audit_record_batch` call before the `answer` event; if that write fails the client gets `error` and no answer. Events hold ids, enums and counts only; both the Python helper and the database reject free text.

Audit writing is always on: it needs `SUPABASE_URL`, `SUPABASE_ANON_KEY` and migration `0005` applied, and a missing configuration or failed write fails the chat request. The only off switch is `AUDIT_STORE=0`, which the unit and browser test runners set so they never add rows to the append-only table; do not set it elsewhere. Check the chain with `python -m scripts.verify_audit` from `backend/` (signs in with `TEST_PERSONA_FIXTURE_FILE`); `--record` saves the head to `docs/evidence/audit-anchor.json`, and later runs fail if older rows were rewritten or the newest removed. Known limits: the table owner can disable the triggers and rewrite the chain (caught only against a recorded anchor); the structural check cannot prove a short token is not personal data; guest events share a 20,000-per-day cap because the anon key is public; every audited write is serialised by one lock.

## API gateway

`app/core/gateway.py` is a pure ASGI middleware in front of every route except `/health` and CORS preflight. It limits the body to `MAX_BODY_BYTES` (64 KiB), verifies the bearer JWT locally against the project's cached JWKS (`ES256`/`RS256` signing keys only; audience `authenticated`; `exp`, `sub` and `session_id` required), applies an in-process token bucket per signed-in user or, for guests, per client address (`RATE_*`, `GUEST_RATE_*`), and builds the `RequestContext`. `customer_id` is resolved from the token's `sub` through the customer's own RLS-scoped profile row, never from the request body. No token means a guest; a bad token is always a 401, never a guest. The first request of a signed-in session records `auth.session_started` through `public.audit_session_started` (migration `0007`, once per JWT `session_id`), and the web client calls `POST /auth/session-end` before sign-out to record `auth.session_ended`. Errors are `{"error": {"code", "message", "request_id"}}` with stable codes (`auth_invalid`, `auth_expired`, `auth_required`, `profile_missing`, `rate_limited` with `Retry-After`, `payload_too_large`, `auth_unavailable`, `audit_unavailable`); clients branch on `code`. Every response carries `x-request-id` (a client value is kept only if it is a UUID). Known limits: rate limits are per process and reset on restart; a token stays usable until it expires even after sign-out; the client address is the direct peer, so behind a proxy all guests share one bucket. Test runners raise the rate limits, as they do with `AUDIT_STORE=0`.

## Database migrations

Migrations live in `backend/supabase/migrations/` as `NNNN_short_name.sql` (four digits, next free number, never renumbered or edited once applied; fix forward with a new file). Apply them in order; the hosted project records its own timestamp per migration. `0002` reproduces the `banking` schema that predates the repo, `0003` adds the business schemas (RLS on every table, read-only customer policies), `0004` seeds the synthetic personas Alex, Michelle and David (`docs/synthetic-data-spec.md`), `0005` adds the audit log, `0006` its row-change triggers and `0007` the once-per-session `auth.session_started` function. Create the persona Auth users before `0004`, and add new schemas to the Data API's exposed schemas. `tests/test_rls_live.py` checks per-persona visibility when `TEST_PERSONA_FIXTURE_FILE` is set.

## Local setup

Use Python 3.12 and Node.js 22. Copy `.env.example` to a private root `.env` and fill in the values. Existing environment variables take priority; a backend `.env` takes priority over the root file. Only the public Supabase URL/anonymous key and API URL use the `VITE_` prefix. The application does not need a service-role key.

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Keep root `PORT` consistent with the backend command. Use `VITE_API_URL=/api` for the Vite development proxy. A production host must also proxy `/api` to the backend, or supply a public API URL and configure CORS. Vite preview alone does not provide the backend.

The provider/model must support strict JSON-schema responses. The implementation requests this using [OpenRouter's structured-output format](https://openrouter.ai/docs/guides/features/structured-outputs) and still validates the response locally. The free router is suitable for experimentation; the live checks do not establish production reliability.

## Run checks

```bash
cd backend
../.venv/bin/python -m pytest -q
```

```bash
cd frontend
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

The browser tests start the normal backend on 8038 and frontend on 5178. Both `test:e2e` and `test:live` use the real configured AI provider. They consume provider quota and fail if it cannot answer. There is no fake model server or simulated AI fallback. Set `TEST_ENV_FILE=../.env` when needed. Safety-only browser tests can be run without model calls using `npm run test:e2e -- tests/chat.spec.js`.

Optional live browser check, with a private root `.env`:

```bash
cd frontend
TEST_ENV_FILE=../.env npm run test:live
```

This uses live services and starts servers on 8038 and 5178. Without `TEST_UNLINKED_FIXTURE_FILE`, the unlinked-user test is skipped. That optional file must contain an array with one synthetic user's `email` and `password`, be stored outside Git with owner-only permissions, and be removed after testing. Test account creation/deletion is deliberately separate from the application.

Offline evaluation (no model or database calls):

```bash
cd backend
../.venv/bin/python -m evaluation.run --output evaluation-results.json
```

This writes JSON and HTML reports. It checks sample strings directly through validators, plus retrieval and fixed safety paths. It does not replace the AI client or measure live model accuracy. The new mode is `offline_validator_checks`, so it cannot be compared automatically with the old simulated-answer reports. The historical [before](backend/evaluation/before.html) and [after](backend/evaluation/after.html) reports used the old simulated-answer method with the same 17 cases: 10 passed before, 17 passed after. Four improvements cover missing banking sources; three cover invented figures. Reports include dataset, knowledge and implementation hashes.

Compare future runs using the same dataset and mode:

```bash
../.venv/bin/python -m evaluation.run --output evaluation-results.json --compare previous-validator-results.json
```

Optional live answer evaluation (deferred; explicit opt-in required):

```bash
cd backend
../.venv/bin/python -m evaluation.run --live
```

Review `evaluation-results.json` against each case's acceptance check. Every successful live response is marked `needs_human_review`, never an automatic accuracy pass. Review all parts of complex questions, relevant citations, factual meaning, currencies, corrections and the requested language. Use synthetic questions only; do not commit credentials or private conversations.

## Scope still outside this round

Compliance document uploads, OCR, compliance tracking, advisor transfers, meeting booking and real staffed support are not implemented. The summary feature only prepares a draft for the customer to use. No fine-tuned model was trained.

## Knowledge maintenance and limits

Primary pages checked on 12 October 2026:

- [MoneySense: bank accounts](https://www.moneysense.gov.sg/understanding-bank-accounts/)
- [MoneySense: credit cards](https://www.moneysense.gov.sg/understanding-credit-cards/)
- [MoneySense: loan types](https://www.moneysense.gov.sg/understand-the-types-of-loans/)
- [ABS: card disputes](https://www.abs.org.sg/consumer-banking/consumers/credit-card-chargeback-dispute-resolution)

To update knowledge, read the original page, revise the short summary in `backend/app/services/knowledge.py`, update its version and checked date, and choose a review date. The current project review interval is three months. Run the backend, offline evaluation and browser checks after edits. Have a teammate review the summary and its scope. The dates record this project's checks; they do not claim endorsement by the publisher.

Expired external articles, invalid dates and references outside the allowed HTTPS domains are excluded from retrieval, including non-English requests. Old source details remain readable and show a review warning. There is no automatic website monitoring or scheduled source refresh.

The figure check compares numeric values against cited evidence only. It rejects new calculations and unsupported figures, but cannot prove currency, attribution, meaning, written-out numbers or translations. A valid source can still accompany a misleading claim. The interface therefore explains that citations do not guarantee correctness. Live answer quality and manual review remain separate from offline regression results.

Before: a fixed test answer saying “Your annual fee is SGD 199” with the valid `cards` ID was accepted.

After: that answer is withheld because its figure is absent from the cited guidance. The user is directed to rephrase or use the bank's official support channel.
