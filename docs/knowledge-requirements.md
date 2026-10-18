# DemoBank knowledge requirements

Companion to [system-architecture.md](system-architecture.md) (§5.7.1 Knowledge Service) and [user-story-mapping.md](user-story-mapping.md). Lists the approved content the agents need to answer every backlog story. Coursework demo: all content is synthetic or summarised from allowed public sources.

**Knowledge vs customer data.** Knowledge is approved content that is the same for every customer (how pending transfers work, what a review status means). Anything about one customer (their balance, their compliance case, their RM) is Customer Data and is not listed here.

## 1. Knowledge articles

Status: **exists** = article in today's `knowledge.py`; **partial** = exists but misses part of the story; **new** = to write.

### Alex: everyday banking (stories 2–6, X2, X4)

| Story | Article(s) needed | Status | Current id |
|---|---|---|---|
| 2 Account Enquiry | Account types and features, how interest works, account services | partial | `accounts`, `sg-accounts` |
| 3 Loan Enquiry | Loan products, eligibility criteria, application steps, "only the bank decides" wording | partial | `loans` |
| 4 Credit Card Enquiry | Minimum payment, interest, available credit, card fees, card not working | exists | `cards`, `sg-card-payments` |
| 5 Transaction Issue | Failed vs pending vs unfamiliar, troubleshooting per type | exists | `transactions` |
| 5 / X4 Fraud and lost card | Fraud playbook steps, lost/stolen card steps | partial | `lost-card` |
| X4 Replacement card | How a replacement card is issued and when it arrives | new | |
| 6 Transfers | How to make a local / overseas transfer, cut-off times | new | |
| 6 Update personal details | How to change address, phone, email | new | |
| 6 Statements | Where to find and download statements | exists | `statements` |
| 6 Online / mobile banking access | App login failing, account locked, password reset in the official app | new | |
| X2 Privacy | Never share PIN, OTP, password, CVV | exists | `security` |
| Human support | How to reach a person; what the bot cannot do | partial (update when handoffs exist) | `support` |

### Michelle: compliance review (CR1–CR5)

| Story | Article(s) needed | Status |
|---|---|---|
| CR1 Document checklist | Approved guidance per document type: what counts as proof of identity, address, income, source of wealth; accepted formats; how to submit | new |
| CR2 Document upload | Allowed formats, size limits, tips for a legible scan | new |
| CR4 Milestone tracker | What each review status means (`not submitted`, `submitted – awaiting review`, `under review`, `complete`) and what happens at each stage | new |
| CR5 Information requests | How to respond to a clarification request and how to confirm it was received | new |
| CR6 Reminders (questions about a reminder) | Why reminders are sent and how to change preferences | new |

### David: relationship continuity (RC1–RC5, X6)

| Story | Article(s) needed | Status |
|---|---|---|
| RC1 RM transition | Approved wording for an RM change; what a relationship manager does | new |
| RC2 Context summary | What the summary contains, who receives it, how it is used; consent and privacy explanation | new |
| RC3 Meeting scheduling | Meeting formats (phone, video, branch) and how to join | new |
| RC5 Meeting preparation | What an agenda is for and how it is shared | new |
| X6 Request intake | Supported request types and typical response times | new |

**Summary:** 10 articles exist today, almost all for Alex. About 18 new or extended articles are needed, mostly for Michelle and David.

## 2. Fact table (exact figures)

Typed rows the Response Validator checks figures against (`kb.get_fact`). Every row has `fact_id`, `value`, `unit` and the `article_id` it comes from.

| Group | Facts |
|---|---|
| Card fees | Annual fee, late payment fee, foreign transaction fee, cash advance fee |
| Interest rates | Savings rate, card interest rate, indicative loan rates |
| Limits | Daily transfer limit, ATM withdrawal limit |
| Card payments | Minimum payment formula, payment due period |
| Processing times | Typical time for local and overseas transfers to complete (for pending questions) |

## 3. Fixed templates (shown as written, never searched)

| Template | Used by |
|---|---|
| Loan and fee disclaimers | Orchestrator *Compose* (X3) |
| AI notice at chat start | Web Client (X3) |
| Lost / stolen card urgent response | Input Guardrail (X4) |
| Secret-detected warning | Input Guardrail (X2) |
| Notification templates (review reminder, card blocked, RM changed, booking reminder) | Notification Service (`notify.templates`) |

## 4. Configuration (rules, not articles)

| Config | Used by |
|---|---|
| Document Check rules per document type: required pages, required fields, legibility threshold | Document Check agent (CR2) |

## 5. Article and section fields

Each article has `article_id`, `version`, `category`, `type` (`faq`, `how_to`, `troubleshooting`, `product`), `audience` (`public` or `customer`), `title`, `summary` and `publisher` (`demobank` or an allowed external publisher). External-publisher articles also need `reference_url`, `checked_on`, `review_due` and an allowed domain (`www.moneysense.gov.sg`, `www.abs.org.sg`). Editing an article bumps `version` (and the dates for external sources).

Each article is split into **sections** (one idea each, about 50–300 words, a heading that says what it answers). Sections are what search returns and what citations point to (`article_id@vN#anchor`). Figures are not typed into sections; they live in the fact table. Writing guidelines and the full schema are in [knowledge-base-design.md](knowledge-base-design.md).

## 6. Where it is stored

| What | Where |
|---|---|
| Articles, sections and facts (runtime) | Hosted Supabase Postgres: `kb.articles`, `kb.sections` (full-text + pgvector embedding), `kb.facts`. Read through RLS by audience; no writes from the app |
| Content changes | Migration files in `backend/supabase/migrations/`, committed to git; the only way content changes |
| Section embeddings | Filled by `python -m scripts.embed_knowledge` after each content migration |
| Fixed templates | Versioned files in the repo, like prompts; notification templates seeded into `notify.templates` |
| Document Check rules | Config file in the repo |
| Offline unit tests | Small fixture, so tests run without the network |

Not Supabase Storage: that holds customer uploads only.

Today the articles are still hard-coded in `backend/app/services/knowledge.py`; moving them to the database is a separate code change.
