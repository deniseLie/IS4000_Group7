# v8 — Verified ticket-status lookup fix (client only)

## Root causes (found in v7)

1. The retail agent called `tickets.list`, but `retail` was missing that tool in `agents/registry.py`. `authorization.decide` denied it, and the orchestration error wrapper emitted the generic “Could not verify” response.
2. The status agent used `customer_data.get_tickets()`, which serialises validated ticket rows as dictionaries, but the agent accessed `row.reference` and other attributes. This mismatch would have caused a runtime failure after fixing the permission.

## Corrected workflow

- Questions such as “Has the bank approved my credit limit increase?” route to an **authorised read of the customer's own tickets**, not a new request.
- The retail agent has **read-only** `tickets.list` permission. No submission, status-changing or staff permissions were granted.
- `get_ticket_records` returns a typed, customer-scoped snapshot to the status agent. `get_tickets` remains available for APIs that need JSON.
- The status response distinguishes a workflow status (`Submitted`, `In review`, `Resolved`) from an actual bank-recorded approval. **Resolved is not approved**.
- If the backend cannot be read, the assistant explains that status is unavailable and directs the client to My Requests. It does not hallucinate approval, start a new limit-change application, create an RM ticket or ask the user to rephrase.

## Browser verification (signed-in synthetic customer)

1. New chat: `Has the bank approved my credit limit increase?` — show matching verified ticket status, clarify if multiple; no new limit question.
2. New chat: `What is the status of TCK-001040?` — show it only if owned, else explain no matching recorded request.
3. Open a known `Resolved` ticket — **do not say “approved”** unless an authorised bank decision explicitly confirms it.
4. Create a test request with confirmation in My Requests; ask its status in Assistant — shows same reference and status.
5. Sign out; ask for your approval status — no ticket data is revealed.
6. Simulate temporarily unavailable ticket service — show a retrieval-unavailable message, no invented status or RM handover.

For failures, check backend event `agent_failed` and its `error_type`/`db_code` (never share credentials). If an old deployment still displays “Could not verify”, confirm the patch is extracted into the **running project's folder**, restart backend, and use a new chat. The real Supabase RPC/migrations must be installed.

**Validation scope:** offline permission/contract tests only. Live LLM and Supabase tests still require your configured environment.
