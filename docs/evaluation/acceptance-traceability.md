# Client view (Michelle): acceptance criteria traceability

Checked against the finalised product backlog (client stories CR1–CR17) on 31 Oct 2026.
**Met** = implemented and covered by an automated test or a row in `assistant-prompt-tests.xlsx`.
**Partly** = the main flow works; the listed criteria still need work or a live check.
**Check** = implemented earlier and confirmed by the code review, not re-verified line by line here.

| Story | Status | Evidence (where) | Open points |
|---|---|---|---|
| CR1 Login | Met | `Login.jsx`: email/password, masked field with Show/Hide, "Forgot password?" guidance, one neutral error for every failed sign-in; personal pages locked when signed out (`LockedPage`); session expiry clears data | Live check: sign out mid-chat, then ask for a balance |
| CR2 General help (no sign-in) | Met | Public answers from approved articles (`knowledge.py`, kb tables); guest personal questions get "please sign in" (`guardrail.py` guest_balance); "Go to …" page buttons (`pageLinks.js`); overseas transfer article (0085) | "What about that fee?" follow-ups depend on the AI: test sheet rows |
| CR3 Document checklist | Check | `ChecklistPreparation.jsx`, `compliance.js`: Not Prepared / Prepared / Submitted – Awaiting Review / Unknown; bank outcome shown separately ("Accepted by bank", "Correction requested by bank") | Re-check wording "Correction Required" vs "Correction requested by bank" with the team |
| CR4 Upload | Check | `ComplianceWorkspace.jsx`: file types and size shown, private storage, Submitted only after backend confirmation | — |
| CR5 Pre-check | Met | `document_templates.py`: fields, dates, pages, translation, certification; label "Preliminary Check – Not Formal Compliance Approval" | — |
| CR6 Status & information requests | Check | Compliance agent answers from the owned case; unknown never shown as missing | — |
| CR7 Reminders | Partly | In-app Inbox reminders from recorded deadlines | Team sign-off on reminders vs opt-outs/quiet hours |
| CR8 RM handover | Met | `rm_handover.py`: rules decide RM vs specialist; preview of topic, question, earlier messages and open requests with status; "Send to my RM" / "Cancel"; nothing sent until she chooses; failures explained | RM inbox view (RM stories) not re-checked here |
| CR9 Raise a ticket | Met | Confirmation before submission; possible duplicate offered ("Add to TCK-…" / "Open a new request"); different topic = new ticket (0085); withheld answers create no ticket; meetings and general answers never create tickets | — |
| CR10 Ticket status | Check | My requests shows reference, category, status, team, last update (`TicketsPage.jsx`) | RM-side same record: check on the RM interface |
| CR11 Card limit change | Met | Card limit request form; chat button pre-fills the latest amount she stated, noting corrections (`limitSummary`) | — |
| CR12 Suspicious charge / lost card | Met | Charges listed and confirmed before the urgent fraud ticket; lost card: safety steps first, then "Yes, report my card"; fraud team, not RM | — |
| CR13 Failed / pending transaction | Met | Pending Lim Interiors transfer explained; fraud wording goes to the fraud flow; case only after confirmation | — |
| CR14 Credit card enquiry | Met | `card_answers.py`: figures from records, "which card?" with one-tap replies, follow-ups stay on the card, topic switch does not carry it | — |
| CR15 Account information | Met | Balances, recent transactions, fees and "what was this charge?" from records (`transaction_answers.py`); Go to Statements | Statement month retention: live check |
| CR16 Loans | Partly | Home, personal and car loan articles (0086); no approval or rate promises | Run the five loan rows before marking complete |
| CR17 Book a meeting | Met | Free times in chat, 10-minute hold, booking summary, Confirm booking, expiry handling, My requests > Meetings | RM schedule sync (RM12): check on the RM interface |
