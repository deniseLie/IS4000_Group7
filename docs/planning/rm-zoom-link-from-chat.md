# Create a Zoom link from the RM chat (high-level plan)

Plan written 8 Nov 2026. It replaces the read-only Zoom status idea in [rm-zoom-status-tool.md](rm-zoom-status-tool.md), which is no longer needed. Nothing is built yet.

## What the RM can do

She types "Set up a Zoom link for Thursday's meeting with Michelle". The RM Calendar agent finds the meeting, checks it can get a link, and shows a card. She presses **Approve** and the link is created. The card then shows "Zoom link created" and the meeting shows *Join Zoom meeting* as it does today.

## The one rule we keep

The RM assistant never writes by itself. Every change is a card the RM approves (AGENTS.md: "The assistant never writes; every write is a separate approved request"). So the agent **prepares** the Zoom link and the RM's **Approve** creates it. This works like the meeting-request cards (`decide_request`) and the availability cards today.

No new Zoom code is needed. Approve calls the routes that already exist:
- `POST /meetings/{id}/zoom/automated` when `ZOOM_CAPTURE_ENABLED` is on (it also records the call for start/end messages and notes)
- `POST /meetings/{id}/zoom` otherwise

The Schedule page's *Create Zoom meeting* button (`MeetingWorkspace.jsx`, around line 220) already picks between the two the same way.

## What changes

### 1. A new Calendar intent: `zoom_link`
- `services/agents/registry.py`: add `zoom_link` to `RM_CALENDAR.intents` (reads `rm_appointments`) and to its `answers` text.
- `schemas/rm.py`: add `zoom_link` to `Intent`.
- New planner prompt `rm_planner/v7` (copy of v6) with one line: "zoom_link: she wants a Zoom link or video call set up for a meeting. Put the day or time she names in appointment_hint." Bump `prompts.VERSIONS`, add `docs/prompt-log/rm_planner/v7.md`, pin it in `tests/test_prompts.py`. v6 stays as it is.

### 2. A handler that builds the card by code (no model)
`services/rm/handlers.py`, `h_zoom_link`, built like `h_decide`:
1. Read her appointments (`rm_appointments`).
2. Find the meeting from the day, time or client (`match_appointments`). If several fit, ask which. If none fit, say so.
3. Check, in code:
   - it is confirmed
   - it is a video meeting
   - it hasn't started yet
   - it has no link yet
   - Zoom is set up (`ZOOM_OAUTH_DIRECTORY`)

   If any check fails, reply with the reason and no card (for example "This meeting already has a Zoom link" or "This is a phone meeting").
4. If all pass, return a `zoom_link` card: the meeting id, client, time, and which route Approve will use.

No model call, so there is no AI-written text to check. Add it to `HANDLERS`; it needs no `TASK_FILE` entry.

The "has a link already" check needs a link read the Calendar agent can use. Either:
- (a) add `has_zoom_link` to `rm_appointments` in a small migration (0150, one boolean, no URL), or
- (b) call `GET /meetings/{id}/zoom` from the card in the browser before Approve.

**Recommended: (a)**, so the chat can say "already has a link" without a second request.

### 3. The card in the chat
- `frontend/src/components/rm/CalendarReview.jsx`: a `ZoomLinkReview` card next to the decision and availability cards. It has:
  - the meeting line
  - "I approve creating a Zoom meeting. The client sees the link."
  - **Approve** and **Not now** buttons
  - on success, "Zoom link created" with the join button
  - on failure, the route's error in plain words
- `frontend/src/components/rm/RmAnswer.jsx` and `lib/rm.js`: show the card when the reply carries `zoom_link`; add a label for the intent.
- The routes already return the existing link when there is one, so pressing Approve twice can't make two meetings.

### 4. Optional: the open-question loop
Not needed for the first version. The planner sends link requests straight to `zoom_link`. Create stays out of `loop_tools`, because no model-facing tool may write.

## What does not change

- `services/meetings/zoom.py`, `api/meetings.py` and the meeting side's migrations. The meeting side owns these, and their checks (RM only, confirmed, future, Zoom configured) still run on Approve.
- The client side. Clients still can't create links.
- Automatic creation by the worker when `ZOOM_CAPTURE_ENABLED` is on. The chat card is mostly for when that is off, or when it failed.

## Tests

- **Backend** (`test_rm_assistant.py`, `test_rm_graph.py`):
  - the phrases route to `zoom_link`
  - each failed check gives its reason and no card
  - an ambiguous day asks which meeting
  - a meeting outside her book is refused
  - no model call is made
- **Planner evaluation** (`evaluation/planner_cases.json`): 3–4 cases, plus "what's on Thursday" staying `schedule`.
- **Frontend** (`rm.test.js`): the card's state; Approve calls the right route; a second Approve doesn't create twice.
- **Migration** (if option a): a native test that `has_zoom_link` is true or false and the URL is never returned.

## Docs

- `AGENTS.md`: RM assistant row (Calendar agent's `zoom_link` card)
- `docs/architecture.md` §14.11
- `docs/workflows/workflow-2-relationship-manager.md`

## Questions to settle

1. Option (a) or (b) for "has a link already". (a) touches `rm_appointments`.
2. Should the card also appear on its own after the RM accepts a video meeting request in chat ("Create the Zoom link now?")? That's a small add-on to the `decide_request` card.
