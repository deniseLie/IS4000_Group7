# Zoom check inside the `schedule` intent

9 Nov 2026. Follows [rm-zoom-status-tool.md](rm-zoom-status-tool.md).

## Problem

The read-only Zoom check `zoom_meeting_check` works live: Mei Ling's 16 Nov meeting returned `set_up`, `zoom_time_matches: true`, `call: not_started`, `live: ok`. But only the Calendar agent's `open` loop can call it.

The RM planner (`rm_planner/v6`) sends questions like "Is the Zoom for my 9:30 with Michelle set up?" to the fixed `schedule` intent. `h_schedule` reads appointments only, so the answer says the records don't show the Zoom status.

## Change

When the question is about Zoom, the `schedule` handler adds the Zoom check as a fixed read. It runs for the meeting the handler already selected, or for a few upcoming meetings when none is selected. The planner, the routing and `open` stay unchanged.

## Steps

1. **Zoom wording** (`services/rm/handlers.py`)
   - Add `ZOOM=re.compile(...)` matching zoom, meeting/join/video link, "call started", "has she joined", "joined the call". Match it on the RM's message with `typos.normalise`.
   - Add `wants_zoom(t)`. It is false when the turn came from a workspace button (`t.req.intent`) that is not about Zoom.

2. **Which meetings** (`h_schedule`)
   - If one meeting is selected (from context or a single `match_appointments` hit), check that one.
   - If none is selected but the question is about Zoom, take the upcoming confirmed video meetings in the 30 days already read. Filter to the named client when there is one. Check the first 3 by start time (`ZOOM_SHOWN=3`) and add a fixed count line for the rest.
   - Keep the existing "Which appointment do you mean?" clarify when several meetings match a hint.
   - Skip in-person and phone meetings, with the fixed line "no Zoom for an in-person/phone meeting".

3. **The read**
   - Call `await t.read('zoom_meeting_check',{'appointment_id':id})` for each meeting picked. These go through MCP with the Calendar agent's context.
   - The appointment ids come only from the `rm_appointments` rows. The model never supplies them.
   - Catch `ExternalError` and `RmDenied` for each meeting. A failure adds unavailable evidence for that meeting and does not end the turn.

4. **Permission** (`agents/registry.py`)
   - `RM_CALENDAR` already has `external=ZOOM_EXTERNAL`. Check that `require_tool` allows an external tool in a fixed handler read, not only in `loop_tools`. If not, allow `spec.external` reads too.
   - Do not add it to the `schedule` intent's default `Read`s. That would call Zoom on every schedule question.

5. **Evidence** (`services/rm/evidence.py`)
   - Move the `zoom-check` builder out of `loop_items` into `ev.zoom_evidence(result, appointment)`, and use it from both `h_open` and `h_schedule`.
   - Give each item its own id: `zoom-check-<last 6 of appointment id>`. The title names the client and time. The record holds codes only.
   - Add `zoom-check-unavailable-<id>` for a failure.

6. **Prompt** (`rm_assistant/v9`, new; v8 stays as released)
   - Copy v8. In `schedule.md`, add one paragraph explaining:
     - how to read the codes (`set_up`, `none`, `stale`, `missing_in_zoom`; the time match; `not_started`, `in_progress`, `not_running`; `live`);
     - to cite the `zoom-check` item;
     - never to claim a link was made or fixed;
     - when `live` is not `ok`, to say Zoom could not be checked.
   - Bump `prompts.VERSIONS['rm_assistant']`, pin the ids in `tests/test_prompts.py`, and add `docs/prompt-log/rm_assistant/v9.md`.

7. **Tests** (`tests/test_rm_graph.py`, `tests/test_zoom_check.py`)
   - A Zoom question with a selected meeting: the tool is called once and `zoom-check-*` is cited.
   - A plain schedule question: no Zoom call.
   - A Zoom question with no selection: at most 3 checks and the count line.
   - An in-person meeting: no Zoom call.
   - Zoom down: an unavailable item, and the answer still comes back.
   - A model-supplied id is never used.
   - The trace shows `tool_call` spans with `transport=mcp`.

8. **Live check**
   - In WSL, ask as Sophie "Is the Zoom for my meeting with Mei Ling on 16 Nov set up?"
   - Expect the intent `schedule`, a `zoom-check` source, and an answer saying the link is set up, matches the time and the call has not started.
   - Also ask "Does Zoom still have my 9:00 with Michelle?" and expect missing in Zoom.

9. **Docs**
   - In `AGENTS.md`, add "also from `schedule` on Zoom wording" to the Tools and MCP row and the RM assistant row.
   - Add one line to the "Built" note in `rm-zoom-status-tool.md`.

## Not in scope

- No new intent or planner prompt.
- No Zoom writes.
- `open` keeps the tool for multi-step questions.
