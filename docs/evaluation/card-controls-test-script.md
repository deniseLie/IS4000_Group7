# Card controls test script (CR19, migration 0096)

Sign in as Michelle (customer 1002). Run `26_apply_0096_hosted.sql` on Supabase first. Her cards: Signature Card ending 7310 (limit SGD 20,000, owes SGD 5,169.60) and Travel Card ending 5592 (limit SGD 10,000). To start again, rerun `17_reset_michelle_demo.sql` and set both cards back to active:
`update banking.credit_cards set status='active' where customer_id=1002;`

| # | Steps | Expected result | Proves |
|---|---|---|---|
| K1 | Overview → Cards → *Manage* on the Signature Card | Four controls: Freeze card, Change limit, Report lost, stolen or damaged, Dispute a charge. A note says unrecognised charges go to the fraud team and only the bank cancels a card. | Controls are visible per card |
| K2 | *Freeze card* | A red box explains what freezing does (new payments declined, past payments not reversed, recurring payments may fail) with *Cancel* and *Freeze card*. Nothing changes yet. | Exact effect shown before confirming |
| K3 | *Cancel* in K2 | Box closes; the card stays active; Actions shows the proposal rejected. | Rejecting changes nothing |
| K4 | *Freeze card* → *Freeze card* | "Card ending 7310 is frozen." The card shows a blue *Frozen* badge. Actions has a receipt; Inbox: "Card frozen". | Client freeze, receipt, notice |
| K5 | *Manage* → *Unfreeze card* → confirm | "Card ending 7310 is unfrozen." Badge gone. | Unfreeze of her own freeze |
| K6 | In SQL: `update banking.credit_cards set status='temporarily_blocked' where card_id=10021;` then reload Overview → *Manage* | *Unfreeze card* is disabled: "The bank froze this card, so only the bank can unfreeze it…" | Bank freeze stays with the bank |
| K7 | Card active again. Chat: "I don't recognise the London charges on my card" → "Yes, report it". Then Overview → *Freeze card* (confirm) → *Manage* | The fraud reply says she can freeze the card herself now. She can freeze it; *Unfreeze card* is then disabled: "The fraud team is reviewing a case…" | Freeze allowed during fraud, unfreeze not |
| K8 | Travel Card → *Report lost, stolen or damaged*. What happened: Lost; tick the declaration; review and confirm in Actions | Request `TCK-…` with Card operations team, urgency high. The Travel Card is *Frozen* at once. My requests shows "Card ending 5592: frozen when reported". *Unfreeze* is disabled: "You reported this card lost…" | Lost card frozen on report |
| K9 | Repeat K8 for the same card | The same reference comes back ("already open"), no duplicate. | One replacement per card |
| K10 | Damaged instead of Lost, with a *When you last had it* date | Refused: "Only give the date you last had the card for a lost or stolen card." | Field rule |
| K11 | Signature Card → *Change limit*: new limit 25500, permanent, Change in income, declaration ticked → *Review request* | Refused in the form: "Choose a new limit in steps of SGD 1,000, from SGD 1,000 to SGD 50,000. Change the details above and review again." | Steps of 1,000 |
| K12 | New limit 20000 | "Your card ending 7310 already has a limit of SGD 20,000." | Not today's limit |
| K13 | New limit 5000, permanent, Lower my spending | "A new limit cannot be lower than what you owe on this card (SGD 5,169.60)." | Not below the balance |
| K14 | New limit 15000, temporary, any end date | "A temporary change can only raise the limit…" | Temporary = increase |
| K15 | New limit 25000, temporary, end date 4 months away | "A temporary limit can last from tomorrow up to 90 days." | 90-day cap |
| K16 | New limit 25000, permanent, declaration **not** ticked | The form lists the declaration as still needed; if sent anyway the bank refuses: "To ask for a higher limit, confirm that your income and employment details are up to date." | Declaration for a higher limit |
| K17 | New limit 25000, permanent, Travel, declaration ticked → confirm in Actions | `TCK-…` with the Cards and credit team. The limit itself does not change. | Valid request recorded, not decided |
| K18 | In SQL: `select support.set_ticket_status('<K17 ref>','resolved','Approved.');` then ask for 30000 on the same card | "The limit on your card ending 7310 was last reviewed on <date>. You can ask again from <date + 30 days>." The Travel Card can still ask. | Once per card every 30 days |
| K19 | Chat: "I want to raise my card limit to 15500" | The assistant explains steps of SGD 1,000 and offers SGD 15,000 or SGD 16,000 before the form. | Chat applies the same rule |
| K20 | Overview → Recent activity → *Dispute* beside the Orchard Bistro charge | *Dispute a card charge* opens with the card and the charge already selected (charge list shows only posted card charges from the last 60 days). | Dispute from a transaction |
| K21 | Reason: Charged twice; contacted merchant: Yes; what happened; tick the declaration → confirm in Actions | `TCK-…` with the Card disputes team; My requests shows "Charge: 27 Oct 2026 · Orchard Bistro, Singapore · SGD 68.20". | Dispute recorded with the charge in words |
| K22 | Dispute the same charge again | Same reference returned, no duplicate. A different charge opens a second dispute. | One dispute per charge |
| K23 | RM side (Sophie): document a call where Michelle reports a damaged card | The proposed service request can be *Replace a lost, stolen or damaged card*; the declaration shows as "The client confirms: …" for Sophie to tick after checking with her. | Same rules through the RM path |

Automated: `backend/tests/test_card_controls.py` (K2–K22 in SQL), `test_card_self_service.py`, `test_tickets.py`, `test_card_answers.py` (K19), `frontend/src/lib/cardControls.test.js`.
