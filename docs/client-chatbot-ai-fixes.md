# Client chatbot: multi-turn and answer reliability fixes

This change set is **client-side only**. It does not implement the unfinished RM assistant.

## What changed

- **Client card-limit request context:** A bounded, per-conversation state reconstruction recognises an initial card-limit request and follow-ups such as travel context, temporary/permanent choice, corrected amounts, card selection, and an end date. It stops at unrelated topics. This is not a database mutation or permission bypass.
- **Card identification:** The chatbot recognises short replies such as `travel`, `signature`, `Travel Card ending 5592`, and `the one ending 5592` from the customer's authorised card records. Saying "I'm travelling" alone does not select the Travel Card.
- **Banking controls:** The chatbot only *prepares* the client's request. The form still requires review and submission; the cards/credit team makes all approval decisions. Figures in the reply are extracted from the customer's own messages and labelled as requested values, not approved bank limits.
- **Planner routing:** Continuations of a card-limit request stay with the client retail flow. Clear unrelated or multi-intent requests are not forced into this flow.
- **Personal LLM reliability:** The retail answer agent now attempts one conservative, validated revision for a safely repairable source-check failure. Rejected claims still fail the original validation; no unverified bank facts are released.
- **Cleaner validation fallback:** When a validated response cannot be produced, the UI no longer appends the same generic error message twice. Labels are clearer (`Could not verify`).
- **Client UI:** The `Raise a card limit request` button remains available after appropriate follow-up answers; the prefill summary keeps the latest corrected requested amount, card and end-date information. It is not shown for rejected answers. Card choice suggestions are one-tap replies.

## What is deliberately unchanged

- Auth and role-based access control, client-specific data reads, compliance and fraud pathways, ticket approval and creation, RM assistant, bank approval decisions, and source/figure validation.
- We did **not** introduce an unrestricted LLM fallback for bank facts. This would undermine the existing evidence and safety model.
- We did **not** add a vector database or change the production retrieval backend. Such changes require representative evaluation data and integration testing.

## Installation and local verification

1. Back up your local development project and extract this ZIP to a separate folder. The ZIP intentionally excludes `.env`, `.venv`, `node_modules`, `.git`, caches, and secrets. Copy your own **private** local environment configuration into your local project, not into a shared ZIP.
2. If merging onto your existing project, use the separate *Patch Only* archive instead. Its root is `IS4000_Group7/` and it includes only the changed source and test files.
3. For the LangGraph client chatbot, ensure local configuration has `PIPELINE=v2` and `CONVERSATION_STORE=database`; your Supabase migrations and private credentials must already work. Restart the backend after the code change. The default example file still contains v1/off for a blank installation.
4. Run the offline Python check from the project root:
   ```bash
   PYTHONPATH=backend python backend/scripts/check_client_limit_flow.py
   ```
5. Run frontend unit tests (Node 22 recommended):
   ```bash
   cd frontend && npm test
   ```
6. Start your backend and frontend as usual, and **open a new saved client chat** to avoid replaying previously persisted rejected turns.
7. Reproduce the original sequence in the same authenticated chat:
   - How can I increase my credit card limit?
   - What if I only need it temporarily?
   - I'm travelling to Japan in December.
   - Can you help me request $15,000?
   - Actually, make that $12,000 instead.
   - travel
   - until 20 December 2026
   Expected: no `Answer withheld`, latest requested amount = 12,000, selected Travel Card, correct end date, no auto-submission or approval claim. Review the request form fields before submitting.
8. Also try `What is my current credit limit?` and `Show my recent transactions` after the conversation. These must leave the limit-change intake flow.

## Verification already performed in this environment

- **98/98** frontend `npm test` unit tests passed.
- **18/18** offline Python regression checks passed using synthetic card data, including a check that unsupported bank figures remain rejected.
- Python modules compiled without syntax errors.
- **Not verified here:** full backend `pytest`, a real LangGraph/Supabase workflow, provider-based answers, migration compatibility in your hosted Supabase, frontend Vite build, or a browser demo. Those require installed packages, a configured environment, and live services not available in this sandbox. The browser screenshot is a previous version until you restart with the patched code.

## How to diagnose any remaining `Could not verify` messages

Inspect the backend's `agent_validation` event (`reason`), `agent_revision` event (`reason`), or `agent_failed` event (`error_type`) from the same request. `agent_failed` points to model/data service availability, whereas `agent_validation` can reveal unsupported figures, missing citations, bad drafts or prohibited claims. Do **not** disable the validators to make a test pass.

## Security

Never place active API keys in shared archives. Keep `.env` files private and rotate any credentials accidentally exposed in an earlier ZIP. Financial and card records in unit tests are synthetic.
