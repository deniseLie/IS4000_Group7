# Client chatbot v5 — fraud and ambiguous-intent fixes

## Scope

This patch builds on **v4** (client-intelligence and card-limit form prefill), and changes the **client side only**. RM screens and backend ownership rules were not changed.

### Changes

1. **Informal fraud language**: `backend/app/services/guardrail.py` recognises descriptions such as “someone used my card without permission”, “my card was used by somebody else”, “my card has been compromised” and “I wasn't even there for this charge”. Critical fraud routing stays rule-based, while ordinary request understanding is still model-first.
2. **Guided reporting entry**: signed-in clients receiving a fraud response see **Report suspicious transaction**. This only continues the assistant's existing identification/confirmation flow. It **does not submit, block, reverse, or file a report by itself**. When the chatbot lists the exact charges and asks “Yes, report it”, that existing confirmation UI takes priority.
3. **Ambiguous spending power**: planner v4 and personal retail v9 explicitly ask the client what sort of spending help they need instead of guessing that they want a transaction history. Existing answer validations are retained.
4. **Navigation**: an unverified answer no longer adds a misleading page link. The “spending power” and informal fraud language no longer leads to “Go to Recent activity” via an unqualified keyword match.
5. **Answer repair**: a rejected draft asks the model to clarify ambiguous requests rather than add unsupported banking claims. It must pass the same checks before display.

## Configuration

```env
PIPELINE=v2
CONVERSATION_STORE=database
CLIENT_INTENT_ROUTING=adaptive
```

The new default planner prompt is `v4`; personal retail prompt is `v9`. Check whether your local `.env` overrides `PROMPT_VERSIONS` and pins an older planner version. Knowledge retrieval remains under your existing `KNOWLEDGE_STORE` configuration. No credentials are included in this ZIP.

## Run tests

```bash
cd frontend
npm test
```

For backend tests, **install the project dependencies first**, then:

```bash
cd backend
python -m pytest -q tests/test_guardrail.py tests/test_client_fraud_natural_language.py
python -m pytest -q tests/test_card_answers.py tests/test_fraud.py tests/test_urgent_help.py
python -m pytest -q tests/test_agents.py tests/test_retail_agent.py
```

These backend suites may depend on extra local services and fixtures. The assistant environment did **not** have `langgraph` installed, so only Python compilation and independent static safety-pattern checks could be run here. The frontend unit suite was run locally.

## Browser acceptance scenarios to run first (use new chat for each scenario)

1. **Ambiguous intent**: “I need more spending power for my overseas trip.” Expected: ask if it's a card-limit increase, available spending capacity, or overseas payment option. No “Go to Recent activity”.
2. **Disowned card**: “I think someone used my card without permission.” Expected: urgent security guidance, a reporting entry, no automatic ticket.
3. **Natural variation**: “Somebody has been using my card.” Expected: same fraud priority.
4. **False positive**: “What is card fraud?” Expected: general information rather than an active fraud report.
5. **Fraud report entry**: click **Report suspicious transaction**. Expected: identify exact card and charge, ask for confirmation. No ticket until confirmation and successful backend receipt.
6. **No confirmation**: decline or correct a proposed charge. Expected: nothing sent.
7. **Card amount correction**: ask for a temporary 15k limit, change to 12k; select Travel Card; say “2028”, then “20 December”. Expected: requested amount 12000, end date 2028-12-20, with form review and no automatic change.
8. **Topic switch**: following that request, ask about statements; do not repeat prior amount/date.
9. **Auth**: ask for own balance as a guest; no personal data before login.
10. **Source safety**: ask for a bank-specific rate absent from approved knowledge; no invented rate.

For the complete **99-case** manual matrix, use the separate workbook `UBS_Client_v5_Regression_Test_Cases.xlsx`. The workbook has a START HERE sheet, Test Cases, Automated Checks and Defect Log.

## Known limits

- **No live LLM, Supabase or full browser integration verification** was performed in the assistant environment. Successful unit tests do not demonstrate a successful fraud ticket submission or that a model always asks the right question.
- Informal fraud detection is intentionally biased towards urgency; watch for false positives in real-world phrasing. More vocabulary can be discovered through traces and evaluated before adding more rules.
- The knowledge search uses the existing keyword/synonym and contextual retrieval enhancements from v3/v4; **no vector search or new bank integration was added in v5**.
- Do not use real customer data or credentials in test screenshots and logs. No live bank operations are connected.
