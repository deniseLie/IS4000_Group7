# Zoom status for the RM Calendar agent (`zoom_status`, `meeting_capture`)

Plan written 8 Nov 2026 on `claude/rm-calendar-zoom-status-e6d1c2` (base `b01a0d0`).

**Built first (8 Nov 2026, after merging main's MCP-only tool layer):** the smaller read-only `zoom_meeting_check(appointment_id)`. It covers the link and the live meeting only, without past-meeting reads, participant counts or notes status. Pieces: migration 0150 `rm_zoom_check` (codes-only record read in her book), `external.zoom_meeting` (bearer token, 60 s cache after the book check), `tools.zoom_meeting_check`, `registry.ZOOM_EXTERNAL` on the Calendar's `external` and `loop_tools`, `h_open` meeting resolution, and MCP read-only/open-world annotations. Tests: `test_zoom_check.py`, `test_zoom_check_db.py`, and a loop test in `test_rm_graph.py`. The `meeting_capture` intent, past-meeting and participant reads and notes status below are still to do. **9 Nov:** the `schedule` intent also runs the check on Zoom wording ([rm-zoom-in-schedule.md](rm-zoom-in-schedule.md), `rm_assistant/v9`).

## What the RM can ask

- "Is Thursday's Zoom with Michelle set up?"
- "Did she join?"
- "Why are there no minutes for today's call?"

The RM Calendar agent answers from one new tool, `zoom_status`. The tool reads the call live from Zoom with the bank's own Zoom login, and reads the notes status from our records. The model only explains what the tool returns; code checks every figure and time in the answer.

## What is there today

| Piece | Where | What it means for this plan |
|---|---|---|
| External tool gateway | `services/external.py` (`fetch`, `ExternalError`, `CACHE`, `allowed_hosts`) | `fetch` makes one cached GET to an allow-listed host and audits `data.external_called {tool, host, status, ms}`. It sends **no auth header**, so Zoom needs a small change. |
| External tools in specs | `registry.EXTERNAL`, `AgentSpec.external`, `authorization.require_tool` | `require_tool` already lets a spec call anything in its `external`. `test_external.py:109` asserts `EXTERNAL == set(external.TOOLS)`, so a non-public tool needs that set split. |
| Zoom client | `services/meetings/zoom.py` (`refreshed_token`, `Zoom`) | Sync `httpx.Client` on `https://api.zoom.us/v2`. `refreshed_token(settings.zoom_oauth_directory)` rotates the own-user grant under a file lock shared with the worker (35 s lock timeout) and refuses a scope change. Only `GET /meetings/{id}` and `GET /users/me` are read today. Its error messages include Zoom's response body, so they must never reach an answer or a log. |
| Zoom link | `meetings.zoom_links` (0123), `public.meeting_link` | Returns `join_url` and `provider_id`. Raises `P1001` when the link is stale after a reschedule. |
| Original occurrence | `meetings.zoom_bindings` (0125) | Holds `provider_id`, `account_id`, `host_id`, `original_uuid`. Zoom's past-meeting endpoints want the UUID. |
| Call start/end | `meetings.accept_zoom_call` (0125), `meetings.workflows.started_at/ended_at/state` | Signed `meeting.started`/`meeting.ended` webhooks (or the RM's documented times) already set these. |
| Notes status | `meetings.zoom_fetch_jobs`, `meetings.events` (`transcript.fetch`), `meetings.zoom_evidence`, `meeting_view.zoom_capture` | A My Notes event creates one fetch job; its event goes `waiting` → `pending` → `processing` → `done` or `failed` with `last_error_code`. Evidence exists only after a transcript was fetched. |
| `meeting_zoom_evidence` | 0125 | Returns the **whole redacted transcript** (`cues`, `segments`, speaker mapping) and records a `meeting.zoom_evidence` read audit. It returns `null` until a transcript exists. |
| Lifecycle stage | `services/meetings.status()` seam → `meeting_lifecycle_status` | `stage` (`awaiting_transcript`, `minutes_review`, …), `waiting_for`, `error_code`. Needs `MEETINGS_ENABLED`. This is the seam function for "why no minutes". |
| RM Calendar spec | `registry.RM_CALENDAR` | Tools `rm_appointments`, `rm_availability`, `rm_meeting_requests`; no `external`. |
| Open-question loop | `rm/handlers.h_open`, `runner.loop`, `settings.agent_tool_calls=4` | `run()` reads `spec.tools[call.tool].free_text`, which raises `KeyError` for an external tool. It catches `RmDenied` and `PolicyDenied` but not `ExternalError`. Clients are named by book name and resolved by code (`names_match`); meetings are not resolvable yet. |
| Meeting matching | `handlers.match_appointments`, `narrows`, `h_schedule` | Day/time hints ("Thursday", "today 10am") already pick one appointment or ask which. |
| RM checks | `services/rm/checks.py` | Every figure, date and reference in an item must be in its cited records; no links. Nothing checks "joined" wording yet. |
| Appointments reader | `public.rm_appointments` (0095) | Up to 62 days, at most 30 days back, confirmed or live-held only. |

## Decisions this plan needs from you

The brief is followed except in these places. Each has a recommendation.

1. **Notes status from a new narrow reader, not `meeting_zoom_evidence`.** `meeting_zoom_evidence` returns the transcript, logs a transcript read that did not really happen, and can only say "completed". It cannot tell "none" from "in progress". **Recommended:** a new read-only SQL function `rm_zoom_status(appointment)` (migration 0150) that returns codes only. It reads the same tables the meeting side reads, so it is agreed with them and added to `test_meeting_contracts.py`.
2. **"several" is not recorded today.** `accept_zoom_note` refuses a second note for the same occurrence (`23505`) and the refusal rolls back, so nothing in our records says a second note exists. **Recommended:** keep `several` in the contract but treat it as reachable only after the meeting side records the refused note (a counter or a `zoom_note_conflicts` row in `accept_zoom_note`). Until then the tool returns at most `completed`, and the plan does not read My Notes live.
3. **"by role" means host and guests.** Zoom's participant list does not know who the client is. The host is the bank's Zoom account, which hosts every UBS Connect meeting for the RM. Everyone else is a guest. **Recommended:** return `{host, guests}` and never say "Michelle joined"; the answer says "1 guest joined besides you (Zoom does not say who)". A check withholds an item that names the client as having joined.
4. **Who owns the 0150 reader.** It reads meeting-side tables (`meetings.*`). Either the meeting side writes it or we write it with their review. It changes none of their tables or functions.
5. **Zoom scopes.** The grant's scopes are fixed: `refresh_locked` refuses a change. The past-meeting reads need past-meeting and participant read scopes. Check the `scope` string in `zoom-tokens.json`; if they are missing, re-authorise the lab app with them. Past-meeting participants may also need a paid Zoom plan. Slice 0 checks both before any code is written.

## Design

### The tool contract

`zoom_status(appointment_id)`. The appointment id always comes from code: the handler's selected meeting, or the loop's `client` + `when` resolved against `rm_appointments` in the RM's book. A model-supplied `appointment_id` is dropped, like `customer_id`. The model never sees or passes a Zoom meeting number or UUID.

What the agent receives (codes, times in Singapore words, counts):

```json
{
  "meeting": "Thu 12 Nov 2026, 10:00 am", "client": "Michelle Tan", "format": "video", "status": "confirmed",
  "link": "set_up",
  "call": "ended",
  "started": "Thu 12 Nov 2026, 10:02 am", "ended": "Thu 12 Nov 2026, 10:36 am", "minutes_long": 34,
  "joined": {"host": 1, "guests": 1},
  "notes": "in_progress", "notes_problem": null,
  "transcript_allowed": true,
  "minutes": "awaiting_transcript",
  "live": "ok",
  "checked_at": "Thu 12 Nov 2026, 10:41 am"
}
```

| Field | Values | From |
|---|---|---|
| `link` | `none`, `set_up`, `stale` (moved after the link was made), `missing_in_zoom` (Zoom returns 404) | 0150 reader, then Zoom |
| `call` | `not_started` (start in the future), `in_progress`, `ended`, `ended_processing` (our webhook says ended, Zoom has no report yet), `not_held` (start passed, Zoom has no past instance), `unknown` | Zoom, with our webhook times as a cross-check |
| `started`, `ended`, `minutes_long` | SGT words, whole minutes, or `null` | Zoom past meeting |
| `joined` | `{host: 0–1, guests: n}`, or `null` while the call is open or before it | Zoom past-meeting participants, counted by person, not by join session |
| `notes` | `none`, `in_progress`, `completed`, `several` (after decision 2) | 0150 reader |
| `notes_problem` | the fetch event's `last_error_code`, or `null` | 0150 reader |
| `transcript_allowed` | `meetings.recording_allowed(meeting)` | 0150 reader |
| `minutes` | the seam's `stage`, or `unknown` when `MEETINGS_ENABLED=false` | `services/meetings.status()` |
| `live` | `ok`, `not_configured`, `unavailable`, `not_authorised`, `plan_limit` | the Zoom reads |

Names, emails, join URLs, Zoom ids and the transcript are never in the result. The Zoom response models declare only the fields we count, so names and emails are dropped while parsing, not after.

### One call, step by step

1. `rm_records.zoom_status(ctx, appointment_id)` runs `public.rm_zoom_status` with the RM's JWT. SQL checks `rm.current_advisor()`, that she owns the appointment and `rm.in_book`; otherwise `rm.deny` (audited, `RmDenied`). It returns the record codes above plus, for the tool only, `provider_id`, `original_uuid` and `host_id`, and records one `data.read` (`tool: rm.zoom_status`).
2. If `link` is `none` or `stale`, or the format is not video, stop: no Zoom call is made.
3. Get the token: `asyncio.to_thread(refreshed_token, settings.zoom_oauth_directory)`, bounded by `EXTERNAL_TIMEOUT_S`. No directory gives `live: not_configured`.
4. `GET /v2/meetings/{provider_id}` gives `status` (`waiting`/`started`) and the topic. The topic must pass `zoom.ours()`, as `Zoom.inspect` does.
5. If the start time has passed and the call is not open: `GET /v2/past_meetings/{uuid}` (start, end, duration). The UUID is double-encoded when it starts with `/` or contains `//` (Zoom's rule). A 404 means `not_held`, or `ended_processing` when our webhook says the call ended.
6. If it ended: `GET /v2/past_meetings/{uuid}/participants?page_size=300` (at most one page). Count the host by `user_id == host_id`; everyone else is a guest, counted once per person.
7. `services/meetings.status(appointment_id, ctx)` for `minutes`, when meetings are on.
8. Drop the ids and return the result.

Steps 4–6 are one to three HTTP requests. Each is audited as `data.external_called {tool: zoom_status, host: api.zoom.us, status, ms}`. In the loop the whole tool is **one read** of the four.

A Zoom failure after step 1 does not fail the tool: the record part is still true, so the result carries it with `live: unavailable` and no live fields. Nothing is filled in from elsewhere. A SQL failure or a refusal still raises.

### Gateway changes (`services/external.py`)

- `fetch(..., token: SecretStr | None = None)` adds `Authorization: Bearer …` when given. The token is never in the cache key, the audit, a log or an `ExternalError`.
- New reasons: `not_authorised` (401/403), `plan_limit` (Zoom's paid-plan error code; confirm the exact code in slice 0), `not_configured`. A 404 that the caller expects (step 5) is audited as `success`.
- `ZOOM_HOST = 'api.zoom.us'`, `ZOOM_TTL_S = 60`. The cache is shared across RMs; this is safe only because the SQL book check (step 1) runs before every cached read. Say so in the docstring.
- Strict response models (`extra='ignore'`, declared fields only): `ZoomMeeting {status, topic, start_time, duration}`, `PastMeeting {start_time, end_time, duration}`, `Participant {user_id, id}`.
- `zoom_live(provider_id, uuid, host_id, starts_at)` is a helper, **not** in `external.TOOLS`: that table stays public data only.
- `registry.PUBLIC_EXTERNAL = {'public_holidays','fx_rate'}` (equal to `external.TOOLS`) and `registry.EXTERNAL = PUBLIC_EXTERNAL | {'zoom_status'}`. Update the assertion in `test_external.py:109`.
- `EXTERNAL_HOSTS` adds `api.zoom.us` in `core/config.py` and `.env.example`. The existing `zoom.py` calls stay as they are.

### Agent, intent and planner

- `tools.zoom_status(appointment_id)` in `services/tools.py`: the RM tool that runs steps 1–8. It is in `tools.TOOLS`, so MCP exposes it too; SQL still checks the book. `result_facts` adds `link`, `call`, `notes`, `live` (codes) to the `tool_call` span.
- `registry.RM_CALENDAR`: `external={'zoom_status'}`, `loop_tools` adds `zoom_status`, and the intent `'meeting_capture': (Read(tool='rm_appointments'), Read(tool='zoom_status'))`. Update the spec's `answers` to say "whether a meeting's Zoom call is set up and happened, who joined (you and guests) and its notes status". No exports in this plan.
- `schemas/rm.py`: add `meeting_capture` to `Intent`. It is in neither `BOOK_WIDE` nor `NEEDS_CLIENT`: a day hint alone can find the meeting.
- `rm_planner/v7` (copy of v6): one intent line, "meeting_capture: whether a meeting's Zoom call is set up or happened: the link, whether anyone joined, when it started and ended, and why there are no notes or minutes yet. Put the day or time she names in appointment_hint." Plus one line telling it apart from `schedule` (what is on her calendar, what a meeting is about).
- `prompts.VERSIONS`: `rm_planner: v7`, `rm_assistant: v9`. Released v6 and v8 stay untouched.

### Handler `h_capture` (`services/rm/handlers.py`)

1. Read `rm_appointments` from 30 days back to 14 days ahead.
2. Pick the meeting: the context's `appointment_id`; else `match_appointments` with the hint and the client; with no hint, the client's meeting nearest to now (the past one on a tie). Several matches → a clarifying question with options (the existing `clarify`). None → a record-only reply saying no meeting was found in that window.
3. Not video → evidence says so, and no Zoom read.
4. `t.read('zoom_status', {'appointment_id': ...})`.
5. One model call with task file `capture.md`, then `brief_reply` and the checks.

Add `HANDLERS['meeting_capture']`, `TASK_FILE['meeting_capture'] = 'capture'` and `REASONING` (off, like every RM intent).

### The open loop (`h_open`)

- `spec.tools.get(call.tool, WHOLE).free_text` instead of `spec.tools[call.tool]`, so an external tool does not raise `KeyError`.
- Catch `external.ExternalError` → a `zoom-status-unavailable` evidence item with no figures, as the client runner does (`runner.read`).
- A tool with an `appointment_id` parameter takes `{"client": "<name in your book>", "when": "<day or time>"}`; code resolves it with `names_match` + `match_appointments` over the meetings already read this turn (one `rm_appointments` read if none). Not exactly one match → `StepRefused('which_meeting')`.
- `TOOL_HELP['zoom_status']`: `{"client": "<name in your book>", "when": "<day or time, e.g. Thursday 10am>"}: that meeting's Zoom status: link, call start and end, how many joined (you and guests), notes status. Read live from Zoom.`
- `loop_items` adds `zoom_status` → the evidence builder below.

### Evidence and the Response Validator

- `evidence.zoom_status_evidence(result)`: id `zoom-status` (a second meeting in the same turn gets `zoom-status-2` through `ev.merge`), kind `calendar`, title `Zoom status for <client>, <meeting>, checked <time>`, `as_of` = the check time. The record is the contract above, so `checks._supports` finds every time and count the answer states.
- New check in `services/rm/checks.py`, applied to every item:
  - An item that says a meeting was joined, attended, missed, set up or linked, or talks about notes or a transcript, must cite a `zoom-status*` record. Otherwise it is withheld as `unsupported_zoom_claim`.
  - An item that says a named client (or "she"/"he") joined, attended or missed the call is withheld as `named_attendee`, because Zoom only counts guests.
- `capture.md` (in `rm_assistant/v9`) says: answer only from the `zoom-status` record; say the check time; call guests guests; explain "why no minutes" in this order: no link → call not held or still open → no notes captured (someone must start My Notes in Zoom) → transcript not allowed → notes fetch failed (`notes_problem`) → minutes waiting for her review; when `live` is not `ok`, say Zoom could not be checked and give only the record part; never suggest creating a link or changing anything (Schedule has those controls).

## Build slices

Commits on this branch, then merge to `main`.

| # | Slice | Files | Done when |
|---|---|---|---|
| 0 | Probe (you, with the lab grant) | none in git | The grant's `scope` string is known and has the past-meeting and participant read scopes (re-authorise if not). The three GETs work on one ended synthetic meeting, or the plan-limit error is known. Responses are saved with names and emails replaced as `tests/fixtures/external/zoom_*.json`. |
| 1 | Records reader | `supabase/migrations/0150_rm_zoom_status.sql`, `services/rm/records.py`, `tests/test_zoom_status_db.py`, `tests/test_meeting_contracts.py`, hosted batch file | Native tests (`TEST_DATABASE_LOCAL=1`): each `link` and `notes` state, stale after a reschedule, outside the book is refused and audited, one `data.read` per call. |
| 2 | Zoom gateway | `services/external.py`, `core/config.py`, `.env.example`, `tests/test_zoom_external.py` | Unit tests on the saved responses: only declared fields survive (the fixture's names and emails appear nowhere in the result), host/guest counting with rejoins, UUID double-encoding, 401/404/429/timeout/plan-limit reasons, cache hit makes no request, audit has tool/host/status/ms and no token, no request when not configured or the host is not allowed. Opt-in `TEST_ZOOM_LIVE=1` makes one real call. |
| 3 | Tool and spec | `services/tools.py`, `agents/registry.py`, `tests/test_external.py`, `tests/test_agent_specs.py`, `tests/test_tools_mcp.py` | `rm_calendar` may call it; `rm_client`, `rm_requests` and the client agents are refused with `agent_tool_forbidden`; a Zoom failure returns the record part with `live: unavailable`; ids never in the result; span facts are codes. |
| 4 | Intent, handler, loop, checks, prompts | `schemas/rm.py`, `services/rm/handlers.py`, `services/rm/evidence.py`, `services/rm/checks.py`, `prompts/rm_planner/v7/`, `prompts/rm_assistant/v9/` (+ `capture.md`), `services/prompts.py`, `docs/prompt-log/rm_planner/v7.md`, `docs/prompt-log/rm_assistant/v9.md`, tests `test_rm_assistant.py`, `test_rm_graph.py`, `test_prompts.py` | The three questions route to `meeting_capture`; ambiguous meetings ask; `h_open` can call `zoom_status` within 4 reads; a model-supplied `appointment_id` is ignored; the two new checks withhold the bad items; full backend suite green. |
| 5 | Evaluation | `evaluation/planner_cases.json`, `evaluation/graph_cases.json` or `qa1_rm_cases.json` (+ `docs/evaluation/test-cases.md`), `evaluation/targets.py` (`zoom_down` fault) | Planner cases for the three questions plus two that must stay `schedule`. Sophie's graph cases for the seeded Michelle meeting, and a `zoom_down` case where the answer says Zoom could not be checked and states no call times. |
| 6 | Docs | `AGENTS.md` (RM assistant and meeting seam rows), `docs/architecture.md` §5.6.1, §14.4, §14.11, §15, `docs/how-agents-run.md`, `docs/pdpa-baseline.md`, `docs/workflows/workflow-2-relationship-manager.md` | Each names `zoom_status`, `meeting_capture`, 0150 and the privacy rule: Zoom participant names and emails are never parsed or kept. |
| 7 | Optional UI | `RmWorkspace`/Schedule meeting card, `lib/rm.js` | A *Zoom status* button on a video meeting opens the assistant with `meeting_capture` and that `appointment_id`, as the Briefing buttons do (`b01a0d0`). |

Slices 1 and 2 do not depend on each other. Slice 3 needs both. Slice 4 needs 3.

## Risks

- **Report lag.** Zoom's past-meeting report can 404 for a few minutes after a call ends. `ended_processing` covers this when our webhook has the end time; the answer says to check again shortly.
- **Live participants.** While a call is open, Zoom lists participants only through its Dashboard API (higher plans), so `joined` is `null` until the call ends. The answer says so.
- **Token lock.** A worker refresh can hold the file lock longer than `EXTERNAL_TIMEOUT_S`. The tool then returns `live: unavailable` instead of waiting.
- **Rate limits.** The 60 s cache and no retries within a turn keep calls low. A 429 is `unavailable`.
- **The shared cache.** Safe only while the SQL check runs first on every call. A test should fail if someone moves the cache in front of it.

## Not in this plan

- Reading My Notes live (only the meeting worker's job may), downloading or showing any transcript text.
- Creating, moving or cancelling a Zoom meeting from the chat. Schedule and `POST /meetings/{id}/zoom` stay the only ways.
- Telling the client who joined, or adding Zoom facts to the client side.
- Exports to other agents (for example a "notes missing" flag for *Today*). Possible later.
