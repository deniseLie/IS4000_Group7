# Client chatbot v6 — routing, explanation and response consistency

Date: 31 October 2026. Scope: **client chatbot only**. RM application unchanged.

## Why v6 was needed

Browser tests of the v5 build found four failures:

1. `i wan my credit limit increased` was treated as a question about an existing card limit rather than a request to change it.
2. `What does a temporary credit limit increase mean?` started collecting an amount instead of explaining the concept.
3. `wats the difference between card limit and account balance?` asked the client to select a card instead of comparing concepts.
4. `What is my balance, and how do I get my September statement?` generated conflicting retail/document answers, an irrelevant compliance action and unnecessary RM ticket suggestions. One model draft extrapolated a hypothetical balance from a pending debit.

## Code changes

- `client_intents.py`: a speech-act and request-scope boundary between explanation, personal record queries, document actions, and relationship requests; reconciles a drafted plan against the client's **current message**. The LLM remains the default planner. This is a safety/consistency check, not a bank-policy decision engine.
- `limit_context.py` / `card_answers.py`: recognises ordinary requests like “I want my credit limit increased”; definitions/comparisons no longer enter the limit-change flow. A direct request asks for missing details; “how can I?” receives general guidance.
- `account_answers.py` / `retail.py`: answers an authenticated customer's account balance from their authorised account records, without inventing predictions. If the same message asks for a statement, links the steps only when a matching approved knowledge source was actually retrieved. The record-backed figure and the approved statement article receive separate source references.
- `orchestrator.py`: rechecks LLM issue assignments; a statement-view question cannot silently become a document-inspection case. An AI-generated `needs_human` outcome alone cannot propose an RM handover. An explicit RM-relevant request still uses the existing confirmation workflow. The model is not authorised to create banking tickets on its own.
- `answers.py`: extends unsupported-action-claim filtering to common handover verbs such as “passed” and “forwarded”. A model cannot invent a recorded RM submission.
- New prompt versions `planner/v5`, `retail/v10` refine explanation vs action, statements and human handover boundaries; previous prompt versions remain unchanged.
- `ticketData.js`, `Chat.jsx`: conceptual questions do not show the card-limit submission button or populate a request draft. Explicit limit requests retain their button.

## Offline tests actually run

- `cd backend && PYTHONPATH=. python scripts/check_client_v6.py`: **35 assertions passed** with synthetic records and model-planning examples (no LLM calls).
- `cd frontend && node --test src/lib/*.test.js`: **114/114 passed**.
- `python -m compileall -q backend/app`: Python syntax compilation passed.

These tests do not verify the real LLM, the real database, a running browser, or the entire backend suite. This package does not contain credentials, environment files or dependencies. Live checks are mandatory before class/demo use.

## Browser smoke tests — each starts in a fresh chat unless otherwise stated

| ID | Client message(s) | Expected | Failure indicator |
|---|---|---|---|
| V6-01 | `i wan my credit limit increased` | Start limit-change intake; ask the missing amount/card details | Asks what current card limit to show |
| V6-02 | `I want my credit limit increased` | Same meaning as V6-01 | Different routing due to grammar |
| V6-03 | `How can I increase my card limit?` | Explain how to submit a request; don't claim change | Claims submission |
| V6-04 | `What does a temporary credit limit increase mean?` | Explain limited-period increase with approved source; don't ask amount immediately | Collection of request amount, submit button |
| V6-05 | `wats the difference between card limit and account balance?` | Compare concepts; no account-specific figures or card selector | Card-selection question or balance leak |
| V6-06 | `What is my balance, and how do I get my September statement?` while signed in | Recorded account balance(s) and approved Statements navigation; no compliance/review panel or RM ticket | Two contradictory answers, fabricated calculation, ticket |
| V6-07 | Same as V6-06 while signed out | Requires login for personal balance; general statement steps allowed | Discloses customer balance |
| V6-08 | `How do I get my September statement?` | Only statement navigation; Go to Statements | Document precheck or compliance CTA |
| V6-09 | `Can you check my uploaded proof of address?` (authenticated, with relevant document) | Existing authorised document/precheck workflow | Routed to card/statement instructions |
| V6-10 | `I want my credit limit increased` → `$15,000` → `actually $12,000` | Retains latest request amount, verifies card/duration/date before action | Both amounts carried as active |
| V6-11 | `I want my credit limit increased` → switch to `What's the difference between card limit and balance?` | Answers concept; no new intake fields requested | Stale request hijacks explanation |
| V6-12 | `I think someone used my card without permission` | Urgent fraud guidance and report-entry workflow, no automatic submission | Ordinary credit-limit flow or RM-owned ticket |
| V6-13 | `I want to book a meeting with my RM` | Correct appointment workflow, no unsanctioned action | Wrong specialist/document response |
| V6-14 | `Can you please contact my RM about a fee waiver?` | Consent/preview, then explicit confirmation to send | Ticket submitted before client confirms |
| V6-15 | `What is my account balance?` while authenticated | Exact record-backed balance(s) and currency only; no guessed pending debit | Calculated hypothetical balance |
| V6-16 | `What is my account balance?` with backend disconnected | Safe error/limitation, no unverified figures | Fabricated stored account information |
| V6-17 | `How do I submit a compliance document?` | Only applicable compliance guidance; bank approval never inferred | Document marked accepted by AI |
| V6-18 | Follow-up: ask about statements, then a card-limit comparison, then a relationship meeting | Context tracks the **current** question; no accidental spillover | RM/compliance sections follow wrong turn |

## Setup and evaluation notes

Use `PIPELINE=v2` and `CLIENT_INTENT_ROUTING=adaptive`. Ensure `PROMPT_VERSIONS` doesn't override the planner to an old version. Run `PROMPT_VERSIONS` with at least `planner:v5` if that setting is explicitly pinned in your local environment; if omitted, v5 is the new default. Use a new chat when retesting prior failures. Old tickets from earlier accidental routing remain in the test database until handled separately; this patch does not delete user records.

For failed live runs, capture the route/agent labels, `agent_validation` or `agent_failed` logs, input (redacted), and output; never paste tokens or real client records. Compare actual vs expected behaviour before adding another phrase-specific fix. Recorded database actions must continue to require authorisation and explicit client confirmation.
