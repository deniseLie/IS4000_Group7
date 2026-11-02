# Knowledge articles

The approved articles the assistant can retrieve, as of 2 Nov 2026 (migrations up to `0087`).

**Two stores, same 28 articles:**
- **Memory** (`backend/app/services/knowledge.py`, `KNOWLEDGE_STORE=memory`) keeps each article as one text: its sections joined in order.
- **Database** (`kb.articles`, `kb.sections`, `kb.facts`, `KNOWLEDGE_STORE=database`) keeps the sections separately (anchors below), plus retired versions.
- **Section vectors** (`kb.section_embeddings`, migration `0088`) are used only by hybrid search (`KNOWLEDGE_SEARCH=hybrid`). Each published section is embedded as its title, heading and body, without its keywords. After any migration that adds or changes sections, run `python -m scripts.embed_knowledge --database-url …`; until then the new sections are found by keywords only.

All content is synthetic demo guidance or a summary of an allowed public source. The 2 Nov articles were informed by [research on how a private bank explains these topics](research/ubs-practices.md). They are written as DemoBank guidance and do not name or quote that bank.

## Compliance review and documents

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `review-status` | Your compliance review status | DemoBank | v1 · review-stages, item-status, why-review | Each review status (review opened … closed); each document status (Prepared, Submitted – awaiting review, Correction requested, Accepted, Unknown); the preliminary check is not approval; why periodic (KYC) reviews happen |
| `review-documents` | Documents for your compliance review | DemoBank | v1 · proof-of-address, bank-statements, source-of-wealth, tax-residency, identity | What counts as proof of address (name, Singapore address and postal code, issuer, dated within 3 months); statements for the last 3 full months; source of wealth vs source of funds; tax residency self-certification (CRS/FATCA, no tax advice); certified copy of an identity document |
| `document-uploads` | Uploading documents | DemoBank | v1 · formats, legibility, how-to-upload | PDF, DOCX, JPEG, PNG up to 10 MB; making a scan readable; upload → preliminary check → send to the bank; files cannot be deleted or replaced |
| `bank-questions` | Answering the bank's questions | DemoBank | v1 · answer, clear-answer, correction | Where the bank's question appears and how to reply; what makes a clear answer; uploading a corrected document |
| `review-reminders` | Review reminders | DemoBank | v1 · why, deadline | Why reminders arrive and when they clear (service messages, not switchable); what to do if you cannot meet the deadline |

## Everyday banking: accounts, cards and loans

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `accounts` | Demo account information | DemoBank | v1 · main | Guidance without login; balances need sign-in; fees, rates and limits only from an approved product record |
| `savings-fees` | Savings account fees | DemoBank | v1 · main | Everyday Savings: no monthly fee, SGD 2 fall-below fee under SGD 1,000 average daily balance |
| `sg-accounts` | Singapore bank account basics | MoneySense | v1 · main | Savings, current and fixed deposit accounts; early withdrawal; what to check before opening |
| `personal-details` | Updating your personal details | DemoBank | v1 · how-to, proof | My requests → Update personal details for address, phone or email; proof of a new address; why the registered phone matters; moving abroad may change tax residency |
| `cards` | Demo credit card enquiries | DemoBank | v1 · main | Card terms only from a product record; card not working (ATM, online, shop) |
| `card-limit-change` | Changing your credit card limit | DemoBank | v1 · main | Temporary or permanent limit requests; the cards and credit team decides |
| `sg-card-payments` | Singapore credit card payments | MoneySense | v1 · main | Credit limits, minimum payments, interest on unpaid balances, due dates |
| `loans` | Demo loan enquiries | DemoBank | v1 · main | Loan enquiries are preliminary; only the bank decides; the chatbot cannot apply |
| `car-loans` | Car loans | DemoBank | v1 · products, eligibility, apply | Car loan products, general eligibility, how to apply |
| `sg-loan-types` | Singapore loan types | MoneySense | v1 · main | Secured vs unsecured, term loans, revolving credit |
| `statements` | Getting a statement | DemoBank | v3 · monthly, older-copies, missing | Overview → Statements for the last 3 full months; older, certified or paper copies and bank letters by request; why the current month or a pending payment is not shown |

## Payments and transfers

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `transactions` | Failed pending and unfamiliar payments | DemoBank | v1 · main | Failed vs pending; avoid duplicates; unfamiliar payments may be fraud |
| `transfer-timing` | Pending transfers and due dates | DemoBank | v1 · main | FAST timing, pending after one business day, do not resend, bills due soon |
| `overseas-transfers` | Sending money overseas | DemoBank | v2 · how-to, details-needed, fees-timing, safety | How to send; recipient details (SWIFT/BIC, IBAN, currency); who pays charges, one to three business days, cut-off; changed bank details; a transfer sent to the wrong place |

## Fraud and security

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `lost-card` | Lost or stolen card guidance | DemoBank | v3 · main | Report immediately; only the bank blocks a card; urgent fraud-team reference when signed in |
| `unrecognised-charge` | Unrecognised card charge | DemoBank | v3 · urgent-steps, patterns, replacement-card | Urgent steps, card-testing patterns, replacement card after a block |
| `sg-card-disputes` | Singapore card payment disputes | Association of Banks in Singapore | v1 · main | Unauthorised transactions and non-delivery disputes with the issuing bank |
| `security` | Protecting private information | DemoBank | v2 · never-share, warning-signs, if-shared | What DemoBank never asks for (passwords, PINs, codes, sign-in links, remote access, "safe accounts"); signs of a scam; steps after sharing a code or clicking a link |
| `app-login-recovery` | Mobile app login recovery | DemoBank | v2 · steps, unexpected-code | Locked out, forgot password, a one-time code you did not ask for |
| `account-security-after-fraud` | Locked out after a suspicious charge | DemoBank | v1 · main | A lockout together with a suspicious charge treated as a security issue |

## Support and relationship manager

| Article id | Title | Publisher | DB version · sections | Covers |
|---|---|---|---|---|
| `support` | Getting human support | DemoBank | v5 · main, which-team | Talk to a person goes to the RM; lost cards and unrecognised charges go to the fraud team; which requests to raise directly under My requests; what the assistant cannot do |
| `rm-working` | Working with your relationship manager | DemoBank | v1 · role, contact | What the RM does and who decides; Talk to a person, Meetings and Records contact details; urgent card issues skip the RM |
| `rm-change` | Changes to your relationship manager | DemoBank | v1 · team-restructure, continuity, no-advisor, unrecorded | Why the RM changed and what stays the same |

## Facts (exact figures checked by the validator)

| Fact id | Value | Article · anchor | Store |
|---|---|---|---|
| `savings.monthly_fee` | 0 SGD | `savings-fees` · main | Database |
| `savings.fall_below_fee` | 2 SGD | `savings-fees` · main | Database |
| `savings.fall_below_threshold` | 1000 SGD | `savings-fees` · main | Database and memory |
| `review.proof_of_address_max_age` | 3 months | `review-documents` · proof-of-address | Database and memory |
| `review.statement_months` | 3 months | `review-documents` · bank-statements | Database and memory |
| `documents.max_upload_size` | 10 MB | `document-uploads` · formats | Database and memory |
| `statements.months_available` | 3 months | `statements` · monthly | Database and memory |
| `transfers.overseas_business_days` | "1-3" business days | `overseas-transfers` · fees-timing | Database and memory |

**Where the 0087 figures come from.** Each one restates a rule the app already enforces or text that was already approved:
- the proof-of-address and statement rules come from `document_templates.py`;
- the upload limit comes from the `0051` storage bucket and `schemas/document_uploads.py`;
- the three statement months come from `lib/statements.js`;
- the transfer time comes from the earlier `overseas-transfers` v1.

## Retired versions (database only)

Kept so old citations still open; search never returns them:
- `app-login-recovery` v1
- `lost-card` v1–v2
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
  - Since 2 Nov the memory copy of `rm-change` holds the same four section texts as the database, so the two copies no longer differ.
- **Most articles now have several sections.**
  - Older articles are 45–130 words in one section.
  - The 2 Nov articles use one idea per section, about 60–150 words each.
- **External sources are limited.**
  - Four articles summarise external sources: three MoneySense, one ABS.
  - They were checked on 12 Oct 2026, review is due 12 Jan 2027, and after that date they drop out of search.
  - The allow-list of external publishers is unchanged.
- **The memory fallback is larger.** With no keyword match, or for a non-English question, memory mode sends every current article to the model. That is now 28 articles instead of 21.
- **Gaps:**
  - home and personal loans, and borrowing against investments;
  - transfer and ATM limits;
  - PayNow and GIRO;
  - deposit insurance;
  - closing an account;
  - notification settings beyond review reminders;
  - temporary card holds (hotels, fuel), which can look like unrecognised charges.
