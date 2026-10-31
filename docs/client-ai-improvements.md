# Client chatbot intelligence changes — 31 October 2026

These changes modify the **client chatbot implementation**; the unfinished RM-facing assistant remains unchanged.

## Scope and behaviour

1. **Intent recognition:** The `plan` node now calls the versioned AI planner for ordinary client intents, including multiple intents and conversational follow-ups. High-priority fraud, security/login and explicit human-assistance routes remain deterministic. `CLIENT_INTENT_ROUTING=rules` enables the previous shortcut for comparison or fallback scenarios; the default is `adaptive`.
2. **Multi-turn:** Card-limit intake recognises the latest corrected amount, whether a change is temporary, the correct card from short answers, travel as a *separate* reason, and a split date (`2028` then `20 December`). A year alone is not a complete end date. A switch to a different topic ends the intake; card and requested amount remain subject to verification and review.
3. **Response generation:** The existing evidence validator is always enforced. An otherwise unsafe/invalid AI response is never shown; a repair attempt explicitly addresses missing sources or bad output structure, then goes through the *same* validator. No generic model-generated banking fact is accepted without evidence.
4. **Approved knowledge retrieval:** The local demo KB now uses exact phrases plus meaning aliases, token overlap and conservative typo matching. Database KB search remains the source of truth for database mode and may retry an empty result with a bounded earlier user question for a *short* context-dependent follow-up. This is not an embeddings/vector store; do not label it semantic embeddings/RAG vector search in slides.
5. **Versioned prompts:** Added `planner/v3` and `retail/v8` with changelogs; no previous released prompt was modified. Existing client UI and verified ticket/action controls remain unchanged.

## Verification (offline only)

From the project root:

```bash
PYTHONPATH=backend python backend/scripts/check_client_limit_flow.py
PYTHONPATH=backend python backend/scripts/check_client_intelligence.py
python -m compileall -q backend/app
```

These checks require existing Python dependencies (e.g. pydantic), but no Supabase credentials or live LLM calls. They use **synthetic card records**, not real banking data. The frontend source was unchanged in this increment.

### Live demo to run on your Mac

Start from a **new authenticated chat** after restarting the backend, with `PIPELINE=v2`, configured `LLM_MODEL` and a working data source. Test:

1. `How can I increase my credit card limit?`
2. `What if I only need it temporarily?`
3. `I'm travelling to Japan in December.`
4. `Can you help me request SGD 15,000?`
5. `Actually, make that SGD 12,000 instead.`
6. `travel` — should select the Travel Card, not merely append a travel reason.
7. `2028` — should ask for the **day and month** (the year alone is insufficient).
8. `20 December` — should retain the year and request details, without claiming approval.
9. `What is my current credit limit?` — must *leave* the card-limit request context and fetch a verified figure, if authorised.
10. Ask a general FAQ, a follow-up using different words, a fraud-related query and an unrelated new topic. Verify intent routing, source citations, error handling and the correct team ownership.

Also inspect any `agent_validation`, `agent_revision`, and `agent_failed` events. For regulated scenarios, verify authentication, access permissions, reviewed sources, explicit confirmation and backend consistency in a configured integration environment.

### Important limits

- **Not yet verified:** real LLM intent accuracy, full backend integration suite, live Supabase permissions, deployed environment settings, or browser E2E testing. No claim of improved live accuracy is supported without before/after measurements.
- This is **not** a deployment approval or certification for UBS systems. It is a synthetic coursework prototype. Do not put `.env`, secrets, `.venv`, `node_modules`, `.git`, or caches in the shared ZIP.
