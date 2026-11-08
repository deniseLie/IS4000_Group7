# Client chat insights: what changed on 8 Nov 2026

Client chat insights (`talking_points` in the code) are what the assistant picks up from a signed-in client's chats about what she is planning (property financing, investments, time deposits, insurance), so her RM can prepare for a meeting. Only list values reach the RM, never her words. Synthetic data only.

Four changes landed on 8 Nov 2026. They were built on `claude/implicit-insights-check-137929`, merged into `main` (merge `9e1de6d`), and the migrations (0142–0145) were applied to the hosted Supabase project the same day.

| # | Change | In one line | Where |
|---|---|---|---|
| 1 | Insights follow the conversation (lane 1 v2) | A follow-up without the topic's name now updates the insight; each topic can name a kind; "we've decided not to go ahead" puts it on hold | Migration 0143, `services/talking_points_state.py`, prompt `talking_points/v2` |
| 2 | *Client chat insights* naming | People read *Chat insights* on her pages and *Client chat insights* on the RM side; code names stay | Migration 0144, frontend and RM evidence labels |
| 3 | Care pause retired | Job loss, debt, bereavement and similar messages no longer pause insights for 60 days | Migration 0145, `services/talking_points.py`, her page |
| 4 | Care cases out of the evaluation | C01–C04 and CV09 removed (57 messages, 14 conversations) | `backend/evaluation/` |

Commits: `e9a8b96`, `5ff7339`, `a43c1a7` (slices 1–3), `3a58526` (slice 4 and the naming), `cb5cdbe` (slice 5), `699a1b9` (0145), `51366e6` (evaluation). The design and the reasons are in [stateful-talking-points.md](stateful-talking-points.md); prompt results in [the v2 prompt log](../prompt-log/talking_points/v2.md).

## 1. Insights follow the conversation (lane 1 v2)

Lane 1 is the background step that runs after each reply in a signed-in saved chat. Lane 2 (*Let Sophie know*, a rule that offers `insight.share`) is unchanged.

### Before (v1)

- Each message was classified on its own, with only the message before it as context, and the model could only add.
- A follow-up with no topic word ("Is the lock-in usually two years?") stopped before the model (`no_topic`).
- There was no field for details such as HDB resale versus condo.
- A plan she called off stayed *planning* until it expired after 90 days.

### After (v2)

**When it runs.** As before, nothing runs on greetings, yes/no and offer replies, fixed safety turns, mostly redacted or sensitive messages, or without her chat insights consent. A message with a topic word runs. New: a message without one also runs while a topic is *active* in this chat, meaning it was recorded from one of her last 4 messages (a carry-over).

**What the model sees.** Her current insights as list values only, the active topics, her last 5 redacted messages in this chat and her latest message. Never the assistant's replies, her records or any stored words (0142 keeps none).

**What the model may propose.** For each topic: *add*, *update* or *on hold*. It never deletes: Delete, Don't suggest this again, withdrawing consent and the 90-day expiry stay with her or with code.

**Checks.** Code (`talking_points_state.validate`) and then SQL (`customer_apply_talking_point`) check every change:
- values from the lists, and a kind that belongs to the topic;
- a quote that is her exact words from her latest message (checked, then dropped: only the turn is kept);
- a topic word in the quote, or a topic active in this chat (else `not_active`);
- update and on hold only for an insight she has (else `no_point`);
- on hold only when her words say the plan is off ("not going ahead", "decided not to", "put it off", "postpone", "hold off", "no longer", "changed my mind", "cancel");
- an insight she shared herself (confirmed) is never changed.

**How a change is applied.**

| Field | Rule |
|---|---|
| Stage | Only moves up: concern < curious < exploring < planning < ready |
| Timeframe | The latest one she gives; *unknown* never overwrites |
| Kind | The latest one she gives; none never overwrites |
| Concerns | Merged, at most 4 |
| On hold | Hides the insight from her RM; her next mention of the topic clears it |
| Expiry | 90 days after her last mention, refreshed on every change |

**New lists.**

| Topic (label) | Kinds |
|---|---|
| Property financing (`home_loan`) | HDB BTO, HDB resale, private property, landed, refinancing |
| Investments | ETFs, unit trusts, stocks, bonds, robo-advisor |
| Time deposit (`fixed_deposit`) | new deposit, renewal |
| Insurance | life, home, travel (no health: a sensitive area) |

New concerns: lock-in, down payment, tenure, risk (alongside rates, fees, eligibility and timing).

### Example

Michelle, in one chat: "I'm thinking about a home loan" → "We're looking at an HDB resale flat" → "Probably in about six months" → "Is the lock-in usually two years?" (evaluation case CV01).

- v1: one insight at *exploring*, with no kind, timeframe or lock-in, because the follow-ups had no topic word.
- v2: *Property financing · planning · HDB resale · within 12 months · asks about lock-in · inferred from chat*.

Then "Actually we've decided not to go ahead with it" puts it on hold (CV03), and "We're back to looking at condos" clears the hold (CV04).

### What each side sees

- **Michelle** (Data sharing → *Chat insights*): each insight in plain words, e.g. "You're planning to finance a property (HDB resale) in the next 12 months. You mentioned lock-in.", with *Picked up from your chat* or *You shared this* and the date. An on-hold insight stays on her page as "On hold: you said this plan is off. Sophie doesn't see it." A new inferred insight shows "Sophie sees this from …" during its 24-hour hold.
- **Sophie** (client overview, *Client chat insights* section; meeting brief, *What the client has told us*): the line, e.g. "Client chat insight: Property financing · planning · HDB resale · within 12 months · asks about lock-in · inferred from chat (last mentioned …). Not confirmed by the client." Inferred insights appear only while Michelle's chat insights consent is active and after the 24-hour hold. An on-hold insight is hidden, and `rm_point_status` reports it as `not_shared`, so the meeting side's brief re-check needed no change.

### Switching back

`prompts.VERSIONS['talking_points']` picks the version (`v2` since slice 5). Setting it to `v1` restores the old path; `customer_record_talking_point` (v1) is still in the database.

### Evaluation results (live, 8 Nov, `anthropic/claude-haiku-5.5` on OpenRouter, one run each)

| Measure | v1 | v2 |
|---|---|---|
| Conversations: final state matched | 11 of 15 | 15 of 15 |
| Conversations: false insights / false on hold | 0 / – | 0 / 0 |
| Conversations: model calls (per conversation) | 16 (1.07) | 27 (1.8) |
| Single messages: false insights | 0 | 0 |
| Single messages: precision / recall | 1.000 / 0.969 | 1.000 / 0.938 |
| Single messages: stage agreement | 0.935 | 0.967 |

The switch-on gate passed: no false on hold, no more false insights than v1, and a clearly better final state. v2's lower recall is one case (TP27: insurance recorded, the home loan missed). These were run on the earlier datasets (61 messages, 15 conversations), before the care cases were removed. They are synthetic English cases scored by code, not proof of accuracy on real chats.

## 2. *Client chat insights* naming

- Words people read: *Chat insights* on her pages (the Data sharing category and its detail, the consent answers in chat) and *Client chat insights* on the RM side (the overview section and brief lines, *Client chat insights shared with you* on Today, the consent gap lines such as "The client has not shared chat insights with you.").
- Migration 0144 changes only the `chat_topics` label in `consent.categories` from *Topics from your chats* to *Chat insights*. The consent notice text she agreed to is unchanged.
- Code names stay: `talking_points`, the `insights` schema, the `insight.*` actions, `schemas/meetings.TalkingPoint` and the released prompts.

## 3. Care pause retired (0145)

**Before.** A care signal in her messages (job loss, debt trouble, bereavement, serious illness, someone pressuring her about money) paused chat insights for 60 days: lane 1 stored nothing and called `customer_pause_suggestions`, lane 2 offered nothing, and her page showed a pause line with *Resume*.

**After.**
- A care message goes through the same checks as any other message. The classifier still records nothing for money trouble, and the sensitive-word filter still stops health and other sensitive subjects.
- Migration 0145 ended every open pause (each audited as `data.suggestions_resumed`, system actor `migration_0145`) and dropped `customer_pause_suggestions`. The `insights.opportunity_pauses` table and the readers' `paused` checks stay, always false, so no released function had to be rewritten.
- Her page no longer has a pause line or a *Resume* button.
- Unchanged: the care flag itself (`guardrail.CARE`, `AnswerEvent.care`). The reply is the same and the suggested next questions show only *Talk to a person*.

**Why.** The pause was outside the project's scope.

**Known effect.** Lane 2 can now offer *Let Sophie know* on a message such as "I was retrenched last month, should I still buy the condo next year?", which the care check used to stop. Nothing is stored unless she says yes.

## 4. Evaluation datasets

The care group no longer has a path of its own, so its cases were removed: C01–C04 from `talking_points_cases.json` (now 57 messages) and CV09 from `talking_points_conversations.json` (now 14 conversations). Before removal, a live v2 run on 8 Nov recorded nothing for them.

```bash
python -m evaluation.talking_points                                # offline: code checks only, no model
python -m evaluation.talking_points --live --version v2            # single messages, real model
python -m evaluation.talking_points --conversations --live --version v2   # conversations, final state per topic
```

## 5. Hosted database (8 Nov)

Batches 48 (0142, no stored quotes) and 49 (0143–0145) were pasted into the SQL editor of the hosted project and checked afterwards:

| Check | Result |
|---|---|
| `supabase_migrations.schema_migrations` | 0142–0145 recorded; the stored SQL is identical to the batch files |
| 0142 | `talking_point_evidence` has no quote column; `insights.my_evidence` is gone |
| 0143 | `kind` and `on_hold_at` on `insights.talking_points`; `customer_apply_talking_point` and `customer_topic_state` exist |
| 0144 | The `chat_topics` label reads *Chat insights* |
| 0145 | `customer_pause_suggestions` is gone; 0 open pauses |

The backend and frontend must run `main`'s code. A backend from before 0143 refuses the new `kind` field (the shared contract rejects unknown fields) and would drop insights.

## Files changed

| Layer | Files |
|---|---|
| Database | `migrations/0143_stateful_talking_points.sql`, `0144_chat_insights_label.sql`, `0145_retire_care_pause.sql`; `hosted/49_apply_0143_0145_hosted.sql`, `hosted/README.md` |
| Shared contract | `schemas/meetings.py` (additive: `kind`, longer `ConcernTag`, `KIND_TEXT`, `CONCERN_TEXT`; `line()` adds the kind only when set) |
| Backend | `services/talking_points_state.py` (new: `gate`, `model_input`, `validate`, `apply`, `extract`), `services/talking_points.py` (picks the version; care branch removed), `schemas/talking_points_state.py` (new), `schemas/talking_points.py`, `services/prompts.py` (`talking_points: v2`), RM labels in `services/rm/brief_facts.py`, `rm/evidence.py`, `rm/handlers.py`, `agents/consent_answers.py`, `tools.py` |
| Prompt | `prompts/talking_points/v2/system.md` (new; v1 untouched), `docs/prompt-log/talking_points/v2.md` |
| Frontend | `components/TalkingPoints.jsx` (kind, on hold; pause line removed), `lib/talkingPoints.js`, `components/rm/SharedTopics.jsx`, `lib/consentSharing.js`, `lib/consentData.js`, `lib/rm.js`, `index.css` |
| Evaluation | `evaluation/talking_points.py` (`--conversations`, `--version`), `talking_points_conversations.json` (new), `talking_points_cases.json` |
| Tests | `test_talking_points_state.py` (new), native `test_talking_points_state_db.py` (new), `test_talking_points.py`, native `test_talking_points_db.py`, `test_meeting_contracts.py`, `test_prompts.py` (pins `v2+63a30afe`), `test_consent_v2.py`, `test_consent_chat.py`, `test_rm_brief_facts.py`, `test_rm_gated_evidence.py`, `test_rm_graph.py`, `talkingPoints.test.js`, `consentSharing.test.js` |
| Docs | `AGENTS.md`, `docs/architecture.md`, `docs/how-agents-run.md`, `docs/workflows/workflow-1-client.md`, `workflow-2-relationship-manager.md`, `docs/evaluation/README.md`, `docs/planning/stateful-talking-points.md` |

## Known limits

- Carry-over is per chat: in a new chat, a message with no topic word changes nothing (CV12).
- On hold needs one of a fixed list of off wordings; other wordings leave the insight as it is.
- v2 makes more model calls: 1.8 per conversation against v1's 1.07.
- The model never sees the assistant's replies, so "the second one" after the assistant lists options gets no kind.
- Results are from synthetic English cases only.

## How to check it yourself

1. Sign in as Michelle in the running app (saved chats on). Her seed shares chat insights with Sophie and already has a *Property financing · planning · within 12 months* insight past its hold. To start again from the seed, run `backend/supabase/hosted/34_reset_meeting_demo.sql`.
2. In one new chat, send: "I'm thinking about a home loan", "We're looking at an HDB resale flat", "Probably in about six months", "Is the lock-in usually two years?". Lane 1 runs in the background after each reply.
3. Open Data sharing → *Chat insights* and refresh: the property financing insight now names HDB resale, the next 12 months and lock-in.
4. Send "Actually we've decided not to go ahead with it": the insight shows *On hold*. Then send "We're back to looking at condos": the hold is gone.
5. Sign in as Sophie and open Michelle's client overview (*Client chat insights*) or meeting brief (*What the client has told us*): it shows the same line, and nothing for that topic while the insight is on hold.
