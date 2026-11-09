# Consent withdrawal requests (runbook)

*Stop using my data* (`consent_withdrawal`, migration 0146) is how a client asks DemoBank to stop using her personal data
for something the Data sharing switches do not cover, such as recording her meetings. The **Data protection team** owns
these requests and works them from the Supabase SQL editor as the project owner. There is no staff role and no screen,
the same as *Delete all my data* (`data_deletion`, 0113). Synthetic data only. The PDPA rules behind each step are in the
[PDPA baseline](../pdpa-baseline.md#withdrawing-consent).

The Data sharing switches are not part of this. Switching off transaction insights or chat insights on Data sharing (or
confirming the chat's offer) stops access **at once**, with a receipt in Activity and no ticket.

## Where requests come from

| Raised by | How | Ticket `source` |
|---|---|---|
| The client | My requests → *Raise a request* → *Stop using my data* | `form` |
| The client in chat | "Stop recording my meetings": the assistant offers the request and records it only when she confirms | `chat` |
| Her RM | A service request from a client note: the assistant drafts it, the RM confirms she checked the client's identity and approves it (`POST /rm/requests`, `rm_create_service_request`). The client gets an Inbox notice with the reference | `rm` |

Her RM sees the request in Follow-ups under *Specialist-managed, for awareness* and can send her messages on it, but cannot change its
status (`rm_send_client_update` refuses a status on a ticket her RM does not own). The target is **10 business days**:
the PDPC treats a withdrawal notice of at least 10 business days as reasonable (Advisory Guidelines on Key Concepts,
¶12.41). If it will take longer, tell her when it takes effect.

## Steps in the SQL editor

1. **Open the queue**, oldest target first:

   ```sql
   select reference, customer_id, summary, details, status, source, target_at
   from support.tickets
   where category = 'consent_withdrawal' and status in ('submitted','in_review','waiting_on_client')
   order by target_at;
   ```

2. **Start the review.** She sees "The data protection team is reviewing it." in My requests:

   ```sql
   select support.set_ticket_status('TCK-…', 'in_review', null);
   ```

3. **Keep your own record** (for example how her identity was confirmed). Internal notes are never shown to the client
   or to her RM:

   ```sql
   select support.add_internal_note('TCK-…', 'Identity confirmed on the call by Sophie Lim.');
   ```

   If you need something from her, ask with a note she can see:
   `select support.set_ticket_status('TCK-…', 'waiting_on_client', 'Please tell us which meetings you mean.');`

4. **Withdraw**, naming the category her request is about: `meeting_recording`, `transaction_insights` or `chat_topics`.
   Run it once per category:

   ```sql
   select consent.withdraw_by_request('TCK-…', 'meeting_recording');
   ```

   This ends her active grants of that category (`end_reason` `withdrawn`, audited `consent.withdrawn`), and her RM stops
   seeing that category at once. Ending `chat_topics` (*Chat insights*) also removes the insights picked up from her chats
   at once; insights she shared herself stay. For `meeting_recording` it also opts her out, so her next confirmed meetings
   are not put back under the recording notice. It returns `grants_ended` and `recording_opted_out`, and running it twice
   changes nothing. It refuses a ticket that is not an open `consent_withdrawal` request.

   **Not yet wired:** the meeting side's recording check (`meetings.recording_allowed`) does not read the opt-out yet.
   Until it calls `consent.recording_opted_out(customer_id)`, tell her RM to keep recording off for her meetings.

5. **Tell her what changed, and close the request.** Say what stopped, from when, and what the bank keeps:

   ```sql
   select support.set_ticket_status('TCK-…', 'resolved',
     'Your meetings are no longer recorded from 10 Nov 2026. Sophie will take written notes instead. Minutes already approved are kept as the law requires.');
   ```

**If the request does not rest on consent** (for example her profile basics, which the bank uses to serve her account),
skip step 4. Resolve the request with a note that says why and what she can do instead. Withdrawing consent does not
delete data (12.54): a deletion request is *Delete all my data* (`data_deletion`).

## Checks

- Her grants: `select grant_id, categories, end_reason, revoked_at from consent.grants where customer_id = … order by granted_at desc;`
- Recording opt-out: `select consent.recording_opted_out(…);`
- Audit for one request: `select action, details, occurred_at from audit.events where details->>'reference' = 'TCK-…' order by event_id;`
- To undo an opt-out in the demo: `delete from consent.recording_opt_outs where customer_id = …;` Her next confirmed meeting
  is then under the recording notice again.

Tests: `backend/tests/test_consent_withdrawal.py` (native PostgreSQL, `TEST_DATABASE_LOCAL=1`).
