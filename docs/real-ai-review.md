# Real AI review — 13 October 2026

Status: integrated into main with the user's explicit approval on 13 October 2026. Local LLM_MODEL is set to the tested free Nemotron model and PROMPT_VERSION to v2; credentials remain private. The user approved integration and push while the last live-model recheck remains blocked by free-provider quota. This is not a claim of complete live-model verification.

## How the pulled project works

| Part | What it does |
|---|---|
| React / Vite | Opens general help for guests; offers Supabase sign-in for synthetic personal records. Chat.jsx sends bounded, redacted history and displays streamed events, sources and the support draft. |
| FastAPI | Validates requests, verifies tokens when supplied, loads personal records only in personal mode and returns SSE events. |
| Supabase | Authenticates users; banking queries filter by linked customer ID as well as relying on row-level policies. Product catalogues are read separately. |
| Knowledge | Twelve small guidance articles, including four Singapore summaries. Keyword retrieval, publisher links and expiry checks; no vector database. |
| LangChain / OpenRouter | Calls a real model with conversation history and available evidence, requesting structured JSON. The pull replaced the old SDK wrapper with LangChain. |
| Validation | Checks source IDs, numeric values, credential leaks, URLs and claims of completed banking actions. It cannot prove every sentence is supported. |
| Prompts / traces | The pull added immutable prompt versions, request spans and optional Supabase trace writes. This update keeps these features and adds prompt v2. |
| Tests / evaluation | Unit checks, browser workflows and evaluation reports. The old browser server returned article text instead of calling the model; this update removes it. |

The multi-capability architecture document is a proposal. The analytics dashboard, human-operated support queue, saved conversations, compliance workflows and meeting booking are not implemented. The legacy ChatWindow.jsx and api/chat.js are not used by the current App.jsx.

## What was wrong and what changes

| Before | Current implementation |
|---|---|
| The browser-test server could return an article as an answer. | Delete that server. Both browser test commands launch the normal backend and call the real provider. |
| Tests and offline evaluation replaced the model with fixed answers. | Test sample strings directly through validators. No code replaces the model with an answer generator. |
| No keyword match searched all earlier questions and favoured old topics. | Supply the small current library and let the real model interpret the latest message in context. |
| Greetings and brief social replies needed banking citations. | Separate conversational replies from banking facts. Conversation has no sources or figures; banking answers still require sources. |
| Internal instructions appeared as customer text. | Prompt v2 tells the model to write directly to the customer and answer the current message. |
| A source-check rejection immediately produced a generic message. | Ask the real model to revise once; validate again. Never substitute a stored article or a fake answer. |
| The UI did not show how an answer was produced. | Label AI-generated answers, fixed safety guidance and withheld answers separately. |
| The free router produced inconsistent replies in this focused check. | Test a specific free model, nvidia/nemotron-3-super-120b-a12b:free, with the existing API key. |

## Verification

Final results and the live responses are saved in the local test reports. These are focused checks, not a claim that every possible question is correct.

- 79 backend checks passed, including direct output validation, source checks, privacy, authentication boundaries, history limits, prompt construction and trace handling.
- 6 frontend utility tests passed.
- Frontend production build passed.
- After integration on main, all 6 browser workflows that do not consume AI quota passed with the normal backend. Backend, frontend utility and offline validator checks also passed again on main.
- 17 offline validator / retrieval / safety cases passed. No model answers were simulated in this run.
- Before integration, the final live browser run: 9 passed, 1 failed because the provider returned an error, 1 optional login check skipped. Seven actual model responses on prompt v2+88f886d0 were inspected; the card / abuse / apology, fixed-deposit / follow-up and greeting / off-topic workflows passed. The last minimum-payment question still needs a live recheck. OpenRouter quota lookup showed 56 used against a limit of 50 and 0 remaining. An earlier prompt run had 10 passes, but manual review found an extra unsupported sentence, so it is not evidence that the final version is fully verified.

The initial browser observer failed when reading a closed SSE response; it now checks the visible UI and provenance attributes. Manual review also caught repeated card troubleshooting, incorrect bank-policy attribution, and extra payment advice absent from the supplied source. The prompt and regression checks were updated; earlier failures are not counted as passes.

## Still deferred or limited

- Two linked live Supabase customers have not been tested. The optional unlinked-login browser case is skipped without its explicit fixture; no customer accounts or database permissions were changed.
- Broad complex-query model reliability remains deferred. This round checks only the reported conversation failure and a small set of related English interactions.
- Multilingual quality, semantic source correctness across all questions, and free-provider availability are not guaranteed. A model can misclassify a reply; local checks are not a proof of truth.
- Fixed safety guidance for secrets and lost cards remains intentional and is labelled separately from AI output.
- The UBS-inspired UI mockup is not integrated in this change.

## Integration scope

The code, prompt v2, tests and documentation are integrated. LLM_MODEL and PROMPT_VERSION were changed only in the private local environment and the public example files. No banking records, database migrations or permissions were changed. The UBS-inspired UI redesign is outside this commit.

## Remaining live recheck

After free quota is available, run `TEST_ENV_FILE=../.env npm run test:e2e -- tests/live.spec.js` from frontend and review the actual answers, especially the minimum-payment case. Do not substitute a fake model or count a provider failure as a passing test. The two earlier deferred checks remain deferred.
