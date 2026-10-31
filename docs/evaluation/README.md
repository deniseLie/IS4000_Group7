# Evaluation

How answers are tested and the results so far. Raw evidence is in `evidence/`.

The QA1 case set (Michelle C01–C10, Sophie R01–R10, with setup, expected output, must-not-happen and S/C/T checks) is in [test-cases.md](test-cases.md). The same cases are eval data in `backend/evaluation/qa1_client_cases.json` and `qa1_rm_cases.json` (LangSmith datasets `demobank-qa1-client` and `demobank-qa1-rm`); `tests/test_qa1_cases.py` keeps the two in step.

## LangSmith evaluations (current)

Goal: one stack, LangChain (model calls), LangGraph (v2 orchestration) and LangSmith (tracing, datasets, experiments, human review), with evaluations that score live answers automatically instead of stopping at `needs_human_review`.

Status: steps 1–2 built, and request tracing moved to LangSmith (§2.1). Reverses decision 2 in `docs/architecture/tracing-plan.md` §10 ("LangSmith removed").

### 1. Where we are today

| Piece | Today | Gap |
|---|---|---|
| Runner | `backend/evaluation/run.py`: offline validator checks by default; `--live` calls `answers.make_answer` (v1 only) | Live rows are only marked `needs_human_review`; nothing is scored. v2 (`orchestrator.answer_public`) is never evaluated |
| Datasets | `cases.json` (11 live), `focused_cases.json` (6 live), `offline_cases.json` (validator samples) | Local JSON, a `check` rubric string per case, no versions beyond `dataset_sha256` |
| Comparison | `--compare` diffs two report JSONs; `before.*`/`after.*` HTML files | Manual, one pair at a time |
| Tracing | Own Supabase `ops.traces`/`ops.spans` plus the JSON `llm_call` log | No view of prompts, model output or graph steps per run (by design: ids/counts only) |
| SDK | `langsmith` 0.14.11 already installed (pulled in by `langchain`); Playwright forces `LANGSMITH_TRACING=false` | Not a declared dependency, no keys in `.env.example` |

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
| `demobank-qa1-client` | `qa1_client_cases.json` | QA1 C01–C10: whole conversations (`turns`, `persona`, `mode` personal or public) with `reply_has`/`reply_lacks`, `ticket`, `schema`, `check` and `trajectory` (nodes, agents, tools, model) |
| `demobank-qa1-rm` | `qa1_rm_cases.json` | QA1 R01–R10, `state: spec` until F12/F14 and the RM assistant exist; may name the planned RM tools |

Each example: `inputs={question, history, language, mode:'public'}`, `outputs={check, expected_sources?, expected_origin?, expect_needs_human?}`, `metadata={case_id}`. Example ids are `uuid5(dataset, case_id)` so re-syncing updates rather than duplicates. After each sync, tag the dataset version with the file's sha256 so every experiment points to an exact dataset version.

Case files gain optional structured fields (`expected_sources`, `expected_origin`, `language`) next to the free-text `check`, so deterministic evaluators do not have to parse the rubric.

### 4. Targets (what is evaluated)

`backend/evaluation/targets.py`, one async target per pipeline, both decorated with `@traceable`:

- `v1(inputs)`: `ChatRequest` → `guardrail.assess_request` → `answers.make_answer`.
- `v2(inputs)`: same guard, then `orchestrator.answer_public(req, risk, guest_ctx, None)` with a guest `RequestContext` built like `core/gateway.py` does for guests; fixed-response cases go through the same no-model path the API uses.

Both return the `AnswerEvent` dump plus the guard's fixed-route name and whether a model was called. LangChain and LangGraph calls inside are traced automatically under the target run. `codex_cli.answer` gets `@traceable(run_type='llm')` so the Codex transport also appears.

### 5. Evaluators

`backend/evaluation/evaluators.py`. Deterministic checks reuse production code, so evals and runtime cannot drift:

| Key | Pass when | Reuses |
|---|---|---|
| `source_check` | `source_check` is not a rejection and every cited id was retrieved | `source_checks.check_sources` |
| `expected_sources` | cited ids include `expected_sources` | — |
| `no_leak` | no secret, URL or action claim in the answer | `source_checks` / `privacy` checks |
| `safety_route` | `response_origin == expected_origin`; fixed routes made no model call | target output |
| `language` | answer is in the requested language (script check for zh/ta, keyword check for ms) | — |
| `needs_human` | matches `expect_needs_human` when set | — |
| `latency_ms`, `tokens`, `cost_usd` | recorded as scores, not pass/fail | `llm` span attributes |

**`rubric` (LLM-as-judge):** grades the answer against the case's `check` text and the cited evidence → `{score: 0|0.5|1, reason}`. It is built with `init_chat_model` and strict structured output like the gateway, using its own `JUDGE_MODEL` (stronger than the answer model and never the same one). It is an aid to review, not proof: the AGENTS.md rule that live results need human review still applies.

**Human review:** a LangSmith annotation queue (`demobank-review`) automatically receives runs where `rubric < 1`, any deterministic check fails, or the answer was withheld. Reviewer feedback (`human_correct`) is stored as feedback on the run. Confirmed failures become new cases (closing the loop in tracing-plan §7).

### 6. Build order (one commit per step on one branch)

1. **Setup (done):** `langsmith>=0.14,<0.15` declared; `LANGSMITH_*` keys in `.env.example` (the SDK reads them itself, so no `config.py` fields; `JUDGE_MODEL` arrives with step 5). `app/core/langsmith_client.py` builds a client whose `anonymizer` runs `privacy.redact` over every string in run inputs, outputs, metadata and errors, and `langsmith.configure` makes it the client LangChain/LangGraph trace through. `app/main.py` installs it only when tracing is on, because the client starts a background upload thread. Tests: `tests/test_langsmith_client.py`.
2. **Datasets (done):** `python -m evaluation.langsmith_sync [--dataset NAME] [--dry-run]` validates every file before uploading, then creates/updates/removes examples and tags the version `sha-<file hash>`. Case files gained `cites_any` (passes when at least one listed article is cited) and `expected_origin`; new `safety_cases.json` (8 cases) adds `expected_route` and `expect_needs_human`. Only `secrets` cases may hold private-looking (synthetic) details, since dataset examples are not run through the trace anonymizer. Tests: `tests/test_langsmith_sync.py`, including every safety case through the real no-model path. Note: `cases.json` changed, so `--compare` against the old `before.json`/`after.json` now reports a dataset mismatch.
3. **Targets + deterministic evaluators:** `targets.py`, `evaluators.py`; unit-test the evaluators on sample outputs (allowed: validators on sample strings).
4. **Runner:** `python -m evaluation.langsmith_run --dataset demobank-focused --pipeline v1|v2 [--judge]` calls `langsmith.aevaluate(..., experiment_prefix=f'{pipeline}-{prompt_version}', metadata={model, transport, prompt_version, pipeline, knowledge_sha256, implementation_sha256}, max_concurrency=2)`. It prints the experiment URL and a pass/fail/error summary, exits non-zero on deterministic failures and writes the existing local JSON/HTML report as an offline record. `run.py --live` then points to the new runner; offline mode is unchanged.
5. **LLM judge + annotation queue:** `rubric` evaluator, queue creation in `langsmith_sync.py`, routing rule for failed runs.
6. **Baseline:** run `demobank-focused` and `demobank-standard` on v1 and v2 with the current model; record results and limitations in `docs/core-results-review.md`.
7. **Docs:** README section (keys, how to run, what is uploaded, region), AGENTS.md "Evaluate quality" row, tracing-plan §10 decision 2 amended, new `docs/evaluation-langsmith.md` covering evaluator definitions and limits.

Later (needs agreement): **phase 4** personal-mode and Workflow 1–3 datasets. These need native PostgreSQL/PostgREST persona sessions (Mac for PostgREST per the Windows test notes) and a decision on uploading synthetic customer records. Also optional: LangSmith online evaluators on dev traces, and pytest `@pytest.mark.langsmith` for the focused suite.

### 7. Risks and limits

- **Data leaves the machine.** Prompts, evidence and answers go to LangSmith's hosted service; this is acceptable only because data is synthetic and redacted (§2.3). Choose the US or EU endpoint deliberately.
- **Judge bias and cost.** The judge model can be wrong and costs tokens per case; `--judge` is opt-in and its scores are labelled as model-graded.
- **Free-router flakiness.** Rate limits and outages on free OpenRouter models show up as `error` outcomes. Report them; do not retry them into passes.
- **Seats.** Teammates without LangSmith access still have the Supabase stats page and the local report files.

## Earlier results (13–24 Oct 2026)

Before LangSmith, answers were checked with the offline validator (`python -m evaluation.run`) and reviewed by hand. On 14 Oct 2026, 151 backend tests and all 19 offline validator cases passed, and 19 browser workflows had passing runs. Three focused live questions were answered by the real model and reviewed by a person, not scored automatically. No released prompt was changed for those runs. The raw files are in [evidence/core-2026-10-14](evidence/core-2026-10-14) (synthetic questions only). Limits that still apply: the validator checks sources and figures, not meaning, so live answers still need human review.
