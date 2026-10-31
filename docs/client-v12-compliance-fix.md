# Client compliance help fix (v12) — 31 October 2026

This change is based on the v11 source and modifies **only** the client compliance help/validation path. It does not change database migrations, the RM assistant, upload permissions, document acceptance, or the shared request-submission API.

## What changed

1. A vague English-language client question such as **"I need help with my compliance documents"** is routed to the compliance agent, even if an earlier chat was about a card limit.
2. The compliance agent asks a short **clarifying question** (required documents, upload, status, or correction) instead of attempting to verify an unspecified banking claim. This branch makes **no personal-data read or model call**, and doesn't change records.
3. Questions specifically asking about the checklist, a submission, a deadline, a correction, or review status still read only the authenticated client's authorised compliance snapshot and use the model to explain relevant evidence.
4. The compliance agent now uses the same **single evidence-aware repair attempt** that the retail agent uses for failed source/format validation. A repaired answer must pass the **same validation checks**; unsupported claims, approvals or failed database reads are not treated as success.
5. A separate regression test module covers the clarification boundary and the specific-question evidence path.

## Verification completed in the sandbox

- Frontend JS unit tests: **125 passed** (`cd frontend && npm test`). The frontend code itself is unchanged.
- Focused offline source-level smoke test: 7 generic/specific compliance examples passed; four additional regex boundary checks passed.
- Python syntax compilation of backend application sources: passed.
- Full backend pytest, live model calls, Supabase data, JWT and browser checks: **not verified in this sandbox** (backend dependencies and live credentials unavailable).

## Apply to your existing project

Stop backend and frontend, back up your project, and extract the `v12_Compliance_Patch.zip` in the directory **containing** `IS4000_Group7`:

```bash
unzip -o IS4000_Group7_Client_v12_Compliance_Patch.zip
```

Restart the backend and frontend. Keep existing `backend/.env` and database configuration; the patch does not contain private credentials or migrations. Start a fresh chat to avoid cached turns.

## Browser acceptance checks

| Test | Message or action | Expected |
|---|---|---|
| C12-01 | `I need help with my compliance documents.` | Asks which compliance task the customer needs; no `Could not verify` for this vague request, no automatic ticket or submission. |
| C12-02 | `What documents are outstanding for my compliance review?` | Uses only the signed-in client's recorded checklist and distinguishes prepared from submitted and bank-accepted. If bank data/model is unavailable, indicates unavailability, not an invented status. |
| C12-03 | `I need help uploading my compliance documents.` | Gives relevant, safe upload guidance rather than the generic choice-of-task prompt. |
| C12-04 | `Has the bank accepted the document I submitted?` | Only says accepted when a bank-confirmed decision exists. |
| C12-05 | `When is my compliance review due?` | Uses the recorded deadline, not an estimate. |
| C12-06 | `Show review here` (explicit UI action) | Opens the inline compliance review only on request; no duplicate navigation buttons. |
| C12-07 | In same chat, ask about card limit, then `I need help with my compliance documents.` | Switches to compliance without reusing card-limit information. |

Use **synthetic client data** for tests. Record the failing message and the corresponding backend log event if a specific compliance question still returns `Could not verify`. Useful events include `agent_validation` (citation/format rejection) and `agent_failed` (data/model error). Do not disable source validation or authentication to force a response through.
