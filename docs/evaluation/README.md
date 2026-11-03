# Evaluation

How answers are tested and the results so far. Raw evidence is in `evidence/`.

The QA1 case set (Michelle C01–C24, Sophie R01–R20, with setup, expected output, must-not-happen and S/C/T checks) is in [test-cases.md](test-cases.md). The same cases are eval data in `backend/evaluation/qa1_client_cases.json` and `qa1_rm_cases.json` (LangSmith datasets `demobank-qa1-client` and `demobank-qa1-rm`); `tests/test_qa1_cases.py` keeps the two in step.

## LangSmith evaluations (current)

Goal: one stack, LangChain (model calls), LangGraph (v2 orchestration) and LangSmith (tracing, datasets, experiments, human review), with evaluations that score live answers automatically instead of stopping at `needs_human_review`.

Status: steps 1–4 built and step 5's judge built (annotation queue not yet), and request tracing moved to LangSmith (§2.1). Reverses decision 2 in `docs/architecture/tracing-plan.md` §10 ("LangSmith removed").

### 1. Where we are today

| Piece | Today | Gap |
|---|---|---|
| Runner | `langsmith_run.py` executes real client `/chat` and RM `/rm/assistant` targets with deterministic checks and optional independent judge | UI approvals, uploads and owner-prepared failure fixtures remain separate |
| Datasets | Versioned LangSmith standard/focused/safety datasets and backlog C01–C24 / R01–R20 with row/hash provenance | 15 QA1 cases explicitly manual; a passing chat check does not certify an unplayed UI step |
| Comparison | Dataset-linked experiments and local JSON/HTML reports | Compare tagged dataset versions; older client IDs were remapped to match the backlog |
| Tracing | LangSmith client/RM roots with tools, model calls and native Codex token usage | Older runs are historical and are not backfilled |
| SDK | Declared `langsmith` dependency and ignored local environment | Keys and synthetic persona credentials must stay out of git |

### 2. Scope decisions (defaults; change before building)

1. **LangSmith replaces Supabase tracing (decided 2026-10-28).** The app traces every `/chat` request to LangSmith; `services/trace_store.py` is removed and migration `0074` drops `public.log_trace`, leaving `ops.traces`/`ops.spans` as read-only history. The app keeps only what LangSmith does not capture: `x-request-id` and the JSON `request` log, warning/error logs, the token budget and the audit log's model-call records.
2. **App tracing follows `LANGSMITH_TRACING`.** `.env.example` ships `false` (no key); the project `.env` sets `true`. Tests force it off. Eval runs enable it inside the runner regardless (`langsmith.tracing_context(enabled=True)`).
3. **Redaction before upload.** LangSmith runs hold prompts, answers and, in personal mode, synthetic customer records (decided 2026-10-28: full text in every mode). The client's `anonymizer` passes every string through `privacy.redact`; eval datasets contain only synthetic questions.
4. **Real provider only.** Targets call the real `llm_gateway`; no fake model (AGENTS.md). Provider failures are recorded as `error` outcomes, never replaced.
5. **Offline validator cases stay local** in pytest/`run.py` offline mode. They call no model, so LangSmith adds nothing.

### 3. Datasets

New `backend/evaluation/langsmith_sync.py`: idempotent upload of local case files, so git stays the source of truth.

| LangSmith dataset | Source | Notes |
|---|---|---|
| `demobank-standard` | `cases.json` | languages, injection, follow-up correction |
| `demobank-focused` | `focused_cases.json` | source questions plus the three Workflow 1 public questions |
| `demobank-safety` (new) | new `safety_cases.json` | lost card, secrets, human support, guest "my balance": must route to fixed responses with no model call |
| `demobank-qa1-client` | `qa1_client_cases.json` | QA1 C01–C24: whole conversations (`turns`, `persona`, `mode` personal or public) with `reply_has`/`reply_lacks`, `ticket`, `schema`, `check` and `trajectory` (nodes, agents, tools, model) |
| `demobank-qa1-rm` | `qa1_rm_cases.json` | QA1 R01–R20: 13 runnable RM API cases and 7 manual approval/fixture boundaries. Client cases have 16 runnable and 8 manual boundaries; manual cases are excluded from scoring |

Each example: `inputs={question, history, language, mode:'public'}`, `outputs={check, expected_sources?, expected_origin?, expect_needs_human?}`, `metadata={case_id}`. Example ids are `uuid5(dataset, case_id)` so re-syncing updates rather than duplicates. After each sync, tag the dataset version with the file's sha256 so every experiment points to an exact dataset version.

Case files gain optional structured fields (`expected_sources`, `expected_origin`, `language`) next to the free-text `check`, so deterministic evaluators do not have to parse the rubric.

### 4. Targets (what is evaluated)

`backend/evaluation/targets.py` (built): `play(inputs, app, token)` runs the real app in process (`httpx.ASGITransport`), to exercise the API, authorisation, tools, model and database; browser rendering and UI interactions are separate checks. Each example becomes one new saved chat (`CONVERSATION_STORE=database`): a guest with a fresh `X-Conversation-Token`, or a persona signed in with its own JWT (password login from the git-ignored persona fixture, as in `test_rls_live.py`). Every turn goes to `POST /chat` exactly as the browser sends it; the pipeline is chosen with `settings.pipeline`. A *tap* whose label is sent back as chat (`fraud_reports.CONFIRM_REPLY`, `LOST_REPLY`, or `rm_handover.SEND_REPLY`) is played only if the preceding response offered it. `confirm_rm_handover: reuse_or_send` chooses the actual offered Add-to-ticket or Send-to-RM confirmation. Form, upload and other button steps are skipped and listed.

It returns the joined reply and each turn's reply, origin, `needs_human` and cited source ids; the guard's fixed route per turn; which graph nodes *acted* (a LangChain configure hook: a node acted when its update changes the state or it paused the turn); agents, tools and model calls (an `observability.span_sink` that collects `task`/`tool_call`/`llm` spans while the target runs, `None` in the app); and the persona's tickets before and after. Permission-checked direct service reads are reported separately as `service_reads`; they are not invented MCP aliases. An opt-in in-process evidence sink supplies the judge only the exact cited evidence ID/version used by the answering turn. It does not log record text or fabricate a new read timestamp. Guests' agent count is reported as `answer`.

`backend/evaluation/rm_target.py` plays Sophie through the real `/rm/assistant` endpoint with her JWT. It carries returned client/ticket/document context and history between turns, captures actual tools/model calls and supplies cited source records to the judge. RM uses its own planner and assistant, not the client LangGraph. It reads ticket state before/after and never approves or sends a business write. Each case records the downloaded backlog row, workbook digest, owner and current-record adaptation; historical client IDs are retained as `legacy_id`.

### 5. Evaluators

`backend/evaluation/evaluators.py` (built). `checks(out, ref)` scores only the checks a case defines, one LangSmith feedback each (1 pass, 0 fail, `None` with the reason when the check depends on a step the chat cannot play). The app's own answer validation (`source_checks`) already runs inside every turn; a withheld answer shows up as `origin` and `needs_human`.

| Key | Pass when |
|---|---|
| `completed` | every played turn returned an answer (no HTTP or SSE error) |
| `reply_has` / `reply_lacks` | every pattern matches / no pattern matches the joined reply (regex, case-sensitive) |
| `cites` | some turn cites one of `cites_any` |
| `record_sources` | a cited record has the required `source_prefix` (for example `ticket-`) |
| `origin`, `needs_human` | the first turn's `response_origin` / `needs_human` match the case |
| `fixed_route` | the guard chose `expected_route` on the first turn |
| `ticket` / `ticket_<category>` | a new or quoted ticket of the expected category; `ticket_categories` checks each required category independently, and `ticket: null` means no new ticket |
| `nodes` | every listed node acted and no `idle` node did; `graph: false` means no node ran |
| `agents` | agent run counts equal the case's exactly |
| `tools` | every listed tool was called and none matching `no_tools` (globs) |
| `service_reads` | required authorised direct service reads occurred; distinct from named MCP calls |
| `model` | a model was or was not called, as the case says |

**`judge_rubric` (LLM-as-judge, `--judge`):** grades the conversation against the case's `check` and `must_not` → `{score: 0|0.5|1, reason}` with the versioned client/RM prompt in `evaluation/judge/v4.md`. On `CHAT_TRANSPORT=codex` it uses the same isolated, answer-only CLI connector with an explicit `JUDGE_MODEL`; on API transport it uses `init_chat_model`. The judge must differ from the actual answering model (`CODEX_MODEL` on Codex, `LLM_MODEL` on API). The local setup uses `gpt-5.6-luna` for answers and `gpt-6-luna` for judging. The judge receives redacted cited source records and the client-visible ticket state needed to verify an opened or quoted reference. It distinguishes unsent drafts, client-approved ticket follow-ups and open submission statuses. Missing numeric grades are reported as errors. Parallel app agents queue through the Codex connector; real provider failures and timeouts remain explicit. Codex traces post redacted model inputs when the run starts (the SDK excludes inputs from completion patches) and store structured outputs at completion. Token usage is emitted as native LangSmith `llm` usage metadata; API wrappers remain chains to avoid double counting the LangChain model child. It is an aid to review, not proof, and never decides a case's pass/fail: the AGENTS.md rule that live results need human review still applies.

**Human review (planned):** a LangSmith annotation queue (`demobank-review`) should receive runs where `rubric < 1`, any deterministic check fails, or the answer was withheld. Reviewer feedback (`human_correct`) is stored as feedback on the run. Confirmed failures become new cases (closing the loop in tracing-plan §7).

### 6. Build order (one commit per step on one branch)

1. **Setup (done):** `langsmith>=0.14,<0.15` declared; `LANGSMITH_*` keys in `.env.example` (the SDK reads them itself, so no `config.py` fields; `JUDGE_MODEL` is read only by the evaluation runner). `app/core/langsmith_client.py` builds a client whose `anonymizer` runs `privacy.redact` over every string in run inputs, outputs, metadata and errors, and `langsmith.configure` makes it the client LangChain/LangGraph trace through. `app/main.py` installs it only when tracing is on, because the client starts a background upload thread. Tests: `tests/test_langsmith_client.py`.
2. **Datasets (done):** `python -m evaluation.langsmith_sync [--dataset NAME] [--dry-run]` validates every file before uploading, then creates/updates/removes examples and tags the version `sha-<file hash>`. Case files gained `cites_any` (passes when at least one listed article is cited) and `expected_origin`; new `safety_cases.json` (8 cases) adds `expected_route` and `expect_needs_human`. Only `secrets` cases may hold private-looking (synthetic) details, since dataset examples are not run through the trace anonymizer. Tests: `tests/test_langsmith_sync.py`, including every safety case through the real no-model path. Note: `cases.json` changed, so `--compare` against the old `before.json`/`after.json` now reports a dataset mismatch.
3. **Targets + deterministic evaluators (done):** §4 and §5. Tests: `tests/test_langsmith_eval.py` (SSE parsing, node tracking on a small real LangGraph graph including a pause, every check on sample outputs, every case file's reference fields).
4. **Runner (done):** `python -m evaluation.langsmith_run --dataset demobank-qa1-client [--case C01 ...] [--pipeline v1|v2] [--judge] [--project demobank-dev]` calls `langsmith.aevaluate(..., experiment_prefix=f'{dataset}-{pipeline}', max_concurrency=1, metadata={dataset, pipeline, answer_prompt, prompt_versions, model, transport, judge_model, dataset_file})`. Spec cases are skipped; signed-in cases need `TEST_PERSONA_FIXTURE_FILE` (or `--personas`). One case at a time, so ticket before/after reads stay attributable. It prints the experiment URL, a pass/fail/skipped/error summary and each failed check, writes the local JSON/HTML report (`--output`) and exits 1 on any deterministic failure or error. With `--project NAME` the runner instead traces each case into that existing tracing project (beside the app's own traces, e.g. `demobank-dev`) as one `eval <case>` run with the app trace nested inside, tagged `eval` and the run label (`<dataset>-<pipeline>-<UTC time>`), and attaches every check as feedback; LangSmith only allows experiments in dataset-linked projects, so this mode has no experiment comparison view. The runner loads the app's `.env` itself (`--env-file` overrides). Side effects: real model calls, and saved chats, audit events and (for cases that ask) tickets or handoffs in the database the `.env` points at. First runs (31 Oct 2026, v2, hosted database, raw results still to be reviewed): guest C01–C02 passed (`demobank-qa1-client-v2-84944bbe`). Michelle C03–C10 (`…-4ff52d31`, C03/C10 rerun in `…-5f49e96d` after the runner stopped playing buttons the reply did not offer): C07 and C09 pass, C08 skipped (upload step). Failures: C03 turn 1 never shows the fixed "Yes, report it" charge list (model answer once, withheld by validation once), so no fraud ticket; in the first run, before the runner fix, a typed "Yes, report it" got a model reply saying the charges were reported although no handoff was recorded. C04 and C05 were withheld by validation (C05's validation handoff opened an `rm_handover` ticket the case says must not exist). C06 omits the 12,000 amount and cites only records, not `transfer-timing`. C10's "status of my request" turn runs the relationship agent on RM requests rather than quoting the ticket, so it sees two relationship runs where the case expects one. `run.py --live` is unchanged for now.
Results are also visible in the app: the team-only **Quality** page (`#quality`, for logins whose administrator-set `app_metadata.demobank_role` is `rm` or `support`) lists experiments with each case's checks, answer, agents and timing, and the latest `chat` and `rm_assistant` traces step by step. The backend reads LangSmith with its own key; nothing LangSmith-related reaches the browser.
5. **LLM judge (done) + annotation queue (to do):** `judge_rubric` evaluator above; still to build: queue creation in `langsmith_sync.py` and the routing rule for failed runs.
6. **Baseline:** run `demobank-focused` and `demobank-standard` on v1 and v2 with the current model; record results and limitations in `docs/core-results-review.md`.
7. **Docs:** README section (keys, how to run, what is uploaded, region), AGENTS.md "Evaluate quality" row, tracing-plan §10 decision 2 amended, new `docs/evaluation-langsmith.md` covering evaluator definitions and limits.

Later: automate the explicit manual boundaries once suitable UI targets and controlled fixtures exist; add annotation-queue routing and optional LangSmith online evaluators. Personal and RM targets already use real synthetic persona sessions; never substitute service-role access for a persona JWT.

### 7. Risks and limits

- **Data leaves the machine.** Prompts, evidence and answers go to LangSmith's hosted service; this is acceptable only because data is synthetic and redacted (§2.3). Use the API endpoint for the configured LangSmith region (this local setup uses APAC).
- **Judge bias and cost.** The judge model can be wrong and costs tokens per case; `--judge` is opt-in and its scores are labelled as model-graded.
- **Free-router flakiness.** Rate limits and outages on free OpenRouter models show up as `error` outcomes. Report them; do not retry them into passes.
- **Seats.** Teammates can use the role-gated Quality page and local report files; the retired Supabase tracing tables are historical.

## Earlier results (13–24 Oct 2026)

Before LangSmith, answers were checked with the offline validator (`python -m evaluation.run`) and reviewed by hand. On 14 Oct 2026, 151 backend tests and all 19 offline validator cases passed, and 19 browser workflows had passing runs. Three focused live questions were answered by the real model and reviewed by a person, not scored automatically. No released prompt was changed for those runs. The raw files are in [evidence/core-2026-10-14](evidence/core-2026-10-14) (synthetic questions only). Limits that still apply: the validator checks sources and figures, not meaning, so live answers still need human review.

## Client compliance document coverage

`backend/tests/test_compliance_formats.py` exercises real DOCX, PNG/JPEG and scanned-PDF bytes through parsing, local OCR, caller JWT/RLS, preliminary checks and submission. It covers unreadable and encrypted files, mismatched content, unknown pagination, issue-date boundaries, ambiguous dates, and exact byte/page/pixel/filename limits. `test_document_parsers.py` also checks malformed packages and decompression limits; `test_compliance_followup.py` checks the selected-document support request, approved RM reply, client timeline/Inbox, ownership and retries.

Run from `backend/` with `TEST_DATABASE_LOCAL=1 TEST_DATA_API_LOCAL=1 ../.venv/bin/python -m pytest tests/test_compliance_formats.py tests/test_document_parsers.py tests/test_compliance_followup.py tests/test_document_templates.py`. OCR pass cases require an actual configured local helper; setup is in [Workflow 1](../workflows/workflow-1-client.md). They are explicitly skipped when no helper is configured. No OCR or model response is fabricated.

These are functional and safety checks, not an AI accuracy score. A successful preliminary check never establishes document authenticity or bank acceptance. Model grounding, agent/orchestrator trajectories and multilingual quality still need the separate LangSmith evaluations. Sophie’s provisioned synthetic login is exercised by the live RM target. Native follow-up tests remain separate from hosted bank acceptance/rejection and do not establish that a bank decision has been made.

## RM assistant (Sophie)

Manual prompt tests for the relationship manager assistant, with expected results and a results table: [rm-assistant-prompt-tests.md](rm-assistant-prompt-tests.md). The LangSmith runner (`langsmith_run.py`) plays client cases through `/chat` and runnable R01–R20 cases through `/rm/assistant`. Use the current acceptance criteria in [test-cases.md](test-cases.md); the older prompt guide is supplementary. Explicit approvals, document uploads and specially prepared failure/consent/attack fixtures remain manual and must not be counted as automated passes.
