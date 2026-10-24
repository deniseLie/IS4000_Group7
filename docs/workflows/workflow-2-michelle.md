# Michelle: compliance review and documents

Current implementation uses migrations **0056–0061**. Hosted database migrations through **0061** are applied; the frontend and API run locally against hosted Supabase Auth, private Storage, database and inbox. Only fictional coursework data is supported. Historical bank decisions and old reports/receipts are preserved.

## Workflow and backlog traceability

The supplied Michelle diagram is the acceptance sequence. Product backlog items CR1, CR7, CR2, CR4 and CR6 map as follows; CR3/CR5 are merged into CR1/CR4 in the backlog.

| Diagram requirement | Implementation |
|---|---|
| Sign in, explain the notice using Michelle’s owned case | Caller JWT/RLS plus explicit customer predicates; personal compliance agent; saved real-model chat. A compliance/document answer automatically opens the owned review. |
| Show only recorded requested documents, instructions, file formats/limits, preparation and one submission status | `ComplianceWorkspace.jsx`; `ChecklistPreparation.jsx`; existing immutable private upload boundary. No new document requirements are inferred from a customer upload. |
| Invalid type/size | Browser descriptor validation and API/Storage validation; PDF, DOCX, PNG/JPEG, 10 MiB. |
| Unreadable/uncertain copy gives no pass/fail | Bounded credential-free parser/OCR and grounded evidence validation; `unable_to_assess`, with retry/support. Provider failure is explicit. |
| Preliminary issues and corrections in the same chat | Every completed assessment automatically sends its exact current case/item/document/check selector through normal `/chat`; this is an actual model response saved in the current conversation. Explain buttons use the same selector. |
| Successful check updates only the item to submitted–awaiting review | Signed report, latest current notice/policy/check, expiry and immutable object metadata rechecked under SQL locks; atomic action receipt/audit/outbox. Bank acceptance and case approval remain separate. |
| Repeat uploads; replacement versions stay private | Every upload has a new owned immutable object/document ID. New reports preserve history. Explicit recheck reevaluates bytes; issues/inability are not cached as decisive daily results. |
| Latest status and timestamp, Your action / With the bank; stale/unavailable warning | Owned workspace snapshot plus server-time freshness; policy controls stale threshold. Status is never inferred from an AI response. |
| Explain selected follow-up; do not invent the reason | Typed request selector, ownership validation before saving/model work, then evidence narrowed to that exact request and case. Multiple requests are supported. Missing reasons/instructions are acknowledged and directed to support. |
| Reminder names recorded action, deadline and where to respond | Restricted worker scheduler; distinct case/request deadlines, stale-state recheck, dedupe. Window, timezone, service classification and approved fallback locale are configuration data. Non-service reminders honour channel/quiet-hour preferences. |
| Refuse approval; offer the right team | The assistant cannot approve the case. Reviewed support preparation binds owned case/item/request/document/check context to a real handoff receipt. Only the MFA-authenticated staff member who claims that handoff can read its recorded context. |

The diagram submits after a successful preliminary check. This resolves the backlog’s shorter “successful upload” wording in favour of the supplied diagram: merely storing bytes never declares them accepted or submitted for bank review.

## Generic requirements instead of demo templates

`compliance.notice_text` combines the authorised case notice, selected item label/instructions and optional **bank-published `check_guidance`**. It never combines requirements from other items. A real model derives source-quoted requirements for arbitrary requested content; a separate real call matches exact document evidence. There is no banking-field allow-list and no customer/persona UUID activation list. Every newly inserted checklist item gets the generic notice policy.

`notice_requirements/v2` distinguishes document age from period coverage and preserves alternatives. `document_evidence/v2` supports letters, paragraphs, tables, reordered labels and explicit date intervals. Backend checks verify exact quotes, source-grounded counts/units, unambiguous configured dates, recency/coverage and explicit page counts. Printed page numbering can identify a missing internal page. Periods can be on one page; required months do not imply pages. Presence is not authenticity or identity matching.

A whole-form instruction without its mandatory-field list remains **unable to assess**. An operator can publish approved checking guidance as data. The model must not invent a bank’s legal form requirements. Unsupported constraints, conflicting evidence, ambiguous dates and low-confidence OCR require a clearer copy or manual support. Successful fixture checks are not proof that arbitrary documents or all languages are understood correctly.

Extracted document text exists only during parsing/checking and is sent to the configured interpretation provider, as disclosed in the UI. It never enters reports, application logs, audit events, answers or checkpoints. Reports retain notice quotes/requirement parameters, codes, byte checksum and identifiers/timestamps. Internal requirement references are excluded from model answer evidence, preventing numeric hashes from tripping the checkpoint’s unchanged secret detector. Real provider failures are never replaced with sample answers.

## Policy, expiry and retry behaviour

`compliance.check_policy` is owner-managed, customer-readable configuration. Defaults: Asia/Singapore; 24-hour report validity; seven-day display freshness and reminder windows; completed-month statement coverage; unambiguous ISO/written-month dates, with numeric-date order rejecting ambiguity; service reminders; approved English fallback. These values are adjustable data, not persona rules. OCR language/confidence settings are private environment configuration (`DOCUMENT_OCR_*`). Parser byte/page/time bounds remain technical safety limits.

The notice digest includes the entire policy and approved guidance. Changing either hides old findings and blocks their submission in both API and SQL. Reports have explicit expiry, so crossing Singapore midnight alone does not invalidate a still-valid result. A check crossing the configured calendar-day boundary during interpretation safely gives inability, requiring a new check. Assessment timestamps are recorded after the real provider finishes.

A successful fresh result can be reused for ordinary retry. Explicit recheck always evaluates again. A newer failed check prevents replaying an older passing check, including direct action commands. Stable command identities return existing receipts for exact retries. Replacing a previously submitted file does not erase that historical submission or bank decision; current findings are displayed separately.

## Authorised notice import and support operations

`python -m scripts.prepare_compliance_notice --input /private/tmp/notice.json --output /private/tmp/notice.sql` prepares reviewable SQL only. It validates synthetic input, generates case/item UUIDs, safely quotes source text and performs no database connection or deployment. Input fields are documented in the script. Only the owner publishes approved notices/guidance; customer-uploaded letters cannot authorise requirements. This is an operator import tool, not automatic ingestion from a real bank.

`POST /documents/support` creates an owned action case with a fixed status/finding summary and selected compliance context. The customer reviews the summary before sending. `staff_support.py compliance-context --handoff ...` reads the selected context only after an authorised support user claims that handoff under MFA. It excludes file paths/bytes and internal compliance records. The demo has no staffed queue or human replies; actual staffing/MFA provisioning is an operator responsibility. Formal bank acceptance and real bank integrations are outside this coursework system.

## Activation

1. Apply unchanged migrations in order through 0061. For an existing 0059 deployment:
   ```bash
   cd backend
   ../.venv/bin/python -m scripts.prepare_hosted_migrations --first 60 --last 61 --output /private/tmp/demobank-0060-0061.sql
   ```
2. Keep the existing database-matching private `DOCUMENT_PRECHECK_SIGNING_KEY`, stable `CHECKPOINT_SIGNING_KEY`, `PIPELINE=v2`, `CONVERSATION_STORE=database`, working configured model and restricted worker. No service-role key is used in app/browser.
3. For OCR install the local helper with `../.venv/bin/python -m scripts.install_document_ocr`, configure its private absolute command path and language/confidence settings. Without a usable OCR engine, scans honestly remain unassessable.
4. Restart the API after code/environment changes. Vite/API processes must remain running; no public web/API deployment or unattended OS service was added.

## Verification

Native tests use temporary PostgreSQL, official PostgREST, real JWT/RLS/SQL transactions and actual file bytes. The local storage adapter exercises our byte/ownership boundary; it does not simulate AI or prove hosted Auth/Storage internals. Model acceptance and browser tests use the actual configured Codex transport. The user’s existing provider configuration is preserved.

```bash
cd backend
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 ../.venv/bin/python -m pytest -q
TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 TEST_BROWSER_LOCAL=1 TEST_MICHELLE_LIVE=1 TEST_MICHELLE_TRANSPORT=codex ../.venv/bin/python -m pytest -q tests/test_notice_checks.py tests/test_michelle.py tests/test_compliance_generic.py tests/test_michelle_browser.py
```

### Verified results (2026-10-24)

- Complete backend suite with native PostgreSQL/PostgREST enabled: **738 passed, 72 opt-in skips** (`/private/tmp/michelle-regression-complete.log`).
- Focused real-model layouts/API plus generic policy/support/prompt checks: **48 passed** (`/private/tmp/michelle-final-acceptance.log`).
- Frontend units: **54 passed**; production build passed (`/private/tmp/michelle-final-frontend.log`, `/private/tmp/michelle-final-build.log`). The existing large-bundle warning remains.
- Both final integrated browser scenarios passed (**2 passed**, `/private/tmp/michelle-browser-complete.log`) and include automatic selected-document chat, selected information request, approval refusal, scoped support receipt, expiry/recheck, persistence, worker inbox and mobile/desktop layout. Shared UI regressions passed across all **10** scenarios covering checklist, private documents, notifications, Alex's action review and David's appointments (`/private/tmp/michelle-final-shared-browser.log` for nine passing scenarios, `/private/tmp/michelle-inbox-regression-final.log` for both inbox scenarios after its native Realtime timing fix). Native inbox tests use the explicit Reload control because native PostgreSQL lacks hosted Realtime.

Computer Use used the existing Sausage Chrome profile and actual hosted Michelle password login, private Storage, signed reports and worker inbox. A separate **Synthetic workflow verification review** was imported using the operator tool, with random case/item IDs; the previous review/documents were retained. Its four requirements are address evidence, wealth declaration with approved field guidance, **two** months of statements, and tax declaration with approved field guidance. Two distinct information requests verify exact selection. All four documents reached submitted–awaiting review, with real receipt IDs and automatic saved-chat explanations. The two request explanations stayed separate and acknowledged that no extra reason was recorded. Formal approval was declined and the selected-request handoff produced a receipt without claiming a person replied.

Uploaded verification files (all fictional), retained in Michelle's private immutable document history:

| File | Contents / purpose |
|---|---|
| `Missing address DEMO.pdf` | Utility bill with Synthetic Demo and issue date, deliberately no address; missing-address finding, no submission. |
| `Utility letter DEMO.pdf` | Utility bill in letter/recipient layout, Synthetic Demo, 42 Fictional Lane and current issue date; submitted. |
| `Wealth narrative DEMO.pdf` | Synthetic Demo's narrative employment/savings declaration and a signature entry; checked against the operator-published field list, submitted. |
| `Consolidated statements DEMO.pdf` | Fictional Bank name and the two latest completed monthly periods on one PDF page; submitted. |
| `Tax narrative DEMO.pdf` | Synthetic Demo and Fictional Country A tax residency statement; checked against the published field list, submitted. |

Review copies and the exact synthetic notice configuration are in `/private/tmp/michelle-generic-review/`. No real personal documents were uploaded. Screenshots: `/private/tmp/michelle-scoped-support-verified.png`, `/private/tmp/michelle-inbox-generic-verified.png`, and `/private/tmp/michelle-workflow-final-verified.png` after reload and a final recheck under the deployed final prompts. The app/API remain local on ports 5173/8000; no public web deployment or actual staffed support has been added.
