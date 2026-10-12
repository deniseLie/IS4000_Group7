# DemoBank customer-support chatbot

The locally tested chatbot improvements have been integrated into the main working copy with user approval. The two-customer live Supabase check and further complex-query model testing are deferred. This is a coursework demo, not an official bank product.

## Implemented features

| Area | Before | Current behaviour | Verification |
| --- | --- | --- | --- |
| General help | Login required before any question | Guests can ask general banking questions | Backend and browser passed |
| Long chats | The 16th user question can exceed the backend message limit | Send a bounded recent history and warn when older messages are omitted | 18 turns in browser; 50 turns in frontend unit test |
| Sources | No visible supporting sources | Versioned demo guidance with source buttons | Backend, browser and live model smoke test passed |
| Private details | Chat input can contain common secrets | Withhold common credential/card-number patterns before sending; also check backend input/output | Backend and frontend passed; best-effort filtering only |
| Lost cards | Depends on model output | Immediate fixed guidance; clearly says the bot cannot block a card | Backend, browser, no model needed |
| New chat | No explicit reset | Cancel the current request and clear the conversation | Browser passed, including reset during a response |
| Human support | No usable handoff flow | Editable summary that must be reviewed before copying | Browser passed; no ticket is sent |
| Model response format | Free-form output | Request structured answers, validate source IDs and withhold invalid output | Contract tests and a real browser/model response passed; live reliability remains partial |
| Customer separation | Queries depend on database row policies alone | Verify a unique linked profile and explicitly filter all customer tables | Local tests passed; two linked live customers remain unverified |
| Languages and complex questions | No repeatable acceptance set | Four answer-language choices and 11 synthetic evaluation cases | Sample live checks completed; repeatability and language quality still need broader review |

The source library is small, fictional demo guidance. It is not an approved real-bank knowledge base. Retrieval currently uses keywords, not embeddings or a vector database.

## Validation and limits — 12 October 2026

The 38 backend, 6 frontend and 8 browser checks below were rerun successfully in the main working copy after integration. The production build also passed. Live Supabase/model checks were not rerun in this integration.

- 38 backend tests passed.
- 6 frontend utility tests passed.
- 8 browser integration tests passed with the real frontend and backend and a deterministic test model.
- Earlier isolated testing: 2 separate live browser checks passed: a real model answer with a source; real synthetic login, denial of unlinked personal access, general help and sign-out.
- The production frontend build passed. Desktop and 390-pixel mobile screenshots were inspected.
- Live sample questions covered statements, multiple questions, clarification, loan approval, unknown fees, instruction attacks, typos, Chinese, Malay, Tamil and a corrected follow-up. These are sample observations, not a claim of 100% accuracy. Some runs failed or produced answers needing improvement; these observations do not replace the deferred live checks.

The first browser run caught a React effect error. It was fixed and the suite rerun. Initial live model runs frequently ignored the required JSON format. Structured output improved this, but intermittent provider errors and weak complex answers were still observed. A passing source-ID check does not prove every statement in an answer is supported.

## Deferred checks and remaining work

1. **Two linked live customers have not been tested (deferred).** The supplied service key can manage test authentication users, but requests to the `banking` schema return `42501: permission denied for schema banking`. An authenticated test user can read the schema, sees zero rows while unlinked, and cannot insert a profile. No access policies were changed. A database owner must provide two synthetic linked profiles/accounts and test logins, or a narrowly controlled way to create and remove these fixtures. Do not solve this by disabling row-level security or putting a service key in the browser.
2. **Complex-query model reliability is deferred.** The final two rechecks returned `RateLimitError`. The unverified provider-side minimum source-list constraint was left out of this integration; the backend still rejects answers with no valid sources. Keep structured output and validation. Evaluate a fixed compatible model instead of accepting whichever free model is routed, then repeat the cases and review the answers. No model setting in the user's real `.env` was changed.
3. **Real product facts are missing from public guidance.** Have the project team approve versioned account, loan and card documentation before presenting real-bank fees, rules or contact details. The supplied guidance is labelled fictional.
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

The browser tests start temporary servers on ports 8037 and 5177. They use a test-only model replacement; production application code does not contain a test bypass.

Optional live browser check, with a private root `.env`:

```bash
cd frontend
TEST_ENV_FILE=../.env npm run test:live
```

This uses live services and starts servers on 8038 and 5178. Without `TEST_UNLINKED_FIXTURE_FILE`, the unlinked-user test is skipped. That optional file must contain an array with one synthetic user's `email` and `password`, be stored outside Git with owner-only permissions, and be removed after testing. Test account creation/deletion is deliberately separate from the application.

Optional live answer evaluation:

```bash
cd backend
../.venv/bin/python -m evaluation.run
```

Review `evaluation-results.json` against each case's acceptance check. Recording a response is not an automatic accuracy pass. Use synthetic questions only; do not commit credentials or private conversations.

## Scope still outside this round

Compliance document uploads, OCR, compliance tracking, advisor transfers, meeting booking, saved cross-session conversations and real staffed support are not implemented. The summary feature only prepares a draft for the customer to use. No fine-tuned model was trained.
