# Knowledge articles

The approved articles the assistant can retrieve, as of 4 Nov 2026 (migrations up to `0102`).

**One store.** Since 4 Nov 2026 (`0102`) articles live only in the database; the in-memory copy (`knowledge.py`) and the `KNOWLEDGE_STORE` switch are gone.
- **Articles and sections** (`kb.articles`, `kb.sections`) keep each section separately (anchors below), plus retired versions, which stay citable.
- **Section vectors** (`kb.section_embeddings`, migration `0099`) are used by hybrid search, which runs whenever `EMBEDDING_API_KEY` is set; without a key, or if the provider fails, search is keyword only. Each published section is embedded as its title, heading and body, without its keywords. After any migration that adds or changes sections, run `python -m scripts.embed_knowledge --database-url …` (or, on the hosted project, `backend/supabase/hosted/embed_knowledge.sql` in the SQL editor); until then the new sections are found by keywords only.

All content is synthetic demo guidance or a summary of an allowed public source. The 2 Nov articles were informed by [research on how a private bank explains these topics](research/ubs-practices.md). They are written as UBS Connect guidance and do not name or quote that bank.

## Compliance review and documents

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `review-status` | Your compliance review status | UBS Connect | v1 · review-stages, item-status, why-review | Each review status (review opened … closed); each document status (Prepared, Submitted – awaiting review, Correction requested, Accepted, Unknown); the preliminary check is not approval; why periodic (KYC) reviews happen |
| `review-documents` | Documents for your compliance review | UBS Connect | v1 · proof-of-address, bank-statements, source-of-wealth, tax-residency, identity | What counts as proof of address (name, Singapore address and postal code, issuer, dated within 3 months); statements for the last 3 full months; source of wealth vs source of funds; tax residency self-certification (CRS/FATCA, no tax advice); certified copy of an identity document |
| `document-uploads` | Uploading documents | UBS Connect | v1 · formats, legibility, how-to-upload | PDF, DOCX, JPEG, PNG up to 10 MB; making a scan readable; upload → preliminary check → send to the bank; files cannot be deleted or replaced |
| `bank-questions` | Answering the bank's questions | UBS Connect | v1 · answer, clear-answer, correction | Where the bank's question appears and how to reply; what makes a clear answer; uploading a corrected document |
| `review-reminders` | Review reminders | UBS Connect | v1 · why, deadline | Why reminders arrive and when they clear (service messages, not switchable); what to do if you cannot meet the deadline |

## Everyday banking: accounts, cards and loans

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `accounts` | Demo account information | UBS Connect | v1 · main | Guidance without login; balances need sign-in; fees, rates and limits only from an approved product record |
| `savings-fees` | Savings account fees | UBS Connect | v1 · main | Everyday Savings: no monthly fee, SGD 2 fall-below fee under SGD 1,000 average daily balance |
| `sg-accounts` | Singapore bank account basics | MoneySense | v1 · main | Savings, current and fixed deposit accounts; early withdrawal; what to check before opening |
| `personal-details` | Updating your personal details | UBS Connect | v1 · how-to, proof | My requests → Update personal details for address, phone or email; proof of a new address; why the registered phone matters; moving abroad may change tax residency |
| `cards` | Demo credit card enquiries | UBS Connect | v1 · main | Card terms only from a product record; card not working (ATM, online, shop) |
| `card-limit-change` | Changing your credit card limit | UBS Connect | v2 · main | Temporary or permanent limit requests (steps of SGD 1,000, up to 90 days, one per card every 30 days); the cards and credit team decides |
| `card-controls` | Freezing, replacing and disputing a card | UBS Connect | v1 · freeze, replace, dispute | Freeze and unfreeze your own card; replacement for a lost, stolen or damaged card; disputing a recognised charge within 60 days |
| `sg-card-payments` | Singapore credit card payments | MoneySense | v1 · main | Credit limits, minimum payments, interest on unpaid balances, due dates |
| `loans` | Demo financing enquiries | UBS Connect | v3 · main | Property financing and borrowing against investments; no personal or car loans; ask which one; arranged through the RM; only the bank decides (0115) |
| `home-loans` | Home loans and property financing | UBS Connect | v2 · main, steps, documents | Uncommitted, variable rate, no early repayment penalty; RM → mortgage specialist's borrowing plan → valuation and conveyancing → credit decision; documents (0115) |
| `lombard-financing` | Lombard loans: borrowing against your investments | UBS Connect | v1 · main, facilities, collateral, apply | Variable loan facility and fixed term advance; the bank decides collateral and lending values; what happens when values fall; through the RM (0115) |
| `sg-loan-types` | Singapore loan types | MoneySense | v1 · main | Secured vs unsecured, term loans, revolving credit |
| `statements` | Getting a statement | UBS Connect | v3 · monthly, older-copies, missing | Overview → Statements for the last 3 full months; older, certified or paper copies and bank letters by request; why the current month or a pending payment is not shown |

## Payments and transfers

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `transactions` | Failed pending and unfamiliar payments | UBS Connect | v1 · main | Failed vs pending; avoid duplicates; unfamiliar payments may be fraud |
| `transfer-timing` | Pending transfers and due dates | UBS Connect | v1 · main | FAST timing, pending after one business day, do not resend, bills due soon |
| `overseas-transfers` | Sending money overseas | UBS Connect | v2 · how-to, details-needed, fees-timing, safety | How to send; recipient details (SWIFT/BIC, IBAN, currency); who pays charges, one to three business days, cut-off; changed bank details; a transfer sent to the wrong place |

## Fraud and security

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `lost-card` | Lost or stolen card guidance | UBS Connect | v4 · main | Freeze it on Overview, then request a replacement; unrecognised charges go to the fraud team; unfreezing rules; never share secrets |
| `unrecognised-charge` | Unrecognised card charge | UBS Connect | v3 · urgent-steps, patterns, replacement-card | Urgent steps, card-testing patterns, replacement card after a block |
| `sg-card-disputes` | Singapore card payment disputes | Association of Banks in Singapore | v1 · main | Unauthorised transactions and non-delivery disputes with the issuing bank |
| `security` | Protecting private information | UBS Connect | v2 · never-share, warning-signs, if-shared | What UBS Connect never asks for (passwords, PINs, codes, sign-in links, remote access, "safe accounts"); signs of a scam; steps after sharing a code or clicking a link |
| `app-login-recovery` | Mobile app login recovery | UBS Connect | v2 · steps, unexpected-code | Locked out, forgot password, a one-time code you did not ask for |
| `account-security-after-fraud` | Locked out after a suspicious charge | UBS Connect | v1 · main | A lockout together with a suspicious charge treated as a security issue |

## Support and relationship manager

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `support` | Getting human support | UBS Connect | v5 · main, which-team | Talk to a person goes to the RM; lost cards and unrecognised charges go to the fraud team; which requests to raise directly under My requests; what the assistant cannot do |
| `rm-working` | Working with your relationship manager | UBS Connect | v1 · role, contact | What the RM does and who decides; Talk to a person, Meetings and Records contact details; urgent card issues skip the RM |
| `rm-change` | Changes to your relationship manager | UBS Connect | v1 · team-restructure, continuity, no-advisor, unrecorded | Why the RM changed and what stays the same |

## Figures in the articles

The validator checks every figure in an answer against the cited section text. These figures were also stored in `kb.facts` until `0102` dropped the table: nothing read it except the retail fall-below check, which now keeps its synthetic SGD 1,000 term as `retail_checks.FALL_BELOW`. The ids below are kept as labels only.

| Figure | Value | Article · anchor |
|---|---|---|
| `savings.monthly_fee` | 0 SGD | `savings-fees` · main |
| `savings.fall_below_fee` | 2 SGD | `savings-fees` · main |
| `savings.fall_below_threshold` | 1000 SGD | `savings-fees` · main |
| `review.proof_of_address_max_age` | 3 months | `review-documents` · proof-of-address |
| `review.statement_months` | 3 months | `review-documents` · bank-statements |
| `documents.max_upload_size` | 10 MB | `document-uploads` · formats |
| `statements.months_available` | 3 months | `statements` · monthly |
| `transfers.overseas_business_days` | "1-3" business days | `overseas-transfers` · fees-timing |

**Where the 0098 figures come from.** Each one restates a rule the app already enforces or text that was already approved:
- the proof-of-address and statement rules come from `document_templates.py`;
- the upload limit comes from the `0051` storage bucket and `schemas/document_uploads.py`;
- the three statement months come from `lib/statements.js`;
- the transfer time comes from the earlier `overseas-transfers` v1.

## Retired versions (database only)

Kept so old citations still open; search never returns them:
- `app-login-recovery` v1
- `card-limit-change` v1
- `car-loans` v1 (0115: not offered to private clients in this demo)
- `home-loans` v1
- `loans` v1–v2
- `personal-loans` v1 (0115)
- `lost-card` v1–v3
- `overseas-transfers` v1
- `security` v1
- `statements` v1–v2
- `support` v1–v4
- `unrecognised-charge` v1–v2

## Observations

- **Compliance now has its own guidance.**
  - Five articles cover review statuses, documents, uploads, the bank's questions and reminders.
  - The compliance and document agents still take the client's own items, due dates and findings from case records and the document templates. The articles explain what those mean.
- **`rm-change` stays at v1 in the database.**
  - Migration `0062` pins its sections for the relationship introduction, so the client-facing RM explanation went into the new `rm-working` instead.
- **Most articles now have several sections.**
  - Older articles are 45–130 words in one section.
  - The 2 Nov articles use one idea per section, about 60–150 words each.
- **External sources are limited.**
  - Four articles summarise external sources: three MoneySense, one ABS.
  - They were checked on 12 Oct 2026, review is due 12 Jan 2027, and after that date they drop out of search.
  - The allow-list of external publishers is unchanged.
- **No match means no evidence.** There is no fallback library: a question with no keyword or similarity match gets no articles, and the answer pipeline treats that as an empty result. Non-English questions rely on hybrid search (keyword search alone rarely finds them).
- **"Safe account" scam.** The `security` never-share section says the bank never asks you to move money to a safe account, but "safe" is in neither security section's heading or keywords. Since `0097` a section matches only on those, so "Someone from 'the bank' wants me to move my savings to a safe account" finds savings and account articles instead. Its search-evaluation case was dropped on 10 Nov 2026.
- **Fixed safety replies keep their own text.** Lost card, human support, secrets and guest balance answer without the model or the database and cite pinned sections (`answers.PINNED`: lost-card v4, support v5, security v2, accounts v1, unrecognised-charge v3). Revising one of those articles means updating the text and pin in `answers.py`; `test_knowledge_checks.py` fails until then.
- **Gaps:**
  - borrowing against investments;
  - transfer and ATM limits;
  - PayNow and GIRO;
  - deposit insurance;
  - closing an account;
  - notification settings beyond review reminders;
  - temporary card holds (hotels, fuel), which can look like unrecognised charges;
  - opening hours, cheques, joint accounts, tax on interest and email alerts for incoming money.
  - Search still returns nearby sections for most of these, because they share words such as limit, deposit, account, card or transfer. Their search-evaluation cases were dropped on 10 Nov 2026 as not critical ([evaluation README](evaluation/README.md#knowledge-retrieval-offline-no-model)).
