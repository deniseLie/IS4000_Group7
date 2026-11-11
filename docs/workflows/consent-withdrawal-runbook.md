# Consent withdrawal requests (runbook, retired)

*Retired on 8 Nov 2026 by migration 0147 (`instant_consent_only`, hosted batch `52_apply_0147_hosted.sql`). Checked 11 Nov 2026.*

This runbook described *Stop using my data* (`consent_withdrawal`, 0146): a request the Data protection team worked
from the Supabase SQL editor with `support.add_internal_note` and `consent.withdraw_by_request`. 0147 removed that flow
and *Delete all my data* (`data_deletion`, 0113): open tickets of both categories were closed with a note, neither can
be raised any more, and those functions, `consent.recording_opted_out` and `consent.recording_opt_outs` were dropped.
There is nothing left to work from the SQL editor.

Consent now changes only through controls that act at once, with no request to a team:

- **Data sharing** (account menu): the switches for transaction insights and chat insights, and *Meeting recording*,
  where she can agree once to transcripts for every meeting (0149).
- **Each meeting** (Meetings → *Open meeting*): *I agree* or *Withdraw* for that meeting's transcript (0122), or
  *Not for this meeting* when she agreed for every meeting. Approved minutes stay.
- **In the chat**, "stop recording my meetings" gets a fixed reply that says where *Withdraw* is
  (`consent_answers.RECORDING`); no offer, no ticket.

The rules and the flow are in the [PDPA baseline](../pdpa-baseline.md#withdrawing-consent). Tests: `backend/tests/test_consent_v2.py` (including the 0147 removal).
