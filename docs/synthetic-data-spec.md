# Synthetic data spec

Status: **proposal**. The fake records the three persona scenarios need (see [user-story-mapping.md](user-story-mapping.md) and [system-architecture.md](system-architecture.md) §5.7). Everything here is invented: names end in `(DEMO)`, emails use `example.com`, staff contacts use `demobank.example`. Loaded by `backend/supabase/migrations/0004_seed_personas.sql`.

## 1. Conventions

| Rule | Value |
|---|---|
| Customer ids | `banking.customer_profiles.customer_id` is `bigint`. Personas use **1001–1003**, clear of the existing demo customers 1–4 (Alicia, Brandon, Chloe, Daniel), which stay untouched |
| Child ids | Banking rows use `<customer_id><n>` (e.g. account `10011`). New-schema rows use fixed UUIDs `00000000-0000-4000-8000-<persona><nnnnnnnn>`, so tests and stubs can name them |
| Amounts | Debits negative, credits positive (same as the existing rows). Currency `SGD` unless stated |
| Dates | Fixed dates around **2026-10-18**. Re-seed or shift them before a demo if "last 30 days" questions must still match |
| Login | Each persona logs in with a Supabase Auth user whose email equals `demo_email`. The seed links `auth_user_id` by email; create the users first, or re-run the link statement at the end of the seed |
| Masking | Only `last4` / `****nnnn` numbers are stored; no full card or account numbers exist anywhere |

The stub in `backend/app/services/customer_data.py` returns the same Alex values, so code built on the stub keeps working against the database.

> Contract note: `Principal.customer_id` in `schemas/contracts.py` is typed `UUID`, but the database key is `bigint`. Raise this at the next contracts review (change the contract to `int`, or map it in the Customer Data Service).

## 2. Alex Tan — everyday banking (stories 1–7, X4)

Profile `1001`, `demo.alex@example.com`, employed, `SGD 5,000 - 8,000 / month`, English.

| Record | Values | Used by |
|---|---|---|
| Savings account `10011` | `****6012`, balance 5,230.45 | 2 Account Enquiry |
| Credit card `10011` | Rewards Card, Visa, last4 **4821**, limit 8,000, outstanding 2,464.20, minimum 50.00, due 2026-11-05, `active` | 4, 5, X4 (block → `temporarily_blocked`) |
| Transaction `100111` | 2026-10-17, `ONLINE MERCHANT 8831`, **−2,400.00**, card 4821, `posted`, overseas | 5: the unrecognised charge (P0) |
| Transaction `100112` | 2026-10-17, `Transfer to landlord`, **−1,800.00**, account 6012, `pending` | 3: the pending transfer |
| Transaction `100113` | 2026-10-15, `Demo Supermarket`, −64.20, card 4821, `posted` | Normal history around the charge |
| Transaction `100114` | 2026-10-01, `Employer Pte Ltd (DEMO)` salary, +5,600.00, account 6012, `posted` | Account history |
| Loan `10011` | Personal, original 10,000, outstanding 6,200, monthly 320, next due 2026-11-01 | 2 loan questions |
| Notification preferences | in-app, quiet hours 22:00–07:00 | Card-blocked notice (service message, always sent) |

The failing app login has no data; it is answered from approved articles.

## 3. Michelle Ng — compliance review (CR1–CR6)

Profile `1002`, `demo.michelle@example.com`, self-employed, `SGD 20,000 and above / month`, English.

| Record | Values | Used by |
|---|---|---|
| Multi-currency account `10021` | `****7734`, balance 250,000.00 | Context only |
| Compliance case `…1002 00000001` | Periodic client review 2026; notice dated 2026-10-01; **due 2026-11-15**; status `awaiting_client_documents` | CR1, CR4, CR6 |
| Internal case data | risk rating `medium`, reviewer note | **Never visible to the customer** (no RLS policy); proves CR4's "no internal fields" rule |
| Requested item 1 | Proof of residential address (dated within 3 months): `not_submitted`, client mark `not_prepared` | CR1, CR3 |
| Requested item 2 | Source of wealth declaration: `submitted_awaiting_review` | CR3 |
| Requested item 3 | Bank statements, last 3 months: `submitted_awaiting_review` | CR3 |
| Requested item 4 | Tax residency self-certification: `unknown` | CR3 (stays "unknown") |
| Status history | 2026-10-01 `review_opened`; 2026-10-01 `documents_requested`; 2026-10-10 `documents_partially_received`; 2026-10-12 `awaiting_client_documents` | CR4 |
| Information request | Explain the source of the SGD 120,000 incoming transfer on 2026-09-22; reply by **2026-10-31**; `open` | CR5, CR6 |
| Notification preferences | in-app + email | CR6 reminders |

## 4. David Lee — relationship manager change (RC1–RC5, X6)

Profile `1003`, `demo.david@example.com`, employed, `SGD 12,000 - 20,000 / month`, English.

| Record | Values | Used by |
|---|---|---|
| Current account `10031` | `****3390`, balance 84,120.00 | RC2 summary |
| Advisor (old RM) | Rachel Goh (DEMO), Relationship Manager, Private Clients Team A | RC1, RC4 |
| Advisor (new RM) | Marcus Chua (DEMO), Senior Relationship Manager, Private Clients Team B, `marcus.chua@demobank.example`, `+65 6000 0000` | RC1, RC3 |
| Assignment (ended) | Rachel, 2024-03-01 to 2026-10-14 | RC1, RC4 |
| Assignment (current) | Marcus, from 2026-10-15, reason code `team_restructure` (wording comes from the approved article, not this row) | RC1 |
| Working hours | Marcus, Mon–Fri 09:00–17:00 Asia/Singapore | RC3 slots |
| Busy blocks | Marcus busy 2026-10-20 09:00–12:00 and 2026-10-21 14:00–15:00 | RC3 (slots exclude them) |
| Open request | "Update standing instruction for the quarterly USD transfer", urgency normal, deadline 2026-11-30, SG, USD, status `seen`, raised with Rachel on 2026-10-08 | RC4, X6 (must not be described as transferred) |
| Consent | No grants, no requests | RC2 starts from nothing |
| Outbox event | `rm.assignment_changed` for David, `pending` | RC1 system turn (Step 4 worker) |
| Notification preferences | in-app | RC1 notice |

## 5. Shared reference data

| Table | Rows |
|---|---|
| `notify.templates` | `card.blocked`, `compliance.deadline_reminder`, `rm.assignment_changed`, `consent.requested` (v1, `en`, in-app) |

Empty at seed time on purpose (filled by the running system): `conversation.*`, `actions.*`, `support.handoffs`, `consent.*`, `notify.inbox`, `rm.appointments`, `rm.agendas`, `rm.shared_summaries`, `compliance.documents`.
