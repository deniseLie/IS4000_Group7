# Client card-limit request: date and form handoff (31 Oct 2026)

## Bug
When a client gave **2028** and later **20 December** in separate turns, the
chat remembered part of the date but the **Raise a card limit request** handoff
only copied a text summary. The `Needed until` date input stayed blank. Other
fields such as the corrected amount and temporary/permanent choice also did not
carry across as structured fields.

## Change
- `frontend/src/lib/ticketData.js`: `limitRequestPrefill(messages)` extracts a
  bounded, client-supplied request state from **both user and assistant turns**.
  It combines a verified day/month/year to ISO `YYYY-MM-DD` for the native date
  field, including when the date and year are supplied in separate messages.
  It does not guess missing dates or card digits; it validates calendar dates.
- `frontend/src/Chat.jsx`: passes the structured draft (`summary`, `details`,
  selected card label) when the client clicks **Raise a card limit request**.
- `frontend/src/components/TicketsPage.jsx`: fills the date, amount, duration and
  reason fields from the draft, asks the client to enter the last four digits
  of the selected card, and requires an end date for temporary requests.
  No banking request is submitted by this handoff; the client must review and
  confirm the eventual action using the existing workflow.
- `frontend/src/lib/ticketData.test.js`: regression tests for date order,
  corrections, invalid/incomplete dates, amount and context boundaries.

## Expected example
User answers: `2028`, then `20 December` to the chatbot's date questions.
The **Raise a card limit request** form should show:

- **Requested limit:** `12000` (after correcting the original `15000`)
- **Temporary/permanent:** `temporary`
- **Needed until:** `20/12/2028` in the local date control (`2028-12-20` as value)
- **Reason:** `Travel`, if the user explicitly mentioned travelling
- **Short summary:** contains `End date: 20 December 2028`
- **Card last 4:** not filled from a card name; requires verified user input.

If only the year or only day/month is known, **Needed until** stays blank and
remains required for a temporary change. The short summary indicates what is
missing rather than using a guessed date.

## Tests
- `cd frontend && npm test`: **105 / 105 passed** (Node built-in unit tests).
- `cd backend && PYTHONPATH=. python scripts/check_client_intelligence.py`:
  **18 / 18 passed** on synthetic data (unchanged from v3).
- Live browser + Supabase + LLM end-to-end behaviour was **not** tested in this
  environment. Confirm using an authenticated synthetic test account.

## Applying the patch
The patch zip contains paths beneath `IS4000_Group7/`. Stop the backend and
frontend, back up the project, and extract the patch **from the parent directory**
of the existing `IS4000_Group7` folder. Restart your frontend/backend and create
**a new chat** to retest. `.env` and dependencies are not in the patch.
