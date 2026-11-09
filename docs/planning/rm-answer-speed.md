# Faster RM answers (R22 and the other briefs)

Plan written 6 Nov 2026; slices 1–5 built the same day (see [What was built](#what-was-built-6-nov-2026)). It covers the **model** side of a slow RM turn; the database side is in [database-speed.md](database-speed.md).

## What was measured (5 Nov 2026)

From the LangSmith `llm` spans of the RM and client evaluation runs on the Claude Code transport (`CHAT_TRANSPORT=claude`, haiku), and a few direct `claude -p` calls:

| Call | Input tokens | Output tokens | Time |
|---|---|---|---|
| RM brief-type answer (`rm_assistant/v5`) | 7.1k | 14.3k | 60 s |
| RM brief-type answer (`rm_assistant/v5`) | 23.1k | 18.4k | 79 s |
| RM ticket summary (R08) | 22.2k | 3.1k | 19 s |
| Client agent (Guide, Accounts, Compliance) | 4–10k | ~1k | ~10 s |
| Planner (client or RM) | 3.3–4.3k | ~400 | 6–8 s |
| A tiny structured answer (direct test) | 1.7k | 1.3k, of which **948 thinking** | 11 s |

**What this means:**
- Time per call is about **5 s plus 1 s for every ~235 output tokens**. Input size barely matters (22k in took 19 s; 7k in took 60 s).
- Most output tokens are hidden thinking. `--effort low`, `MAX_THINKING_TOKENS=0` and `--settings {"alwaysThinkingEnabled":false}` did not turn it off. The only lever is a smaller task for the model.
- The 5 s is the CLI starting a process. It does not happen on the API transport.
- The brief prompt asks the model for 9 sections, each item with ids, kind and owner. That is 10–18k output tokens, 45–80 s, and on its own close to the 24,000-token request budget (`LLM_REQUEST_TOKEN_BUDGET`). R22 adds a planner call and a second answer call (the follow-ups), so it ran out of budget, and at 90 s the CLI timed out.

So: **cut what the model writes**, then what it reads. Code renders what is already a record; the model writes only what needs judgement. This is the same rule as `services/rm/priority.py` (code scores, the model explains) and main's `45637af` (the model explains the top 10 ranked records; code counts the rest).

## Fits with other work

Checked against `main` at `467733d` and every worktree on 6 Nov 2026.

| Other work | Status | Overlap | How this plan stays clear |
|---|---|---|---|
| `45637af` today/priorities context budget (`RANKED_SHOWN`, `not_listed`, `with_rest`) | On main | `handlers.py` | Start from main. Leave `h_today`, `h_priorities`, `today.md` and `priorities.md` as they are; reuse the same pattern (code appends fixed sections). |
| `claude/admin-overview-sgt-1e88a8` (admin dashboard, 3 commits) | Merged into main (`67c9002`), the base of this branch | `AGENTS.md` (admin row), `insights.py`, `quality.py`, `components/admin/*`, `lib/admin.js`, `index.css` | Touch none of these. In `AGENTS.md` change only the RM rows. Admin → Latency already reads `first_section_ms`, so slice 4 shows there without admin code. |
| `3ef0601` frontend load speed (lazy workspace per role, `lib/roles.js`, `initials` in `lib/format.js`) | On main since this branch started | Import lines of `ClientsPage.jsx` and `lib/rm.js` | Different hunks from ours (we change `askAssistant`/`partialReply` and the `AiOverview` body); merge main before merging back. |
| UBS theme redesign (uncommitted in its worktree) | Not committed | `index.css`, `index.html`, `AGENTS.md` (UI conventions row) | No CSS changes: reuse `.loading` and the skeleton classes from the "UI refresh" block. |
| Architecture docs edits (uncommitted in the orchestrator-architecture worktree, 52 commits behind) | Not committed | `architecture.md` §1–§13, `v5-build-plan.md` | Edit only §14.11 (and §14.6 if a rule changes). |
| `evaluators.py` (uncommitted in the persona-1-prompts worktree) | Not committed | `evaluation/evaluators.py` | Do not edit it. Case changes go in `graph_cases.json` / `qa1_rm_cases.json` only, if any. |
| `docs/architecture.md` spelling-fix row (uncommitted in this worktree) | Not committed | Knowledge table | Not ours; leave it. |
| RM copilot build plan 5a: `prepare_meeting` → `services.meetings.prepare` | Waits on the meeting side | `RmAnswer.jsx`, the brief for a scheduled meeting | `meeting_brief` keeps its reply shape, so 5a can add the `BriefCard` beside it. After 5a, `meeting_brief` is only "brief me on Michelle" (no scheduled meeting), so its code-built "The meeting" section must be easy to drop. No substitute for the meeting side's brief (build plan decision 5). |
| Meeting side (`services/meetings/**`, `api/meetings.py`, `components/rm/meetings/*`, `lib/meetings.js`, prompts `brief_writer`/`proposal_drafter`/`minutes_writer`, migrations 0120–0139) | Theirs | – | Not touched. No migrations in this plan. |
| Agent worktrees for 0108, 2b, 3b and consent v2 | Already on main | – | Stale; ignore. |
| `rm_assistant/v6` | Free on every branch | – | This plan takes v6. |

**Ground rules for the build**
- Sync this branch with main first (`sync_with_base_branch`); it is one commit behind (`45637af`).
- One branch, one commit per slice, merged to main at the end. If slices 1–2 are merged before slice 3, `rm_assistant/v6` is pinned then and slice 3 takes `v7`.
- Never edit `rm_assistant/v5`: v6 starts as a copy of v5 and changes only the task files named below.
- Keep the reply shape (`summary`, `sections` of items with `text`, `kind`, `owner`, `source_ids`, `sources`, `validation`), so `RmAnswer.jsx`, saved history, `replyText` and the evaluation keep working.
- Every code-built item cites its record id, so R04's "every point has a source id" still holds.
- Exports stay ids, dates, counts and codes. The follow-ups step re-reads the note; note text never travels as an export.
- Streaming shows only code-built sections and model sections that passed the checks. `answer` stays the reply of record.

## Slice 1: Brief facts and a split brief (`meeting_brief`)

**Goal:** the brief's model call reads a few hundred tokens of facts and writes 3 short sections.

**Files**
- `services/rm/brief_facts.py` (new, pure functions): from the reads `h_brief` already makes, build
  - `facts`: a small JSON object for the model, each fact tagged with its evidence id;
  - `sections`: the record sections, built by code with their `source_ids`.
- `services/rm/handlers.py` `h_brief`: run its six reads together (`asyncio.gather`; [database-speed.md](database-speed.md) option 2, here only); call the model with the facts; put the code sections and the checked model sections in the brief's order; keep `ask_cards`.
- `prompts/rm_assistant/v6/brief.md`: the model writes only the summary and the 3 narrative sections. `handlers.ASSISTANT_VERSION='v6'`.
- `docs/prompt-log/rm_assistant/v6.md` (from `TEMPLATE.md`); pin v6 in `tests/test_prompts.py`.

**Who writes each section**

| Section | Written by | From (evidence id) | Limit |
|---|---|---|---|
| The meeting | Code | `selected-appointment`, or a gap when none is confirmed | 1 item |
| Who the client is | Code | `profile` (client since, with the RM since, inherited from) | 2 items |
| History and commitments | Code | The **latest note** only: date, format, who wrote it, its commitments ("recorded, not necessarily completed"), requests and decisions; one line counting older notes; `notes-withheld` | latest note + 1 line |
| Open matters | Code | `ticket-…`, `rm-requests`, `tasks`, `compliance`, `milestones` | 5 items, then a count line |
| What the client has told us | Code | `preferences` (top 3), `talking-points` lines with their labels, `shared-note` or the `consent-status` gap, one gap per category not shared | as listed |
| Conversation starters | **Model** | facts | 2–3 items |
| Suggested discussion points | **Model** | facts | 3–4 items |
| Questions to ask | **Model** | facts | 2–3 items |
| Gaps to check | Code | `profile.missing_information`, consent gaps, no confirmed meeting, private notes withheld | as listed |

**Facts given to the model** (target ≤ 1.5k tokens): client name, since and inherited from; the meeting (when, format, purpose) or none; the latest note's commitments, requests and decisions; open tickets (reference, label, status); the review due date; the next 3 milestones; product names; the top 3 large movements and up to 3 insight figures (only when shared); up to 3 talking-point lines and 3 preferences; consent per category. No raw transactions, no older notes, no full profile.

**Checks:** the model's items go through `checks.check_sections` against the facts the model saw (its figures and dates must be in the cited facts). Code sections need no model checks. Sources shown in the UI stay the full records.

**Tests**
- Unit tests for `brief_facts` on synthetic records: section order, limits, every item cited, consent gaps (shared, not shared, pending), no confirmed meeting, several notes (only the latest), Mei Ling (R05: no `shared-note` id anywhere).
- `test_rm_graph.py`: the brief's model input contains no older note, no transaction rows and no client text beyond the latest note; `latest_note` is still exported.
- Live: QA1 R04, R05 and R11 (the judge) on the Claude Code transport.

**Done when:** R04 and R05 pass; the brief's `llm` span shows under 4k input and under 4k output tokens.

## Slice 2: Follow-ups from the note (`actions_from_note`, R22's second wave)

**Goal:** the follow-ups call reads the note and the request types only, and writes only the proposed actions.

**Files**
- `handlers.h_actions`: keep its reads (profile and notes, for the duplicate checks and the note). Send the model only the selected note's text and the request types (category, label, fields). Code fills `items` from the note's recorded commitments, client requests and decisions, with `certainty` set by code from the note's words (may, might, maybe, considering, probably, to confirm, will think). `interaction.met_on` comes from the note.
- `prompts/rm_assistant/v6/actions.md` and a smaller schema next to `documentation_schema` (summary, gaps, actions). `documentation_reply` is unchanged: it still checks quotes, fields, owners, due dates and duplicates in code.

**Tests:** `test_rm_assistant.py` (actions still must quote the note; duplicates still flagged); `test_rm_graph.py` (R22: two waves, `client_ref` and `latest_note` passed, nothing written).

**Done when:** R22 in `graph_cases.json` passes on the default budget (24,000) and the default CLI timeout (90 s), with the follow-ups section listing drafts (the judge's 0.5 last time).

## Slice 3: The same pattern for the other client reports

| Intent | Code writes | The model writes |
|---|---|---|
| `client_overview` | Snapshot, accounts and products, open requests, upcoming, large movements, insight figures, consent gaps | The summary and up to 3 "worth raising" points. A direct question ("When is her review due?") is answered by the model from the facts in one short section. |
| `handover_brief` | The 7 record headings of `HANDOVER_SECTIONS` (as now, in that order) | "Suggested introductory topics" and the summary |
| `meeting_history` | One section per note, newest first, last 5 notes, then a count line | Only a question about a meeting ("what did we agree on loans?") |

**To decide in the slice:** how to tell an overview request from a question about the client. Start with a rule (overview wording and no question mark), and test it on R11's three turns.

**Files:** `brief_facts.py` (shared builders), `handlers.h_overview`, `h_handover`, `h_history`, `rm_assistant/v6` `overview.md`, `handover.md`, `meetings.md`. R23 is unchanged (`h_shared` already has no model).

**Tests:** `test_rm_graph.py` overview test (insights shared and not shared), `test_rm_assistant.py` handover structure, `test_rm_workspace.py` consent test; live QA1 R11, R23 and the handover case.

**Done when:** those cases pass and each of these calls writes under 4k output tokens.

## Slice 4: Stream sections on `/rm/assistant`

**Goal:** Sophie sees the code-built parts as soon as the records are read, and each model section once it has passed the checks. Not word by word: model text is shown only after its checks.

**Files**
- `services/rm/graph.py`: `RmRuntime.show` and `respond(req, ctx, say, show=None)` (optional, so existing callers and tests keep working).
- `handlers.Turn.show(sections, sources, task_id)`: `h_brief`, `h_overview` and `h_handover` call it before their model call, then again with the checked model sections.
- `api/rm.py`: a `section` event (task id, sections, sources) between `status` and `answer`; set `first_section_ms` on the trace like `/chat`, so Admin → Latency shows it.
- `frontend/src/lib/rm.js` `askAssistant(..., onSection)`; `RmAssistant.jsx` shows the parts in the pending reply under the status line, with `RmAnswer` in `compact` mode; the final `answer` replaces them.

**Tests:** `test_rm_graph.py` (event order: code sections before the model call; a withheld item is never streamed; `answer` unchanged); `rm.test.js` (`askAssistant` collects sections, ignores unknown events). `evaluation/targets.play_rm` reads only `answer` events, so it needs no change.

**Done when:** for R22 the first section arrives after the planner and the brief's reads (about 10 s on the Claude Code transport).

## Slice 5 (optional): Smaller wins

- **See thinking in traces:** `claude_cli.parse_result` returns `thinking_tokens` and `num_turns`; `llm_gateway.call` puts them on the `llm` span. Span metadata only: the admin pages can read them after the admin branch merges.
- **Skip the RM planner for one-request messages:** a rule plan for "brief me on <client>", "overview of <client>" and "my day" saves one model call (6–8 s). First check how `plan_checks` scores RM planner cases, since the plan source becomes `rule`.

## Not doing, and why

- **Background summary cache (refreshed by the meeting worker).** R22 is a first, uncached request, so a cache does not fix it. The meeting runner "never receives the RM's JWT" and must not read client records (architecture §15.3 and the seam's docstring). The pre-meeting brief is the meeting side's Brief Writer (`prepare`, build plan 5a). If repeat requests matter later: an in-memory cache keyed on the note id, ticket statuses and consent state, never saved.
- **Word-by-word streaming.** Model text must pass the source checks before anyone sees it.
- **Running R22's follow-ups beside the brief.** It would save one wave, but R22's design is two waves with `latest_note` passed on, and the evaluation checks that.
- **Raising the budget or the timeout as the fix.** That hides the cost; the diagnostic run at 120k only proved the path works.
- **`today` and `priorities`.** Main bounded them in `45637af`.

## Order and expected effect

| Slice | Size | R22 total (estimate) |
|---|---|---|
| Today | – | 139 s at a 240 s timeout and 120k budget; fails at the defaults |
| 1 Brief facts, split brief | M | ~70 s |
| 2 Follow-ups from the note | S | ~40 s, ~15k tokens (under 24k) |
| 3 Overview, handover, history | M | – (R11, R23, handover cases about 2–4× faster) |
| 4 Section streaming | M | First content after ~10 s |
| 5 Extras | S | ~35 s with the planner rule (not for R22, which has two requests) |

The estimates come from the measured rule (5 s + output tokens / 235 per s), not from runs.

## Measure again

- LangSmith `llm` spans: on the Claude Code transport they are run type `chain` named `llm`, so filter by name. Read `input_tokens`, `output_tokens`, latency and (after slice 5) `thinking_tokens`.
- Run R22 (`graph_cases.json`) and QA1 R04, R05, R11, R23 on `CHAT_TRANSPORT=claude` with the default budget and timeout. Record the before and after in `docs/evaluation/README.md`.

## What was built (6 Nov 2026)

All five slices, on one branch, as planned. These are the differences from the plan and what was added after the first live runs. Results are in [evaluation §9](../evaluation/README.md#9-rm-answer-speed-6-nov-2026-rm_assistantv6).

- **Slice 1, brief:** as planned. `brief_facts.brief_sections` writes the record sections and `points_schema` (`rm_brief_points`) asks the model for a summary, conversation starters, discussion points and questions, checked against the extract. The six reads run together (`handlers.client_reads`).
- **Slice 2, follow-ups:** as planned (`rm_actions`). Code fills the note's items and the interaction. Added after the live runs:
  - open tickets reach the model counted by category with the newest reference (`handlers.open_by_category`), not one row each;
  - `actions.md` asks for one action per commitment and client request in the note, and names open work that may cover one in a gap. Code flags duplicates as before.
- **Slice 3, other reports:** `client_overview` (`rm_overview_points`), `handover_brief` (`rm_handover_points`, which also takes up to 3 outdated or conflicting records) and `meeting_history` (`rm_history_points`; no model call when there are no notes). A question about a client or a meeting is answered from the full records (`question.md`, `meetings.md`). `handlers.wants_report` decides between the two: a button, report wording without a question mark, or no topic beyond the names.
- **Slice 4, streaming:** as planned. The model's points are streamed only after the checks.
- **Slice 5, extras:** the `llm` span records `num_turns`, `api_ms` and `answer_chars`, and since 7 Nov `thinking_tokens` (the CLI reports it in `usage.output_tokens_details`; slice 5 first missed it and kept only the answer size). The rule planner also plans a brief followed by follow-ups from the latest note (R22). It applies only when the words it takes as the client are her book name and nothing else (`graph.only_name`), so "brief me for my next confirmed meeting with Michelle" still goes to the model planner, which reads the meeting hint.
- **Bounded extract:** the first 3 open tickets go to the model, whatever the count (Michelle had 44 on hosted). The record section still lists 5 and counts the rest.
- **Evaluation:** `targets.rm_text` now includes the proposed actions of a follow-ups or documentation card, so the judge sees them.
- **Still open:**
  - thinking makes up most of each report call's output (the brief writes 4.8–8.2k output tokens for about 2k characters), and it cannot be turned off on the CLI;
  - the browser streaming was not checked live;
  - R05 (manual) was not run.

## Docs to update at the end

`AGENTS.md` (RM rows only), `architecture.md` §14.11, `how-agents-run.md` (the RM paragraph), the `services/rm/handlers.py` docstring ("No canned answers" becomes "code renders records, the model writes the judgement parts"), `docs/prompt-log/rm_assistant/v6.md`, `docs/evaluation/README.md`.
