# Storyline × architecture: the end-to-end flow (7 Nov 2026)

How each scene of the Michelle / Sophie storyline runs through the architecture diagram (page V4, numbers ①–⑳), who
approves what, and what is still to build. Synthetic data only.

## The one rule to say in class

**The AI drafts, people decide.** Nothing is saved, sent or approved by the system on its own:

| Step | Who decides | Where it is enforced |
|---|---|---|
| Meeting request | Michelle confirms the draft; Sophie accepts a time | Approved Actions ⑬ (client offer, 30-min expiry; then the RM approves the time) |
| Brief | Sophie approves the exact version she read | Meeting graph ⑧ waits in `brief_review` (waiting = rm); SQL refuses a replaced or stale version |
| Minutes | Sophie approves | Meeting graph ⑧ waits in `minutes_review` (waiting = rm) |
| Recap, follow-ups, specialist hand-off, compliance question | Sophie reviews, edits, sends | Approved Actions ⑬, one audited change each; lifecycle `following_up` waits for rm |
| Ticket outcome | The specialist or compliance team | `support.set_ticket_status` (bank side only) |
| Collateral action | The bank | Alert is information only |

Checked in the code: the lifecycle (0132) only moves past brief and minutes after an RM action, and every approve
function (`rm_meeting_brief_approve`, `meeting_approve_pack`, minutes review) checks the RM first.

## Scene by scene

| # | What happens | Path through the diagram | Status |
|---|---|---|---|
| 1a | Michelle asks when her time deposit matures; answer with source | Web Client ① → API Gateway ③ → Input Guardrail ④ → Client graph ⑥ planner → **Accounts & Cards** agent ⑩ → Tool layer ⑫ `my_portfolio` → Customer Data ⑬ → DB with RLS ⑱ → Response Validator ⑪ → compose | Built |
| 1b | Downloads her statement | Web Client ① → Customer Data ⑬ → Document Storage | Built (the app calls it *Statements*, not Mailbox) |
| 1c | Uploads the sale agreement for her periodic review; the app checks it | **Compliance & Documents** agent ⑩ → Approved Actions ⑬ (upload offer she confirms) → Document Storage; check before submit | Built |
| 1d | "I'm thinking of buying a condo next year" passes to Sophie | Client graph ⑥ → **Chat insights** ⑭ records the topic, only because Consent ⑬ shows chat-topic sharing on | Built |
| 1e | Assistant drafts "Property financing options"; she confirms | **Relationship** agent ⑩ → Approved Actions ⑬ → meeting request (0106) → Notification ⑬ | Built |
| 2a | Today ranks the request, maturity in 4 days, review due 30 Nov | RM graph ⑦ (book check) → **RM Requests**, **RM Client**, **RM Calendar** ⑩ → priority ranking | Built |
| 2b | Sophie accepts a time | **RM Calendar** ⑩ → Approved Actions ⑬ → appointment; Michelle notified; `meeting.scheduled` queued for the Meeting worker ⑳ | Built (retest end to end) |
| 2c | Her brief (5 points, each labelled and sourced) | Meeting graph ⑧ **brief** node (`preparation.py`) → scoped readers ⑫ under Sophie's login, consent-gated ⑬ → LLM Gateway ⑮ only *chooses* references → SQL rechecks → **RM reviews the brief** | Built by the meeting team; 3 refinements below |
| 3a | Meeting in person or by video | Zoom (meeting team) or in person. Sophie advises; the AI gives no advice | Zoom: meeting team |
| 3b | Minutes from Sophie's notes or the transcript, with quotes; Sophie approves | Transcript upload or typed notes → Meeting graph ⑧ **minutes** (`writer.py`) → **RM reviews the minutes** | Built (0120–0124, 0131); new speaker dropdowns planned |
| 3c | Hand-off to the mortgage specialist, compliance question, her own follow-up | RM graph ⑦ "Document this meeting" → **RM Requests** ⑩ drafts → Validator ⑪ → on Send: Approved Actions ⑬ → tickets `financing_specialist` (0117) and `compliance_documents` (0071) + RM task → Audit Log | Built; one-review UX planned |
| 4a | Michelle gets the recap with next steps | Notification ⑬ → Background Worker ⑯ → her Inbox | Built; add "what the specialist will ask for" from the home-loans article ⑰ |
| 4b | Specialist starts from the hand-off | Ticket in the support queue (owner *Mortgage and Lombard specialists*) carries goal, timeline, context, minutes | No specialist screen: demo script needed (below) |
| 4c | Compliance answers from the request alone | Ticket (owner *Compliance review team*) carries the question and the document | Same |
| 5a | The next brief holds the whole story | Meeting graph ⑧ brief reads meeting history, approved commitments (0134) and open requests | Built |
| 5b | Collateral below the limit → alert with source | `44_demo_lombard_alert.sql` → `rm.check_collateral` → Today *Do first* + brief alert source (0139) | Built |
| 5c | A client who changed RM keeps her history: **Tan Mei Ling** (1007) moved from Rachel to Sophie on 26 Oct (team restructure) | RM assistant → pick Tan Mei Ling → "Prepare a handover brief" → RM graph ⑦ **handover brief** (RM16, 0073/0090): Rachel's notes, open request, preferences, the handover note she consented to; Rachel's private note and notes with expired or withdrawn consent are left out | Built and seeded (0090) |

Every request also passes Identity Provider ② (JWT) and API Gateway ③, is traced in Tracing ⑤ / LangSmith, and every
read and write lands in the Audit Log.

## Specialist and compliance: what happens after Send

1. Sophie presses Send. Approved Actions ⑬ opens a ticket per item (`support.open_ticket`), owned by the team, with the
   request fields and the approved minutes as context. Audit Log records it.
2. Michelle gets one Inbox notice when the ticket is opened (`rm.request_raised`: "Your relationship manager raised
   request TCK-… with the Mortgage and Lombard specialists on your behalf. Check My requests for updates."). It
   shows in **My requests**. Sophie sees it in **Ticket inbox** as specialist-managed (she can't change its status).
3. The team replies. There is no specialist persona in the app, so in the demo `45_demo_specialist_reply.sql`
   records one reply from each team with `support.set_ticket_status(reference, status, '<public note>')`
   (statuses `in_review`, `waiting_on_client`, `resolved`, `closed`).
4. The note reaches Michelle in **My requests** (*Latest update*) and Sophie in **Ticket inbox**, and the next brief
   lists the request with its new status. A status change sends **no Inbox notice** (it writes a ticket event and
   an audit row, no outbox event), so in the demo open My requests to show her the reply.

Say in class: "The specialist and compliance teams work in the bank's own case tools. We show the hand-off and
their reply arriving; their screens are out of scope."

## One brief writer

There are no longer two in use. The meeting team's migration 0139 made both RM screens (Briefing and the meeting
lifecycle) use their lifecycle brief (`meetings.briefs`, `preparation.py`). Our 0118 versions stay as history only
and are hidden if their sources changed. Their writer is the safer design: the model only picks record references
and question codes; all text comes from records.

Refinements so it produces the storyline brief:

1. **Time deposit maturity** as its own record (today only the portfolio total is a source). Add the maturity
   milestone to `meeting_preparation_inputs`.
2. **"Possible options to discuss"** as an AI suggestion: a new question code that names the time deposit,
   a Lombard loan and property financing, with "based on the points above, not advice".
3. **Display names** in the card: Client said / Record / AI suggestion, and sources shown as Chat, Account,
   Portfolio, Compliance case.

## Built on 7 Nov (second pass)

1. Brief: one writer, the meeting team's lifecycle brief. 0140 adds the maturing time deposit as its own Record and
   the `discuss_funding_options` AI suggestion (property financing or investments agenda only); brief_writer v4;
   labels Record / Client said / AI suggestion and sources named Chat, Client record, Compliance case, Requests.
   Hosted: `46_apply_0140_hosted.sql`.
2. Meeting page: Schedule › Appointments › **Open meeting** shows one page with the four steps. Document has two ways
   in (upload transcript, with "who is who" dropdowns read from the file; or typed notes). Minutes are approved in
   step 3; step 4 shows follow-ups, recap and hand-offs and sends the selected items with one press (each still its
   own audited approval). Hand-offs open the assistant's "Document this meeting" with the approved minutes.
3. Assistant: no microphone; "Needs you now" (top 3 from Today), starters in four tabs (Start my day, Before a
   meeting, After a meeting, Any time) and a **Next:** button after each answer.
4. `45_demo_specialist_reply.sql`: the bank teams' replies for scene 4.

Still open: editing follow-up and recap wording after the minutes are approved (today: request changes to the
minutes before approving), and a demo date so the maturity reads "in 4 days".

## Wording to align

- Decided: the topic is **Property financing** everywhere (app labels already use it; say it in the storyline too:
  "Property financing options", topics *Property financing, Investments*).
- Decided: borrowing against the portfolio is a **Lombard loan** (Lombard financing article, collateral alert).
- Mailbox vs **Statements** in the app.
- The Lombard article covers both a variable loan facility and a **fixed term advance**; the mortgage article says
  variable rate with extra repayments allowed. Say "in this demo", not UBS's actual terms.
- The Lombard top-up line ("the bank may ask for more collateral or repayment") is from UBS's public terms: cite it.
