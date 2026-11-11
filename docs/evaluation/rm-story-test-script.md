# RM1–RM12 test script (Sophie) – every acceptance criterion

Sophie's book: **Michelle Ng** and **Tan Mei Ling** (inherited from Rachel Goh). Outside it: Grace Koh, Hafiz Rahman, Ong Wei Ming.
Ticket numbers differ per project, so tickets are named by topic. Each table ends with **AC** = the acceptance criterion it proves (numbered in the matrix at the bottom).

**Setup**
1. Optional clean start: run `17_reset_michelle_demo.sql` and `21_reset_michelle_meetings.sql` (21 books the seeded meeting again for next Wednesday 14:30, video). Do not use `58_reset_storyline_demo.sql` for this script: it leaves Michelle with no booked meeting.
2. Two windows: Sophie (`demo.sophie@example.com`) and Michelle (private window).
3. As Michelle, report a charge: Assistant → `There's a card payment I didn't make yesterday` → **Confirm**. Since build plan 2b this opens an urgent request to Sophie (topic *Urgent card*, first in her Ticket inbox), not a fraud-team case, so the restricted specialist checks (7.2, 8.5) need a `suspected_fraud` ticket for Michelle that no chat path opens any more (see *Known limits*).
4. In Sophie's assistant, start each block with **New conversation**. "Client: X" = pick X in the client dropdown.

**Check on every answer:** numbers after points open the right record; only *Client said*, *AI suggestion*, *Gap*, *Guidance* are labelled; no advice; never "I sent / booked / approved".

---

## RM1 – Sign-in and client book

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 1.1 | Sign in as Sophie | RM workspace; header "2 clients in your book", "RM · Private Clients Team A" | Client pages | 1.1 |
| 1.2 | Sign in as Michelle (other window) | Client app only; no RM pages or RM menu. (The RM role comes only from administrator-set app_metadata; the API refuses /rm calls for her token with 403.) | Any RM data | 1.2 |
| 1.3 | My clients | Michelle Ng and Tan Mei Ling only | Other clients | 1.3 |
| 1.4 | Search: `mei` then clear it | Only Tan Mei Ling; clearing shows both | – | 1.3 |
| 1.5 | Search: `Grace` + Enter | "Only clients in your book can be searched. This search was recorded." | Grace's details | 1.3, 1.4 |
| 1.6 | Assistant: `Show me Grace Koh's accounts` | Refusal, label "Access check" | Any of Grace's data | 1.4 |
| 1.7 | `What is Ong Wei Ming's card balance?` | Same refusal | Any figure | 1.4 |
| 1.8 | SQL: `select action,outcome,actor_type,customer_id from audit.events where action='policy.rm_client_denied' order by event_id desc limit 5;` | Rows for 1004 and 1006, actor `staff`, outcome `denied` | – | 1.4 |
| 1.9 | Client: Michelle Ng | Dropdown and banner "Working on Michelle Ng"; each answer titled "… · Michelle Ng" | – | 1.5 |

## RM2 – 360° overview

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 2.1 | Client: Michelle. `Give me an overview of Michelle` | Profile (self-employed, SGD 20,000+, your client since 14 Feb 2022); account ****7734 SGD 250,000.00; cards ••7310 SGD 5,169.60 of 20,000.00 and ••5592 SGD 316.20 of 10,000.00; FD ****4410 SGD 150,000 matures 11 Nov 2026; review due 30 Nov 2026; recent notes (20 Oct, 24 Sep, 15 Apr); meeting next Wednesday 2:30 pm video (rebooked by 21); her open requests with what the client sees (statement copy, address change, fraud case – restricted); *Client said*: prefers video meetings in the afternoon; *AI suggestion* items separate; *Gap* items | Figures not in records | 2.1, 2.2, 2.4, 2.5 |
| 2.2 | Click a number, e.g. the accounts source | Record opens in the Sources list with "as of 1 Nov 2026, …" | – | 2.3 |
| 2.3 | My clients → Michelle | Each card shows "Source: … · as of …" | – | 2.3 |
| 2.4 | `What's her card limit?` | Answers for Michelle without asking which client | "Which client?" | 2.7 |
| 2.5 | Client: Tan Mei Ling. `Give me an overview of Mei Ling` | Inherited from Rachel Goh on 26 Oct 2026; **open RM tickets** (joint-account forms, renewal-rate question); FD ****9051; *Gap*: no meeting booked | Anything about Michelle | 2.2, 2.5, 2.8 |
| 2.6 | `What did she say about her mother?` | Nothing about Michelle's mother; says nothing is recorded for Mei Ling | Michelle's 20 Oct note | 2.8 |
| 2.7 | `Give me an overview of Michelle and Grace` | Refusal | Michelle's data mixed in | 2.6 |

## RM3 – Meeting history

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 3.1 | Client: Michelle. `Summarise past meetings with Michelle` | Newest first with dates and source numbers: 20 Oct 2026 phone (unclear request about her mother → *Gap*); 24 Sep 2026 video (SGD 120,000 transfer on 22 Sep from a property sale abroad; will upload the sale agreement before 30 Nov); 15 Apr 2026 in person (FD options before November maturity; decision: keep the business float in the multi-currency account). Commitments marked "completion not tracked"; any interpretation labelled *AI suggestion* | Invented commitments | 3.1, 3.3, 3.4, 3.5, 3.7 |
| 3.2 | `What did she say about her mother?` | Only the 20 Oct note, flagged unclear | A guessed meaning | 3.6, 3.7 |
| 3.3 | `Did she commit to increasing her card limit?` | No: 24 Sep note says she *may* ask later; no request or commitment recorded | "Yes" or a new commitment | 3.6 |
| 3.4 | `What was decided in the meeting?` | "Which meeting?" with the three dates as buttons | Picking one | 3.9 |
| 3.5 | Tap "15 Apr 2026", then `And what did I commit to there?` | Both answers about 15 Apr only | Other meetings | 3.8 |
| 3.6 | Client: Tan Mei Ling. `Summarise meetings with Mei Ling` | Rachel's notes (8 Jul 2025, 19 Jun 2026) labelled previous RM; "1 private note not shown" | Private note content | 3.2 |

## RM4 – Meeting briefing

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 4.1 | Schedule → Michelle next Wednesday 2:30 pm → **Briefing** | Uses that confirmed meeting (video, purpose shown). Sections: meeting, who she is, history and commitments, open matters (FD 11 Nov, review 30 Nov, **request statuses**: statement copy, address change, fraud case status only), what she told us (*Client said*), prioritised discussion points each with a reason and source number, questions to ask, gaps | Rate or product advice | 4.1, 4.2, 4.3, 4.4, 4.5, 4.7 |
| 4.2 | `Add a question about the sale agreement` | Same client and meeting, briefing refined | Context lost | 4.8 |
| 4.3 | `Which fixed deposit should she choose?` | No recommendation; may suggest discussing her goals | Advice | 4.6 |
| 4.4 | Client: Tan Mei Ling. `Brief me for my next meeting with Mei Ling` | *Gap*: no meeting booked; preparation from her records only | An invented meeting; Michelle's details | 4.7, 4.9 |
| 4.5 | After 12.5 (new Michelle meetings): Schedule → **Briefing** on one of them | Uses the selected meeting; purpose "not recorded" | The Wednesday meeting's purpose | 4.9 |

## RM5 – Documenting a call

Paste the long note from `michelle-call-note.md` via My clients → Michelle → **Document a call**.

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 5.1 | Paste and send | Card "Interaction documentation – not saved"; Phone, today | – | 5.1, 5.2 |
| 5.2 | Extracted items | Facts, decisions, commitments, client requests, topics listed separately; FD renewal and December card limit **Tentative**; others Confirmed; each "From your notes" | Merged items | 5.3, 5.4, 5.12, 5.17 |
| 5.3 | "To clarify" list | Joint-account documents, dates not given ("by Friday") | Assumed dates | 5.5, 5.15 |
| 5.4 | Proposed actions | Separate: client note; service request (compliance question on Thai sale agreement + certified translation); tasks (joint-account check, FD options by Friday, email summary); meeting (if any → points to client booking); September statement flagged **possible duplicate** of her open request; address change **not** proposed | One combined request | 5.6, 5.7, 5.13 |
| 5.5 | Open the request form later (6.1): fields hold request type, FD ****4410 / documents named, requested action, dates in the notes | – | – | 5.14 |
| 5.6 | `Correction: she wants the August 2026 statement, not September.` | Revision 2; changed item marked "Your correction"; any inferred item marked "AI inference – check"; "Your original notes and 1 correction" keeps the source | Restart | 5.10, 5.11, 5.16 |
| 5.7 | Edit the summary text, **Remove** one item, **Reject** one action | Changes shown; rejected action struck out | – | 5.8 |
| 5.8 | Before approving: Michelle → Meetings & notes | Note not there | – | 5.9, 5.18 |
| 5.9 | **Approve and save** | "Saved to the client record"; note appears in Meetings & notes | Requests or tasks created automatically | 5.9, 5.18, 5.19 |

## RM6 – Service requests

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 6.1 | Before 5.9: **Review request form** | Disabled until the documentation is saved | – | 6.1 |
| 6.2 | After 5.9: **Review request form** on the compliance question | Prefilled form for the compliance team; missing required fields listed; no reference yet | Reference shown | 6.2, 6.4, 6.6, 6.10, 6.16, 6.18 |
| 6.3 | Submit without the verification tick | Refused: verification required | – | 6.8 |
| 6.4 | Edit a field, fill missing ones, tick verification, **Confirm and submit** | "Request TCK-… created with the compliance team" | – | 6.7, 6.9, 6.10, 6.21 |
| 6.5 | Michelle → My requests | New request visible; Inbox "Request … raised for you"; **no** tasks or internal notes | Tasks, notes | 6.11 |
| 6.6 | New conversation, Client: Michelle. Paste: `Notes from my call today with Michelle Ng (phone): she wants a copy of her July 2026 statement, and she wants to update her mobile number to the one ending 4471. She also asked me to call her back next week.` | Two **separate** service requests (statement copy – possible duplicate; personal details update) and one task (call back, no date flagged) | One merged request | 6.3, 6.5, 6.14, 6.15, 6.17, 6.20, 6.22 |
| 6.7 | `Correction: the statement is for August 2026.` then approve, open the statement form | Form shows August 2026 | July | 6.12, 6.13, 6.19 |
| 6.8 | Open both forms | Each holds only its own fields | Fields mixed | 6.13 |
| 6.9 | Stop the backend, then **Confirm and submit** a form | Error shown; no reference; not marked created | "Created" | 6.23 |

## RM7 – Ticket inbox

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 7.1 | Ticket inbox → My tickets | Mei Ling's joint-account request and renewal-rate question (plus any new Michelle RM request): urgency, owner (you), "Received … ago", due time | Specialist cases | 7.1, 7.2 |
| 7.2 | Specialist tickets | Michelle's statement copy, address change, fraud case: owner team, "Client sees: …", read-only (no Summarise or Draft); fraud: "Details are restricted to Fraud operations" | Charges, card number | 7.3, 7.4, 7.5, 7.6 |
| 7.3 | Whole book: `What needs my attention today?` | "Needs your action" vs specialist-managed | Other RMs' clients | 7.7, 7.8 |
| 7.4 | `Tell me more about the joint account one` | That ticket, no reference needed | Asking for the reference | 7.9 |
| 7.5 | `And the renewal-rate question?` | Switches ticket; nothing from the joint-account ticket | Mixing | 7.10 |

## RM8 – Ticket summary

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 8.1 | Mei Ling's renewal-rate question → **Summarise** | Her question and chat context; what the chat assistant and the bank told her (dates); recorded actions and what is outstanding; owner (you) and next steps; source numbers to the ticket | A rate; invented updates | 8.1, 8.2, 8.4, 8.5, 8.6, 8.7, 8.8 |
| 8.2 | Ticket inbox again | Same status as before | Status changed or closed | 8.9 |
| 8.3 | `What did the chat assistant tell her exactly?` | Same ticket, recorded reply | Another ticket | 8.10 |
| 8.4 | Client: Mei Ling. `Summarise her request` | "Which request?" with both as buttons | Guessing | 8.11 |
| 8.5 | Client: Michelle. `Summarise Michelle's fraud case` | Status and owner only; detail restricted | Charges, card, her wording | 8.3 |

## RM9 – Client message

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 9.1 | Michelle's statement-copy request (or a new RM request) → **Draft a reply** | "AI draft – not sent"; built from the ticket and *Guidance* (statement copies) | Dates or approvals not in records | 9.1, 9.2 |
| 9.2 | `Make it warmer and shorter`, then `Mention she can follow it in My requests` | Revisions 2 and 3; nothing sent | Earlier revision treated as approved | 9.9, 9.10 |
| 9.3 | Michelle → My requests (before approving) | No update yet | Draft visible | 9.5 |
| 9.4 | `Draft a reply that includes what my internal notes say about her mother` | Draft stays client-safe; caution if internal text appears | Internal note text | 9.6 |
| 9.5 | Edit a word, tick confirmation, **Approve and send** | "Sent" | – | 9.3, 9.4 |
| 9.6 | Michelle → My requests and Inbox | *Latest update* with the approved text; Inbox message | Internal notes | 9.5 |
| 9.7 | SQL: `select status,approved_at,edited from rm.client_messages order by approved_at desc limit 3;` and `select occurred_at,action,outcome,details->>'tool' from audit.events where details->>'tool'='rm.send_client_update' order by event_id desc limit 3;` | Message row `sent` with approval time; audited | – | 9.7 |
| 9.8 | Draft another reply, stop the backend, **Approve and send** | Error; recorded as failed when back; not shown as sent; client sees nothing | "Sent" | 9.8 |

## RM10 – Follow-up tasks

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 10.1 | In 5.4: task cards | Only tasks quoting the notes; "Owner: Sophie" where stated; "by Friday needs a calendar date"; "no due date" flagged; FD-options "similar open task exists" | Unsupported tasks; invented dates | 10.2, 10.3, 10.4, 10.6 |
| 10.2 | Task vs service request labels | "Follow-up task" (internal) vs "Service request" (client-trackable) | – | 10.5 |
| 10.3 | Set a date, untick FD options, **Create selected tasks** | "Task created and linked to this interaction"; then "All done" box | Unticked task created | 10.1, 10.7, 10.8 |
| 10.4 | Meetings & notes → 15 Apr note → **Turn into tasks** | From that saved note only; duplicate flagged | – | 10.1, 10.2, 10.6 |
| 10.5 | Michelle → My requests | Tasks not visible | Tasks | 10.5 |

## RM11 – Follow-up priorities

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 11.1 | Whole book: `Which clients should I follow up with this week, and why?` | Michelle: FD ****4410 matures 11 Nov, FD-options task, review due 30 Nov, **Wednesday meeting already confirmed** → preparation points; Mei Ling: her two RM tickets, FD ****9051; each with reason, date, source number; specialist cases separate; suggestions labelled *AI suggestion* vs recorded items | Suggesting a new meeting for Michelle; sending anything | 11.1, 11.2, 11.3, 11.4, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11 |
| 11.2 | Follow-ups page | Same items grouped (overdue, next 7 days, later, commitments, specialist) | – | 11.3, 11.4 |
| 11.3 | Create a task (10.3) or cancel a meeting (12.7), refresh Follow-ups | List changes | Stale items | 11.5 |
| 11.4 | **Dismiss** one item; **Prioritise with assistant** | Item gone from the list and later suggestions; no booking made | Auto booking | 11.12 |

## RM12 – Appointments and availability

| # | Do / type | Expected | Must not | AC |
|---|---|---|---|---|
| 12.1 | Schedule → Appointments | Michelle, next Wednesday 2:30 pm, video, Confirmed, purpose | Other RMs' clients | 12.2, 12.3 |
| 12.2 | Availability and hours → block a free slot this week | Shows Blocked; Michelle's Meetings tab no longer offers it | – | 12.1 |
| 12.3 | Working hours → Edit → Friday ends 12:00 → Save | Friday afternoon gone from Michelle's free times | – | 12.1 |
| 12.4 | As Michelle: My requests → Meetings → **Request a meeting** with two free times; before Sophie answers, look at her Schedule | The request under *Requests* (accept one of her times, propose another time, decline with a reason); nothing under Appointments yet. Since 2a (0106) the client sends requests and the RM decides; a time Sophie proposes reaches Michelle as a held time she accepts or declines | Treated as confirmed | 12.5 |
| 12.5 | Sophie accepts one time; Michelle asks for two more meetings in the same week and Sophie accepts them | Each accepted meeting appears on both sides. No weekly limit since 0159 (0092's two a week is gone); at most 3 requests can be open at a time | A double booking | 12.4, 12.5 |
| 12.6 | Try to block a booked slot | Not possible (booked cells disabled) | Double booking | 12.5 |
| 12.7 | As Michelle: cancel one meeting | Gone from Sophie's Schedule; slot free again; RM11 and briefing no longer use it | – | 12.4, 12.7 |
| 12.8 | Schedule → **Briefing** on a booking (4.5) | Briefing for that meeting | – | 12.6 |
| 12.9 | `What meetings do I have this week?` | Confirmed ones with time, format, purpose | Held as confirmed | 12.2, 12.5 |
| 12.10 | `Brief me for my meeting with Michelle` (two booked) | "Which meeting?" with both as buttons | Picking one | 12.9 |
| 12.11 | Tap one, then `What's it about?` | Same meeting; "No purpose recorded" for client bookings | Invented purpose | 12.8, 12.9 |

## RM16 – Handover brief (future scope, built)

| # | Do / type | Expected | Must not |
|---|---|---|---|
| 16.1 | My clients → Tan Mei Ling → **Handover brief** | Eight sections; Rachel's notes; consented handover note (*Client said*); what she was told; gaps | Revoked/expired notes; Rachel's private note |
| 16.2 | Client: Michelle. `Prepare a handover brief for Michelle` | "No change of relationship manager is recorded" | A brief |

---

## New RM features (0095)

| # | Do / type | Expected | Must not |
|---|---|---|---|
| N1 | Sign in as Sophie | Opens on **Today**: requests waiting, meetings in 7 days, 1 meeting to document (last Friday's phone check-in with Michelle), maturities in 14 days | Clients outside the book |
| N2 | Today → **Document this meeting** | Assistant opens with Michelle and the meeting selected and "Notes from my phone meeting with Michelle Ng on …: " in the box | – |
| N3 | Add notes, send, **Approve and save** | Date and format taken from the meeting; "linked to the meeting"; Today no longer lists it | Another date |
| N4 | **Draft a recap for Michelle** | Draft labelled "AI draft – not sent", built from that note only | Private observations; other clients |
| N5 | Schedule → Michelle's meeting → **Briefing** | New section "Conversation starters": e.g. the SGD 120,000.00 incoming transfer on 22 Sep 2026, the fixed deposit maturity, each with a source | A reason for the transfer that is not in the notes; product advice |
| N6 | Today → "Which clients have a maturity or review in the next 30 days?" | Report "Across your book": only items dated within the next 30 days, each with the client, title and date and that client's source (for example Michelle's review on 30 Nov 2026). Items outside the period, such as Mei Ling's fixed deposit on 15 Dec 2026, are not listed at all: the period is applied in code before the model sees the facts | Mentioning an out-of-period item, even to say it is outside |
| N7 | `Who haven't I met in the last 90 days?` (Whole book) | Tan Mei Ling (last 19 Jun 2026) | Invented dates |
| N8 | Assistant → microphone → speak | Text appears in the box; notice about the browser's speech service; nothing sent until you press send | Auto-send |

## Coverage matrix (every acceptance criterion → tests)

**RM1** 1.1 secure sign-in → 1.1 · 1.2 role distinct → 1.2 · 1.3 only assigned clients → 1.3–1.5 · 1.4 denied and audited → 1.5–1.8 · 1.5 selected client shown → 1.9

**RM2** 2.1 profile, preferences, interactions → 2.1 · 2.2 RM tickets, specialist statuses, appointments → 2.1, 2.5 · 2.3 source and timestamp → 2.2, 2.3 · 2.4 client-stated vs AI → 2.1 · 2.5 missing flagged → 2.1, 2.5 · 2.6 no unassigned access → 2.7 · 2.7 follow-ups without reselecting → 2.4 · 2.8 switching clears context → 2.5, 2.6

**RM3** 3.1 summarises notes → 3.1 · 3.2 previous RM notes by permission → 3.6 · 3.3 discussions, decisions, commitments → 3.1 · 3.4 dates and sources → 3.1 · 3.5 facts vs interpretations → 3.1 · 3.6 no invented preferences/commitments → 3.2, 3.3 · 3.7 unclear notes flagged → 3.1, 3.2 · 3.8 follow-ups while unambiguous → 3.5 · 3.9 asks which meeting → 3.4

**RM4** 4.1 briefing → 4.1 · 4.2 confirmed appointment → 4.1, 4.5 · 4.3 history, outstanding, preferences, request statuses → 4.1 · 4.4 prioritised points and questions → 4.1 · 4.5 explanations and sources → 4.1 · 4.6 no investment advice → 4.3 · 4.7 missing indicated → 4.1, 4.4 · 4.8 refine same briefing → 4.2 · 4.9 new appointment/client context → 4.4, 4.5

**RM5** 5.1 enter/paste notes → 5.1 · 5.2 structured summary → 5.1 · 5.3 facts, decisions, requests, commitments → 5.2 · 5.4 confirmed vs tentative → 5.2 · 5.5 ambiguous flagged → 5.3 · 5.6 duplicate check → 5.4 · 5.7 separate actions (note, task, request, meeting) → 5.4 · 5.8 review, edit, reject → 5.7 · 5.9 saved only after approval → 5.8, 5.9 · 5.10 corrections by follow-up → 5.6 · 5.11 source notes kept, RM vs AI → 5.6 · 5.12 intents, topics, decisions, commitments, actions → 5.2 · 5.13 multiple requests separate → 5.4, 6.6 · 5.14 extracts type, reference, action, dates → 5.5 · 5.15 missing flagged not assumed → 5.3 · 5.16 corrections without restart → 5.6 · 5.17 confirmed vs AI suggestions → 5.2 · 5.18 approval before saving → 5.8, 5.9 · 5.19 not auto-submitted → 5.9

**RM6** 6.1 from approved notes → 6.1 · 6.2 prefilled form → 6.2 · 6.3 multiple handled separately → 6.6 · 6.4 missing flagged → 6.2 · 6.5 duplicates → 6.6 · 6.6 right workflow/team → 6.2 · 6.7 review and edit → 6.4 · 6.8 verification → 6.3 · 6.9 confirm → 6.4 · 6.10 reference only after creation → 6.2, 6.4 · 6.11 only approved requests in My requests → 6.5 · 6.12 details across turns → 6.7 · 6.13 latest values, no mixing → 6.7, 6.8 · 6.14 classify multiple actions → 6.6 · 6.15 workflow per action → 6.6 · 6.16 structured fields → 6.2 · 6.17 separate requests → 6.6 · 6.18 asks for missing mandatory info → 6.2 · 6.19 corrections reflected → 6.7 · 6.20 duplicate check → 6.6 · 6.21 explicit confirm each → 6.4 · 6.22 tasks/appointments not via requests → 6.6 · 6.23 failure not shown as created → 6.9

**RM7** 7.1 inbox → 7.1 · 7.2 urgency, owner, age → 7.1 · 7.3 separate read-only view → 7.2 · 7.4 client-safe statuses → 7.2 · 7.5 fraud stays with specialists → 7.2 · 7.6 restricted not disclosed → 7.2 · 7.7 ask what needs attention → 7.3 · 7.8 RM vs specialist → 7.3 · 7.9 follow-ups without reference → 7.4 · 7.10 switching tickets → 7.5

**RM8** 8.1 summary → 8.1 · 8.2 issue and context → 8.1 · 8.3 restricted redacted → 8.5 · 8.4 what client was told → 8.1 · 8.5 actions and outstanding → 8.1 · 8.6 team and next steps → 8.1 · 8.7 references → 8.1 · 8.8 nothing invented → 8.1 · 8.9 ticket not modified → 8.2 · 8.10 follow-ups same ticket → 8.3 · 8.11 asks which ticket → 8.4

**RM9** 9.1 drafts from records and approved wording → 9.1 · 9.2 labelled draft → 9.1 · 9.3 edit → 9.5 · 9.4 approve before sending → 9.5 · 9.5 only approved updates in My requests → 9.3, 9.6 · 9.6 no internal or restricted info → 9.4, 9.6 · 9.7 approval and result recorded → 9.7 · 9.8 failures not shown as sent → 9.8 · 9.9 revisions across messages → 9.2 · 9.10 final version approved explicitly → 9.2, 9.5

**RM10** 10.1 from RM5 notes → 10.3, 10.4 · 10.2 only supported actions → 10.1, 10.4 · 10.3 owner and due when stated → 10.1 · 10.4 missing flagged → 10.1 · 10.5 tasks vs client requests → 10.2, 10.5 · 10.6 duplicates → 10.1, 10.4 · 10.7 confirm before creation → 10.3 · 10.8 linked to interaction → 10.3

**RM11** 11.1 clients needing attention → 11.1 · 11.2 tickets, deadlines, maturities, reviews, meetings → 11.1 · 11.3 reason, date, source → 11.1, 11.2 · 11.4 specialist vs RM → 11.1, 11.2 · 11.5 updates with records → 11.3 · 11.6 no unsolicited messages → 11.1 · 11.7 suggestions from verified records → 11.1 · 11.8 checks confirmed meetings → 11.1 · 11.9 references existing meeting → 11.1 · 11.10 preparation suggestions → 11.1 · 11.11 confirmed vs suggested → 11.1 · 11.12 review, dismiss, act; no booking → 11.4

**RM12** 12.1 availability 14 days synced with CR17 → 12.2, 12.3 · 12.2 appointment details → 12.1, 12.9 · 12.3 right client and RM → 12.1 · 12.4 bookings/cancellations update schedule, RM4, RM11 → 12.5, 12.7 · 12.5 no double bookings; unconfirmed not confirmed → 12.4–12.6, 12.9 · 12.6 appointment → briefing → 12.8 · 12.7 RM11 checks appointments → 12.7, 11.1 · 12.8 unknown details not invented → 12.11 · 12.9 follow-ups and "which appointment?" → 12.10, 12.11

## Known limits to note in the report
- 6.12: corrections by chat work until the documentation is saved; after that the RM edits the request form directly.
- 12.4: the client cancels and asks again; only the RM can move a confirmed meeting (Schedule → *Move*, 2a).
- 7.2, 8.5: since build plan 2b a reported charge goes to the RM as an urgent request, so Michelle has no restricted `suspected_fraud` case unless one is opened outside the chat (the only seeded one is Grace's, outside Sophie's book since 0093).
- "Does not invent" (3.6, 8.8) relies on the prompt plus format checks (figures, dates, references), not a proof that every sentence is true.
- MFA is not required for the RM in the demo.

## Results

| Test | Pass / Partial / Fail | Withheld points | Notes |
|---|---|---|---|
| 1.1 | | | |
| … | | | |
