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

`backend/app/services/customer_data.py` reads these rows with the signed-in persona's JWT and keeps the former stub's field names, so code built on the stub keeps working (no `segment` or loan `rate_pct`: the database has no such columns).

> `Principal.customer_id` is a positive integer matching the database `bigint` key. Customer Data RPCs re-resolve the signed-in JWT's linked profile and reject any mismatch with that request scope.

## 2. Alex Tan — complex banking needs (EB1–EB6, EB8)

Profile `1001`, `demo.alex@example.com`, employed, `SGD 5,000 - 8,000 / month`, English.

| Record | Values | Used by |
|---|---|---|
| Savings account `10011` | `****6012`, balance 5,230.45 | EB2 My Account Information |
| Credit card `10011` | Rewards Card, Visa, last4 **4821**, limit 8,000, outstanding 2,563.10 (2,464.20 before `0064`, 2,470.19 after it), minimum 50.00, due 2026-11-05, `active` | EB4, EB8 (block → `temporarily_blocked`) |
| Transaction `100115` | Fri 2026-10-17 **19:15 SGT**, `Hawker Delight, Tanjong Pagar`, −12.50, card 4821, `posted`, merchant country `SG` (migration `0070`; was a `0064` small charge) | EB8: Alex's own charge in Singapore; the travel rule's earlier charge |
| Transaction `100116` | Fri 2026-10-17 **21:42 SGT**, `SUNSET GRILL LA`, −86.40, card 4821, `posted`, overseas, merchant country `US` (migration `0070`) | EB8: flagged by the travel rule; Alex does not mention it, the dispute draft includes it |
| Transaction `100111` | Fri 2026-10-17 **21:58 SGT**, `TECHVAULT ELECTRONICS`, **−2,400.00**, card 4821, `posted`, overseas, merchant country `US` (migration `0070`) | EB8: the charge Alex asks about (P0); flagged by the travel rule |
| Transaction `100112` | 2026-10-17, `Transfer to landlord`, **−1,800.00**, account 6012, `pending` | EB5: the pending transfer |
| Transaction `100113` | 2026-10-15, `Demo Supermarket`, −64.20, card 4821, `posted`, merchant country `SG` | Normal history around the charge |
| Transaction `100114` | 2026-10-01, `Employer Pte Ltd (DEMO)` salary, +5,600.00, account 6012, `posted` | Account history |
| Loan `10011` | Personal, original 10,000, outstanding 6,200, monthly 320, next due 2026-11-01 | EB3 loan questions |
| Notification preferences | in-app, quiet hours 22:00–07:00 | Card-blocked notice (service message, always sent) |

The locked app and the one-time code Alex did not request have no data: there are no sign-in, device, location or OTP records, so the assistant links them to the US charges only from what Alex says, and answers from the approved `app-login-recovery` v2 article.

`0070` adds `banking.transactions.merchant_country` (ISO two-letter code, nullable; only card charges have one) and the customer transaction reader returns `txn_at` (the charge time) and `merchant_country`. The travel rule (`fraud_records.travel_flags`) flags a card charge made in another country less than 4 hours after a charge on the same card. Seeded times are fixed around October 2026; the 90-day record window and the 120-day dispute window must still include them.

## 3. Michelle Ng — compliance review (CR1, CR2, CR4, CR6, CR7)

Profile `1002`, `demo.michelle@example.com`, self-employed, `SGD 20,000 and above / month`, English.

| Record | Values | Used by |
|---|---|---|
| Multi-currency account `10021` | `****7734`, balance 250,000.00 | Context only |
| Compliance case `…1002 00000001` | Periodic client review 2026; notice dated 2026-10-01; **due 2026-11-15**; status `awaiting_client_documents` | CR1, CR4, CR6 |
| Internal case data | risk rating `medium`, reviewer note | **Never visible to the customer** (no RLS policy); proves CR4's "no internal fields" rule |
| Requested item 1 | Proof of residential address (dated within 3 months): `not_submitted`, client mark `not_prepared` | CR1 |
| Requested item 2 | Source of wealth declaration: `submitted_awaiting_review` | CR1 |
| Requested item 3 | Bank statements, last 3 months: `submitted_awaiting_review` | CR1 |
| Requested item 4 | Tax residency self-certification: `unknown` | CR1 (stays "unknown") |
| Status history | 2026-10-01 `review_opened`; 2026-10-01 `documents_requested`; 2026-10-10 `documents_partially_received`; 2026-10-12 `awaiting_client_documents` | CR4 |
| Information request | Explain the source of the SGD 120,000 incoming transfer on 2026-09-22; reply by **2026-10-31**; `open` | CR4, CR6 |
| Notification preferences | in-app + email | CR6 reminders |

## 4. David Lee — relationship manager change (RC1–RC4)

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
| Open request | "Update standing instruction for the quarterly USD transfer", urgency normal, deadline 2026-11-30, SG, USD, status `seen`, raised with Rachel on 2026-10-08 | RC4 (must not be described as transferred) |
| Consent | No grants, no requests | RC2 starts from nothing |
| Outbox event | `rm.assignment_changed` for David, `pending` | RC1 system turn (Step 4 worker) |
| Notification preferences | in-app | RC1 notice |

## 5. Shared reference data

| Table | Rows |
|---|---|
| `notify.templates` | `card.blocked`, `compliance.deadline_reminder`, `rm.assignment_changed`, `consent.requested` (v1, `en`, in-app) |

Empty at seed time on purpose (filled by the running system): `conversation.*`, `actions.*`, `support.handoffs`, `consent.*`, `notify.inbox`, `rm.appointments`, `rm.agendas`, `rm.shared_summaries`, `compliance.documents`.

## 6. Additions in migration 0063 (every persona has complete data)

| Persona | Added | Why |
|---|---|---|
| Alex (1001) | Current RM **Priya Nair (DEMO)**, Relationship Manager, Personal Banking Team C, `+65 6000 0002`, since 2023-06-01; Mon–Fri 09:00–17:00; video/phone/branch joining details | No "no relationship manager" state; Workflow 1 records unchanged |
| Michelle (1002) | Current RM **Daniel Tan (DEMO)**, Senior RM, Private Clients Team A, `+65 6000 0003`, since 2022-02-14; same hours/options; two everyday transactions | Complete overview and RM page |
| David (1003) | Four everyday transactions (salary, bill, premium, investment transfer) | Overview shows recent activity; his RM change is unchanged |

Alex and David have no compliance case on purpose: the app shows "No review needed — nothing for you to do right now".
