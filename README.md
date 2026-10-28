# DemoBank customer-support chatbot

The chatbot improvements, including the banking knowledge and source-check changes, have been integrated into the main working copy with user approval. The two-customer live Supabase check and further complex-query model testing are deferred. This is a coursework demo, not an official bank product.

For the current presentation, use the [ordered demo walkthrough](#demo-walkthrough) and [startup commands](#local-setup).

For another Mac, start with [handoff.md](handoff.md): CLI sign-in, private environment settings, startup, verification and the OpenRouter switch.

## Card self-service update — 27 October 2026

A signed-in customer can now unblock their own temporarily blocked card or cancel it permanently from **Records**, then review and confirm the change in **Actions** (migrations `0068`–`0069`). Both are confirmed actions with a receipt, audit event and in-app notice; cancelled is terminal and the cancel flow warns twice that it cannot be undone. The chat assistant cannot propose or run either change. When an open card verification or dispute handoff exists for the card, the change is noted on it for staff (the handoff stays open). Staff unblock/cancel through `scripts/staff_support.py` is unchanged.

Known limits: unblocking is allowed even after a fraud block, so anyone holding a stolen signed-in session could reverse that block; the demo accepts this trade-off for customer convenience and records it in the staff note and audit trail. Apply `0068`–`0069` to the hosted project before using it there. The browser check (`tests/test_card_changes_browser.py`) needs native PostgREST, like the other native browser suites.

## Workflow 1 Part B update — 27 October 2026

[Workflow 1](docs/workflows/workflow-1-alex.md) Part B is now Alex's "something's wrong with my money" message: an unrecognised $2,400 charge with smaller odd charges before it, a pending rent transfer due Friday, and an app lockout he asks about. A rule finds the SGD 1.00 and 4.99 charges from the same merchant (migration `0064`) and the answer calls it a possible card-testing pattern, not fraud. The urgent section offers two decisions in the same paused reply: the temporary block, and a dispute draft listing all three charges that asks only "Do you still have the card?" (migrations `0066`–`0067`). One case summary built from records and the recorded decisions goes to staff with an urgent handoff. The transfer answer repeats code-computed dates (pending since 17 Oct, due 30 Oct) and says not to resend; the fall-below check notes that the card charge does not touch the savings balance. The lockout is answered as a security step: reset the password in the official app first (migration `0065`).

Known limits: the pattern, due-day and lockout-link rules are bounded English rules, not classifiers. Staff decide refunds and cancellation outside the demo. Free-router answers vary; a section that fails validation is withheld and escalated, while both offers still come from records. Apply `0064`–`0067` to the hosted project before running Part B there. The native Data API and browser suites (`test_workflows.py`, `test_workflow1_browser.py`) need PostgREST, which `scripts.install_postgrest` provides for macOS/Linux only.

## Workflow 1 update — 24 October 2026

[Workflow 1](docs/workflows/workflow-1-alex.md) (Alex: three problems in one message, the urgent one first) now runs end to end in personal v2 mode. Informal wording ("i dont recognise", "$2400") flags fraud and finds card 4821 before any model call; all three sections show at once and the urgent section offers an in-chat temporary block that runs only after **Confirm**, with a receipt. Declining keeps the card active and repeats the urgent steps. Signed-in "Talk to a person" records a support handoff (staff queue) from a summary of topic labels only; guests keep the copy-only draft. Migration `0055` adds fictional DemoBank articles for savings fees, app login recovery, car loans and unrecognised charges.

Known limits: the seeded charge (2026-10-17) is readable until 2027-01-15 (90-day window). After a confirmed demo block, unblock card 4821 with the staff tool (`scripts/staff_support.py`, `card.unblock`) before the next run. Section order, planning and wording still come from the model and are checked only structurally. Apply `0055` to the hosted project before using `KNOWLEDGE_STORE=database` there. The native Data API and browser suites, including `tests/test_workflow1_browser.py`, need a pinned PostgREST binary, which `scripts.install_postgrest` provides for macOS/Linux only. The hosted run of `workflow1.spec.js` (`TEST_PERSONA_FIXTURE_FILE`, `TEST_HOSTED_AI=1`) uses real logins, declines the block and writes conversation, handoff and audit rows for Alex.

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

## Demo walkthrough

Use this order to show the implemented customer workflows. The setup commands in
[Local setup](#local-setup) start the v2 foundation demo at `http://localhost:5173`
with the API at `http://127.0.0.1:5001`. `PIPELINE=v2` is an explicit startup
override; the application's default remains v1. Use the existing private `.env`
with `CHAT_TRANSPORT=api`, `LLM_BASE_URL=https://openrouter.ai/api/v1`,
`LLM_MODEL=openrouter/free`, `KNOWLEDGE_STORE=database`,
`CONVERSATION_STORE=database` and `RUN_WORKER_IN_API=true`. The worker also needs
its restricted `WORKER_DATABASE_URL`. Keep the existing checkpoint signing key.

Normal banking replies use the configured real AI provider. Lost-card guidance,
secret warnings, requests for human support and a guest's personal-balance request
use fixed safety responses. Action receipts and notification templates describe
recorded outcomes without an AI call. Provider failures stay visible; the app does
not replace them with scripted banking answers. The configuration above selects
OpenRouter; [the Codex guide](docs/codex-local-provider.md) describes the explicit
alternative. Neither mode supplies real bank product terms.

The demo uses fictional customers in actual hosted Supabase Auth, database and
Storage services. Use the existing Alex, Michelle and David logins from the private
`personas.json`; passwords and API keys must stay outside the presentation and Git.
Saving, confirming or uploading creates real demo records in the hosted project.
Whenever a step opens Actions for confirmation, select **Review proposal** on the
matching item, check the acknowledgement and select its specific confirmation
button. A saved proposal alone does not execute its requested change.

### 1. Guest help, sources and fixed safety guidance

1. Open the site signed out and leave **Information** on **General help**.
2. Ask: "Does paying only the minimum on my credit card avoid interest?"
3. For a successful model reply, show **AI-generated**, its source controls and
   the source detail panel. Topic cards only fill the composer; press **Send**.
4. Start **New chat**, then send "I lost my card". Show **Safety guidance** and
   the urgent instructions. This fixed route does not currently propose a block.
5. In another new guest chat, ask "What is my balance?" to show the login boundary.
6. Open **Talk to a person**, review the editable summary, then show the copy step.
   Copying the summary does not send a ticket or contact a staffed support queue.

If the provider is unavailable or a reply fails validation, show the explicit error
or withheld answer. A safety response is not evidence that the AI provider works.

### 2. Alex: owned records, personal AI and saved chats

1. Sign in as Alex and open **Records**. Show masked accounts, cards, loans and
   transactions. Change a transaction date/status filter and **Reload records**.
2. Close Records, change **Information** to **My records**, and ask
   "What are my recorded account balances?" Show the personal answer's sources.
3. Use **Reload saved chat**, then **Saved chats**, to reopen the recorded answer.
   Refresh the browser and show that the owned conversation remains available.
4. Sign out before switching personas. Another customer must not see Alex's chats
   or records. This demonstrates scoped demo data, not a connection to a real bank.

Personal retail answers have focused real-provider/browser verification. Approved
bank fees, product rates, loan eligibility and application procedures are still
missing; do not present unsupported product advice as implemented.

### 3. Michelle: compliance case and preparation marks

1. Sign in as Michelle, choose **My records**, and ask
   "What is the current status of my compliance review?"
2. Open **Records** and show the notice, recorded status, deadline, checklist and
   information requests. Compare the answer with these records and its sources.
3. Change one **Your preparation mark** selection and **Save preparation mark**.
4. **Reload records**, then open **Actions** to show the durable receipt.
   Preparation changes must not change submission status or the bank's decision.
5. Open **Inbox** to show the resulting service notification. Mark it read, reload
   the inbox and show that the read state persists.

### 4. Michelle: documents and preliminary checks

The completed native implementation adds **Your compliance review** in chat, per-item
upload/correction, durable notice-based preliminary checks, receipt-backed submission,
grounded AI explanations and action-specific reminders. Setup, scope and verification:
[Michelle workflow](docs/workflows/workflow-2-michelle.md). Migrations `0055`–`0061` and the matching private signing key are activated on the hosted project (2026-10-24); local OCR and the restricted worker are configured. Generic requirements come from the owned notice and approved guidance; policy controls report expiry and date coverage. Upload findings automatically receive a selected-record explanation in saved chat; scoped support uses real handoff receipts.

The existing Records-only technical check remains available:

1. In **Records**, find **Document records**. Use an existing registered demo file
   and select **Run technical file check**.
2. A supported text PDF/DOCX shows extracted-character counts; a PDF also shows
   page count. PNG/JPEG shows decoded dimensions. No extracted text is displayed.
3. This technical-only control reports parser counts. Preliminary notice-based findings
   and optional local OCR are separate controls in **Your compliance review**; neither approves a document.
4. To demonstrate a new upload manually, choose a requested item, select a tiny
   fictional fixture from `frontend/tests/fixtures/documents/`, and select
   **Upload and record file**. Keep the form open until its receipt appears.
5. **Reload records** and show the new owned record, download control and Actions
   receipt. The download link expires after 60 seconds. Uploading does not yet set
   the checklist item to submitted-awaiting-review or approve the document.

### 5. David: advisor details, a private agenda and reviewed sharing

1. Sign in as David. In **Records**, show the currently recorded relationship
   manager. Close Records and open **Data sharing** to show recipient and consent.
2. Select **Prepare meeting agenda**, then **New agenda draft**. Enter one topic
   per line, choose any recorded profile fields to include, and **Save private draft**.
3. Show the saved preview, categories and revision. Saving keeps the draft private;
   **View recorded action** opens its receipt in Actions.
4. Back in Data sharing, select all categories required by this draft, choose a
   duration and **Record consent request**. Refresh the recorded consent status.
5. Select **Review grant**, then **Review in Actions**. Review the stored scope,
   acknowledge the confirmation and confirm. Return and refresh consent status.
6. Open the saved agenda's **Review sharing**, select one active grant that covers
   all draft categories, and **Prepare sharing proposal**. Confirm it in Actions.
7. Show the separate sharing receipt and inbox notification. No real advisor is
   contacted. Editing a draft later does not change its already-shared snapshot.
8. To show revocation, select **Review revocation** for that grant and confirm in
   Actions. Refresh its status. Revocation does not erase an earlier shared copy.

For the read-only Relationship AI demonstration, select **My records** and ask
"Who is my relationship manager?" Show the recorded-assignment source and reload
the saved answer. Requests and appointment suggestions use their own sources.
AI agenda drafting and conversational changes remain pending; use the reviewed
forms for booking, intake, consent and sharing.

### 6. David: appointment review and relationship-request intake

1. In **Data sharing**, open **View requests and appointment times**. Choose a
   date range containing advisor working days and **Refresh recorded status**.
2. Choose a meeting format and **Hold for ten minutes** on an available slot.
   The hold is not a confirmed booking.
3. Select **Review booking**, then **Review in Actions**. Review and confirm the
   stored proposal before it expires. Refresh current appointments to show status.
4. Use **Review cancellation** and confirm in Actions to demonstrate cancellation.
   No external calendar, real meeting or joining link is connected.
5. In the same relationship view, enter a fictional request, urgency and any
   optional deadline/country/currency references. **Prepare request for review**.
6. Open **Review in Actions**, check the exact details, acknowledge and confirm.
   Refresh recorded status to show the request and inspect its receipt in Actions.
   `sent` means recorded demo intake, not fulfilment or delivery to a real advisor.
7. For a second proposal, choose **Reject** to show that it executes nothing.

### 6b. David: Workflow 3 in the chat

Apply migration `0062` first. Sign in as David and open the chat: the new-RM introduction appears from his records.
Follow [the Workflow 3 guide](docs/workflows/workflow-3-david.md): ask about the change, send a request, check request
status, prepare and share a handover note, then book a meeting – each step is confirmed in an in-chat dialog.

### 7. Explain the remaining workflows without claiming completion

The v2 orchestrator implements planning, priority ordering, bounded concurrent
agents, section composition and durable action review. Personal fraud can propose
a demo card block and pause for confirmation. Focused real-provider and UI checks
pass with the explicit Dots model documented below; broader accuracy remains
unverified. Do not use the fixed lost-card response as proof of this flow.
Combined real-provider acceptance, full follow-up evaluation and latency/report
checks remain outstanding. Docker
startup is deferred. The five public agent entries do not mean all five personal
agent workflows are complete.

Staff claim/close/unblock/cancel operations have native integration coverage, but
hosted staff provisioning and MFA are still required; see the invocation examples
in `backend/scripts/staff_support.py`. For a read-only audit-chain demonstration,
run from `backend/` with the configured private environment:

```bash
../.venv/bin/python -m scripts.verify_audit --login-file ../personas.json
```

The checker prints counts/hashes and its result, not customer conversations.
Tracing configuration is described under [Tracing](#tracing). Private document
checks, record forms, receipts and inbox delivery can be demonstrated independently
of model availability. Focused Relationship checks passed against real OpenRouter
on 21 October 2026, including the hosted Computer Use walkthrough. These samples
do not establish broader answer accuracy or continuing provider availability.

## Foundation pipeline

`PIPELINE=v1` keeps the existing answer flow. `PIPELINE=v2` enables the initial
public LangGraph flow: plan, prioritise, dispatch and compose. `KNOWLEDGE_STORE=memory`
uses the approved in-memory library; `KNOWLEDGE_STORE=database` uses the read-only
knowledge RPCs in either pipeline after migrations `0008`, `0009` and `0011` are applied.
The v1 adapter preserves authenticated account context and fixed safety responses;
empty database searches stay empty and database failures never substitute local articles.
Five real-provider domain agents return
validated sections with their own sources and prompt versions. Fixed safety
responses still bypass the model. Personal retail reads, compliance case explanations
and the reviewed fraud proposal workflow are connected; the remaining personal agents and uploaded-document
processing report explicit limitations.

The existing hosted project has migrations `0008`–`0053` applied, with source
checksums and executed SQL recorded in Supabase migration history. Its pre-existing
`0001`–`0007` baseline was checked before deployment; legacy knowledge tables were
preserved. All three existing demo Auth logins pass RLS isolation and the
foundation reader checks; internal `events` remains unexposed. The normal local
app uses hosted saved conversations. `npm run test:hosted` tests this running app
with `TEST_PERSONA_FIXTURE_FILE=../personas.json`; it creates synthetic chats,
private agenda drafts and a reviewed sharing snapshot. The sharing test revokes
its one-day test grant before finishing. `TEST_HOSTED_AI=1` additionally opts into a real provider call.
Credentials remain git-ignored. The restricted hosted worker is configured and
in-app delivery is verified. Staff MFA and the remaining personal agents/record
executors are pending.

Records supports private PDF, DOCX, JPEG and PNG uploads up to 10 MiB. An audited
preparation read checks the review scope; the caller's JWT uploads bytes directly
to Supabase Storage. The `document.register` action verifies the owned object and
records metadata, receipt, audit and notification event atomically. Customers cannot
overwrite or delete stored files. Download links require an owned registered object
and expire after 60 seconds. Uploading does not change the bank's review status or
perform an AI precheck. After an ambiguous upload response, the open form can retry
registration without uploading another copy. Closing the page before registration
can leave a private unregistered object; automatic cleanup/resume is not implemented.
`TEST_HOSTED_DOCUMENTS=1 TEST_PERSONA_FIXTURE_FILE=../personas.json npm run test:hosted -- --grep 'hosted (private documents|upload acknowledgement)'`
uploads the synthetic fixtures to hosted Storage and checks byte-identical downloads,
access isolation, immutable uploads, receipts, notifications and lost-acknowledgement
recovery. Run it only with approval to upload those fixtures to the configured project.

Records also offers a technical file check for owned registered objects. Migration `0053`
retains the existing invoker grants and verifies stored size/MIME before downloading with
the customer JWT. PDF text-layer extraction reports actual page counts; DOCX extracts
body paragraphs/tables without claiming pagination. PNG/JPEG checks decode pixels but
perform no OCR. The credential-free parser child has a five-second wall budget, sampled
256 MiB RSS watchdog and bounded output; format limits include 20 PDF pages, 32,000
characters and 16 million image pixels. The watchdog is not an OS-enforced memory ceiling.
The API returns counts, dimensions and a byte checksum, never extracted text. Contents
are not saved, logged, checkpointed or sent to a model. Results are temporary and do not
change preliminary-check, checklist or bank review status. Approved bank-specific page,
field and OCR-legibility rules for real bank documents are not configured. The separate
Michelle workspace uses the recorded notice/item instructions, flexible-layout real-model interpretation and optional local OCR after migrations `0056`–`0061`. Extracted synthetic text is sent to the configured provider for each check; values are not retained in reports/logs. Unknown or ungrounded requirements cannot pass. Notice changes invalidate earlier verdicts at both read and submission boundaries.
`TEST_HOSTED_INSPECTION=1 TEST_PERSONA_FIXTURE_FILE=../personas.json npm run test:hosted -- --grep 'hosted technical inspections'`
reads the existing four synthetic fixture formats without uploading copies or calling a
model, verifies checksums/mobile layout and rejects Alex/David access to Michelle's files.

The database knowledge adapter validates citation keys, publisher URLs, review
dates and ranked results. Empty searches and missing facts remain empty; no
sample fee or unrelated article is substituted. Exact version/section URLs open
in the existing source dialog, including retired versions marked as no longer
current. Source details require successful audit storage. Native database/API
checks cover retrieval, facts, source details and v2 evidence. Hosted source details
are verified; real-model quality remains a separate check.

Tool authorisation uses explicit rules in `services/authorization.py`. Customer
readers and v2 knowledge retrieval audit their decisions before access. Rules
check the trusted principal, customer scope and backend-assigned agent, and return
masking, confirmation and consent obligations. Staff/system access is unconfigured
and denied. Consent lookup uses JWT/RLS scope, exact purpose/recipient/category
coverage, revocation and wall-clock expiry. Signed-in users can open the
data-sharing view to check basic-profile consent for their currently assigned
manager. This requires migration `0010` on the target database. Registry failures remain errors, and status is released only
after audit succeeds. Migrations `0037`–`0040` add scoped consent requests and
reviewed grant/revocation through the Action Service. **Data sharing** names the
recipient, purpose, categories and fixed expiry; **Actions** requires explicit
customer confirmation before changing a grant. Requests can cover 1–365 days,
and confirmation does not extend that expiry. Revoking one grant leaves other
grants intact and does not erase previously shared records. Registry snapshots
use JWT/RLS plus explicit customer filters, show active grants first and disclose
the 200-row bounds and totals. Writes, receipts, audit and outbox are atomic;
grant/revocation notifications are service records. A grant does not itself share
data. Migrations `0041`–`0042` add private agenda drafts through `agenda.save`:
customers edit 1–12 topics, explicitly select basic profile fields and review the
redacted, server-derived preview. Optimistic revisions prevent lost edits; each
save has an atomic receipt, audit and notification. The data-sharing panel shows
up to 200 current action-created drafts and their total; Actions retains historical
save receipts. Saving never grants consent or shares data. Migrations `0043`–`0044`
add confirmed `rm.share_summary`: the server copies an owned saved revision into
`rm.shared_summaries` under one exact `rm_brief` grant. Review shows the recipient,
content, categories, grant and expiry. Confirmation serialises with grant/revocation
and rechecks the current recipient, saved revision and wall-clock expiry. Separate
partial grants cannot combine. Later draft edits or revocation leave the recorded
sharing snapshot and receipt intact. Native worker checks verify its service
notification; hosted browser checks cover consent, sharing and revocation. AI
drafting and other record executors remain pending. Migration `0045` rejects
incompatible legacy consent categories before a sharing proposal enters the ledger.

Migrations `0048`–`0049` add confirmed relationship-request intake. The form records
a proposal with redacted details, optional deadline/country/currency references and
urgency; the server derives the customer and current recipient. Actions reviews the
exact stored fields before submission or rejection. Confirmation rechecks assignment,
names and the Singapore-date deadline, then atomically records the request, receipt,
audit and notification event. Requests shows its current recorded status. Status
`sent` acknowledges synthetic intake only: no financial action is executed, real
advisor contacted or fulfilment/deadline promised. The hosted test leaves one clearly
labelled synthetic request and its immutable receipt.

The data-sharing panel also opens relationship requests and suggested
appointment times. Migration `0012` calculates thirty-minute suggestions from the
current advisor's hours, blocks, confirmed bookings and unexpired holds. The API
checks the customer's scope and audits each read; no other customer's booking
details are exposed. Native browser checks cover empty and populated records,
invalid dates and registry failures at desktop/mobile sizes. Suggestions do not hold or book an appointment. The explicit **Hold for
ten minutes** control uses migrations `0034`–`0036` to reserve a synthetic slot.
Booking and cancellation require reviewing and confirming a proposal in **Actions**.
SQL rechecks the active assignment, working-hours grid and calendar blocks; a native
GiST exclusion constraint rejects overlapping reservations. Expired holds cannot
confirm and stop blocking new holds. The current-appointments view checks wall-clock
expiry, while receipts describe immutable command outcomes. Business changes,
receipts, audit and outbox commit together; the worker delivers verified booking
notifications. The direct form creates an owned action case without inventing a chat
message or model reply. Reservations are thirty minutes, within 366 days. Native
API/browser checks cover retries, competing customers, changed calendars, expiry,
rejection, rollback and desktop/mobile review. No real calendar, meeting or joining
link is connected. Consent management
is separate from booking; sharing requires its own reviewed action.

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
Guest browser access expires after 24 hours. Hosted persona browser checks verify
saved safety replies across refresh and history clearance on sign-out. Real-provider
checks of saved follow-ups remain pending.

Saved v2 turns also use durable LangGraph snapshots after migrations `0015`/`0016` and
configuration of a private, stable `CHECKPOINT_SIGNING_KEY` (generation command in
`.env.example`). `services/checkpoints.py` scopes the public-key/JWT connector to
the owned turn; its module docstring describes signing and key rotation. Runtime
authentication stays outside graph state. Native tests cover pause/restart/resume,
pending writes, tampering, customer/guest isolation, cancellation and atomic audit
failure. The normal UI also preserves explicit unavailable sections across refresh.
Automatic recovery of interrupted model requests remains pending.

The Action Service (`services/actions/`, migrations `0017`–`0019`) now persists proposals,
customer confirmation/rejection and receipts for temporary synthetic-card blocks and
demo support handoffs. `POST /commands` requires an owned conversation case;
`POST /commands/{id}/confirm` loads the stored target and rechecks policy. Customers
review their latest 50 commands in **Actions**; expired/rejected commands execute
nothing, and failed commands have an explicit failed receipt. Card blocks require
confirmation; support handoffs follow the policy's automatic-action rule. Reusing an
open handoff keeps its identity and emits a reuse event instead of another creation.
SQL executors
keep business changes, ledger, outbox and audit atomic over the public-key/user-JWT
Data API. Python executor modules validate their typed contracts. Native API/browser
checks cover idempotency, concurrency, scope, expiry, changed targets and rollback.
The manual native preview in `tests/preview_actions.py` uses a pre-issued local test
session, not hosted password login. Other record executors are pending. No real bank
card or staffed support service is connected.

Staff handoff and card operations (`scripts/staff_support.py`, migrations `0030`–`0033`)
use the public Data API key and a staff member's verified Auth JWT. SQL requires an
administrator-assigned support role in `app_metadata`, `aal2`, and ownership of the
claimed handoff before closing it or changing a card. Only a temporarily blocked
synthetic card can be reactivated or permanently cancelled. Customer and agent
proposal paths cannot execute staff operations. The queue orders priority then age;
stable command UUIDs retrieve the original receipt after a retry. Staff effects use
the same transactional ledger, audit and outbox, and appear in customer Actions,
Records and Inbox. The CLI module docstring contains setup and invocation details.
Native CLI/SQL/browser checks use locally signed test tokens; hosted staff account
provisioning, MFA and login have not been performed. No real support service or bank
card is connected.

Migrations `0020`–`0022` connect the first personal fraud workflow to these services.
With `PIPELINE=v2` and saved conversations enabled, the fraud agent reads masked card
and transaction evidence and may propose one eligible synthetic card block. The graph
pauses at a signed checkpoint before the UI enables review. Confirm/reject resumes only
post-review nodes, reads canonical outcomes, records a synthetic support handoff and
saves a separate receipt-based section. Cancelling rejects undecided proposals; it does
not reverse previously executed commands. Resume leases exclude competing workers.
Failed continuation preserves its receipts and exposes **Resume saved reply** in Actions.
The local native API/browser tests exercise these mechanics without replacing a model:
their fixtures start after dispatch and use safety-origin text. Real-provider fraud
proposal checks pass with the explicit real OpenRouter model
`dots-studio/dots-3-note-preview:free` and released personal prompt `fraud/v3`.
The focused direct call, saved-chat API review and normal browser check selected the
owned card/transaction pair, paused before confirmation, recorded two receipts and
reopened the saved answer. Computer Use verified the same flow on disposable local
records. One additional manual answer was withheld by source validation; free-router
checks also returned timeouts or invalid structured output. These checks do not prove
general fraud accuracy or reliability of `openrouter/free`. No private provider
setting is changed by these tests. Reproduce the live browser check from `backend/`:

```bash
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 TEST_BROWSER_LOCAL=1 TEST_FRAUD_LIVE=1 \
LLM_MODEL=dots-studio/dots-3-note-preview:free \
../.venv/bin/python -m pytest -q tests/test_fraud_agent_browser.py
```

This sends masked fictional card/transaction evidence to OpenRouter and changes only
disposable local records. For a manual preview, follow `tests/preview_fraud.py`.

V2 withheld sections create one automatic `general_support` handoff per saved,
linked customer case. Its fixed summary contains no rejected model text or customer
records. The existing action service commits the handoff, receipt, audit and outbox
together, and idempotent replay reuses the receipt. The reply preserves every withheld
section and any valid sections; a separate receipt-backed notice directs the customer
to Actions and explains that there is no staffed queue. A failed handoff transaction
fails the reply explicitly. Guests and unsaved public chats retain official-support
guidance because they have no customer-owned action case. V1 is unchanged.
Native graph/API tests cover mixed sections, priority, replay, ownership, rejected-text
exclusion, outbox rollback and retry. Normal browser checks exercise missing real
provider configuration for a saved customer and guest, reload and subsequent safety
messages. Computer Use also verified a personal unavailable-provider reply, automatic
receipt and saved reload on disposable local records. These are failure-path checks,
not AI accuracy evidence.

The personal retail agent reads the caller's real profile, masked accounts/cards,
loans and last 90 days of transactions through permission-first JWT/RLS readers.
Its released personal prompt is `retail/v2`; the public retail prompt stays at v1.
Separate dataset citations retain the captured timestamp. Card and loan currencies
are unrecorded and must not be borrowed from another record. Collections are bounded
to 200 rows and are separate reads, not an atomic statement. The agent is read-only;
provider/validation failures withhold the section. In a saved linked customer case,
the orchestrator records the separate support handoff described above.
Native checks cover all three personas, failed reads, wrong currencies/citations and
action claims. Real OpenRouter account checks pass for all three personas; a real
native browser check verifies saved answers at four viewport widths. Computer Use
also verified David's hosted account answer and saved-chat reload. These focused
checks do not establish broad semantic accuracy. The personal document agent now explains stored preliminary notice-based findings;
public agents and v1 keep their existing behaviour.

The personal Relationship implementation connects the existing owned advisor assignment,
requests, appointments and available-time readers. It provides separate cited snapshots,
up to 20 suggestions in a runtime 14-day Singapore-date window, and no action execution.
Advisor emails stay outside model/checkpoint evidence; recorded contact details are
shown directly in Records. The current/previous advisor distinction, empty collections,
request statuses and reservation states are preserved. Booking, intake, consent and
sharing still use the reviewed forms/Actions flow; AI agenda drafting is pending.
Native integration, source/bounds/privacy validation, checkpoint round trips and explicit
reader/provider failure checks pass. Four focused real OpenRouter checks cover all
three personas' advisor results and David's recorded request status. A separate real-model
browser check verifies citations, saved-chat reload and four viewport widths. Computer Use
also verified David's hosted advisor answer against its source and saved-chat reload.
The current stage passes 645 backend tests (50 opt-in skips), 46 frontend tests, the
production build and three hosted persona smoke checks. These checks do not prove
broad semantic accuracy, multilingual quality or availability of the free provider.
Run the opt-in `TEST_RELATIONSHIP_LIVE=1` direct and browser modules separately,
as described in `tests/test_relationship_agent.py`.

The personal compliance agent uses one audited latest-case snapshot and the separate
case, checklist, history and information-request citations. It excludes internal
assessments and unneeded record IDs from model/checkpoint evidence. Preparation marks
remain distinct from bank submission/review status; the agent cannot edit either.
Customers can save their own preparation marks through Records. Native checks cover
all three personas, missing cases, permission/read failures, unsupported figures and
strict checkpoint round trips. Real OpenRouter checks cover preparation versus bank
acceptance and the normal saved-chat UI at four widths; Computer Use verifies Michelle's
hosted status, source and reload. Run the direct live test and browser suite in separate
processes, as explained in `tests/test_compliance_agent.py`. The public compliance
prompt remains v1; the personal prompt is v2. These samples do not prove general
semantic accuracy or multilingual quality.

The notification foundation (`app/worker.py`, migrations `0023`–`0027`) consumes real
outbox records under a restricted database login. Template delivery, inbox dedupe,
event completion and audit commit together. Expected failures retry five times before
dead-lettering; an unknown database failure stops the worker. Service messages ignore
opt-outs and quiet hours; optional messages honour them. The worker schedules recorded
compliance deadline reminders; the exact window and login setup are in its module docstring.
Run it separately with `python -m app.worker`, or explicitly set `RUN_WORKER_IN_API=true`.
`WORKER_DATABASE_URL` is private and must use the restricted role, never the database
owner or a service-role key. A stopped API-owned worker degrades `/health` to HTTP 503.

Signed-in customers can open **Inbox**, reload their own latest 50 messages and mark
them read. The UI also subscribes to owned Supabase Realtime inbox changes; connection
failures are visible. Native checks cover actual worker/API/UI delivery and explicit
reload, including crash rollback and read persistence. The hosted worker login
passes the runtime privilege check, and a real saved agenda reaches an already-open
inbox through Supabase Realtime. Read acknowledgements use a separate request from
live refreshes so a pushed update cannot cancel the acknowledgement. The hosted
browser check also verifies read persistence: `TEST_HOSTED_WORKER=1 TEST_PERSONA_FIXTURE_FILE=../personas.json npm run test:hosted -- --grep 'hosted worker delivery'`.
It creates one synthetic private agenda draft and marks its notification read.
The hosted reminder producer is verified with no currently due cases; native checks
cover actual due-reminder delivery. English templates are provided; other locales
require approved versions. Email delivery, pg_cron installation and worker-started
AI turns remain pending. Database connection failures stop the worker and remain
visible in health; a transient hosted socket failure was observed during activation.

Customer Data reads (`services/customer_data.py`, migrations `0028`/`0029`) check
permission before opening the caller's JWT Data API client. Invoker RPCs retain RLS
and explicit customer predicates; each snapshot commits its read audit before returning
server call-id/as-of metadata. Required payload fields, masks and customer scope are
validated before release. **Records** in the normal signed-in UI shows accounts,
cards, loans, Singapore-date transaction filters, the latest client-visible compliance
case and assigned advisor. Card/loan currency is currently unrecorded and shown as such.
`/customer/*` is read-only; internal compliance records are never included.
Migrations `0046`–`0047` add `compliance.update_checklist_item` through the existing
Action Service. Records lets customers review and save prepared/not-prepared marks,
with a durable receipt in Actions. Case/item ownership and the previous mark/timestamp
prevent cross-customer changes and stale edits; completed/closed cases reject changes.
Only preparation fields change: bank submission status, case decisions and documents
remain separate. Writes, receipts, audit and notification events commit together.
The hosted checklist browser check saves a synthetic mark, restores the previous mark
and verifies that bank review status remains unchanged.
Native checks exercise all three synthetic personas, permissive-policy regressions,
corrupt records, audit rollback, unavailable RPCs and desktop/mobile UI. These checks
use local signed sessions; hosted password-login browser checks additionally cover
the three personas' own records. This stage does not add personal AI answers for the remaining agents.

Run `npm run test:foundation` from `frontend/` for the v2 browser checks, which
call the real configured provider. Run `TEST_AGENTS_LIVE=1 ../.venv/bin/python -m
pytest tests/test_agents.py -q -k real_domain_agent` from `backend/` to check each
domain agent against that provider. These checks consume provider quota and
fail on invalid or unavailable answers. The normal backend suite skips them.

## Prompt versioning

Prompts live in `backend/app/prompts/<agent>/<version>/`, not in Python. The answer agent uses `prompts/answer/<version>/` (`system.md`, `format.md`, `public_only.md`) and `PROMPT_VERSION` (default `v2`) selects the directory; other agents are selected with `PROMPT_VERSIONS` (JSON, e.g. `{"planner":"v1"}`). Every model call goes through the LLM Gateway (`backend/app/services/llm_gateway.py`): strict JSON check, `LLM_TIMEOUT_S` per attempt, `LLM_MAX_RETRIES` on timeouts/429/5xx only, and a per-request `LLM_REQUEST_TOKEN_BUDGET`. Gateway failures are reported as errors, never replaced with a sample answer. To change wording, copy the latest directory to a new `vN`, edit it and set `PROMPT_VERSION=vN`; do not edit a released version. Each model-generated answer carries `prompt_version` (e.g. `v1+1a2b3c4d`, the version plus a content hash). Fixed no-model responses carry `null`. Evaluation reports record it too, so `--compare` can show before/after for two prompt versions.

## Tracing

Request tracing goes to LangSmith. With `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` set, each `/chat` request is one root `chat` run (inputs: the messages, mode and language; outputs: the reply; metadata: `request_id`, `session_id`, pipeline, route, outcome, `reject_reason`). Nested under it are the steps from `core/observability.span()` (`route`, `retrieve`, `validate`, customer-record reads, consent checks, document inspection, v2 `task` runs), every `llm` call with the LangChain provider run inside it (model, tokens, latency), and the LangGraph nodes. `LANGSMITH_ENDPOINT` must be the API host (`https://apac.api.smith.langchain.com` for APAC, not the web host). Runs carry chat text and synthetic customer records: before upload, `core/langsmith_client.py` redacts card numbers, emails, PINs, OTPs, CVVs, passwords and NRICs in every string. Tests force `LANGSMITH_TRACING=false`.

Kept in the app because LangSmith does not cover them: the `x-request-id` header and one JSON `request` log line per HTTP request (most endpoints make no model call), warning/error log lines, the per-request token budget, and the model-call records the audit log needs. The earlier Supabase trace store is retired: migration `0071` drops `public.log_trace`, and `ops.traces`/`ops.spans` (`0001`) remain read-only history. Known limit: Codex-transport calls appear as the `llm` run with tokens only, since they do not go through LangChain.

## Audit log

`audit.events` (migrations `0005`, `0006`) is the append-only, hash-chained record of who did what (`docs/system-architecture.md` §6). Rows are written only by `audit.record()`; app roles cannot update, delete or truncate it, and each row's `hash` covers the previous row's hash. A generic trigger records insert/update/delete on the business tables in the same transaction (changed column names; values only for enum-like columns). `POST /chat` collects its events in `app/services/audit.py` and writes them in one `public.audit_record_batch` call before the `answer` event; if that write fails the client gets `error` and no answer. Events hold ids, enums and counts only; both the Python helper and the database reject free text. Migration `0054` preserves canonical UUID identifiers even when their suffix contains twelve digits; other long-number strings remain rejected. It is applied and verified in the hosted project. The native registration regression covers the numeric-suffix Storage object id and verifies the audit chain.

Audit writing is always on: it needs `SUPABASE_URL`, `SUPABASE_ANON_KEY` and migration `0005` applied, and a missing configuration or failed write fails the chat request. The only off switch is `AUDIT_STORE=0`, which the unit and browser test runners set so they never add rows to the append-only table; do not set it elsewhere. Check the chain with `python -m scripts.verify_audit` from `backend/` (signs in with `TEST_PERSONA_FIXTURE_FILE`); `--record` saves the head to `docs/evidence/audit-anchor.json`, and later runs fail if older rows were rewritten or the newest removed. Known limits: the table owner can disable the triggers and rewrite the chain (caught only against a recorded anchor); the structural check cannot prove a short token is not personal data; guest events share a 20,000-per-day cap because the anon key is public; every audited write is serialised by one lock.

## API gateway

`app/core/gateway.py` is a pure ASGI middleware in front of every route except `/health` and CORS preflight. It limits the body to `MAX_BODY_BYTES` (64 KiB), verifies the bearer JWT locally against the project's cached JWKS (`ES256`/`RS256` signing keys only; audience `authenticated`; `exp`, `sub` and `session_id` required), applies an in-process token bucket per signed-in user or, for guests, per client address (`RATE_*`, `GUEST_RATE_*`), and builds the `RequestContext`. `customer_id` is resolved from the token's `sub` through the customer's own RLS-scoped profile row, never from the request body. No token means a guest; a bad token is always a 401, never a guest. The first request of a signed-in session records `auth.session_started` through `public.audit_session_started` (migration `0007`, once per JWT `session_id`), and the web client calls `POST /auth/session-end` before sign-out to record `auth.session_ended`. Errors are `{"error": {"code", "message", "request_id"}}` with stable codes (`auth_invalid`, `auth_expired`, `auth_required`, `profile_missing`, `rate_limited` with `Retry-After`, `payload_too_large`, `auth_unavailable`, `audit_unavailable`); clients branch on `code`. Every response carries `x-request-id` (a client value is kept only if it is a UUID). Known limits: rate limits are per process and reset on restart; a token stays usable until it expires even after sign-out; the client address is the direct peer, so behind a proxy all guests share one bucket. Test runners raise the rate limits, as they do with `AUDIT_STORE=0`.

## Database migrations

Migrations live in `backend/supabase/migrations/` as `NNNN_short_name.sql` (four digits, next free number, never renumbered or edited once applied; fix forward with a new file). Apply them in order and record their repository versions in Supabase migration history. `0002` reproduces the `banking` schema that predates the repo, `0003` adds the business schemas (RLS on every table, read-only customer policies), `0004` seeds the synthetic personas Alex, Michelle and David (`docs/synthetic-data-spec.md`), `0005` adds the audit log, `0006` its row-change triggers and `0007` the once-per-session `auth.session_started` function. Create the persona Auth users before `0004`, and add new schemas to the Data API's exposed schemas. `tests/test_rls_live.py` checks per-persona visibility when `TEST_PERSONA_FIXTURE_FILE` is set.

## Local setup

Use Python 3.12 and Node.js 22. Copy `.env.example` to a private root `.env` and fill in the values. Existing environment variables take priority; a backend `.env` takes priority over the root file. Only the public Supabase URL/anonymous key and API URL use the `VITE_` prefix. The application does not need a service-role key.

First-time dependency installation, from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
```

Start the foundation demo backend in one terminal:

```bash
cd backend
PIPELINE=v2 ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 5001
```

In another terminal:

```bash
cd frontend
npm ci  # first install, or after the dependency lockfile changes
VITE_API_URL=/api BACKEND_PORT=5001 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://localhost:5173`. Stop each process with Ctrl+C. The private root
`.env` supplies the configured AI provider, hosted Supabase and worker settings.
Omit `PIPELINE=v2` to use the existing default v1 answer flow.

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

Formal bank document acceptance, advisor transfers, real calendar booking and real staffed support are not implemented. Michelle’s new workspace adds optional local OCR, notice-based preliminary text checks and AI explanations of stored findings; activation and limits are documented in `docs/workflows/workflow-2-michelle.md`. Private synthetic document uploads and technical checks remain available through Records. Synthetic appointments use a hold/review/confirmation flow; agenda drafts and consent-reviewed sharing use the action ledger. No fine-tuned model was trained.

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
