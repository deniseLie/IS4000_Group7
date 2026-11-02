# Evaluation

How answers are tested and the results so far. Raw evidence is in `evidence/`.

The QA1 case set for Michelle (C01–C10, with setup, expected output, must-not-happen and pass criteria) is in [test-cases.md](test-cases.md).

## Knowledge retrieval (offline, no model)

Does knowledge search hand the model the right approved article? `backend/evaluation/retrieval.py` scores both stores against `retrieval_cases.json`: 135 synthetic, hand-labelled general questions (covered, informal/misspelled, no approved article, off-topic) plus 2 non-English ones reported separately (88 on 31 Oct, extended on 2 Nov). Each case lists the acceptable article ids, or none when no approved article covers it. Keywords are tuned on `split=tune`; `split=holdout` (cases written before the change they test) shows whether a change generalises.

```bash
cd backend && ../.venv/bin/python -m evaluation.retrieval --misses                          # memory store
cd backend && ../.venv/bin/python -m evaluation.retrieval --database-url postgresql://... --misses   # database store (public.kb_search)
```

Metrics (k = 4, the sections passed to the model): **hit@1** right article first, **hit@4** right article passed at all, **MRR** 1/rank averaged, **no-match** share of uncovered questions that return nothing. `tests/test_retrieval_eval.py` holds floors for both stores (database part with `TEST_DATABASE_LOCAL=1`) and requires off-topic questions to return nothing.

Results on 31 Oct 2026, before → after (overall English cases; held-out split in brackets):

| Store | hit@1 | hit@4 | MRR | no-match |
|---|---|---|---|---|
| Memory | 0.52 → 0.95 (0.61 → 0.89) | 0.84 → 1.00 (0.89 → 1.00) | 0.67 → 0.97 (0.74 → 0.94) | 0.72 → 0.76 (0.50 → 0.50) |
| Database | 0.83 → 0.94 (0.89 → 0.96) | 0.98 → 1.00 (1.00 → 1.00) | 0.90 → 0.96 (0.94 → 0.97) | 0.16 → 0.56 (0.13 → 0.38) |

Changes: memory keyword phrases score by word count (a specific phrase beats one broad word) and both stores apply the explicit chat-spelling table (`typos.respell`, not the close-match guesses that turn "change" into "charge"); customer wording added to keywords and broad words removed from `support` and `cards`; migration `0086` makes a database section match only when a question word is in its heading or keywords (before, any shared body word matched, so "Tell me a joke" returned four articles). A score threshold was tried and rejected: unrelated questions scored up to 0.86, correct ones as low as 0.29.

Limits: the cases are small and written by the developers, so tune-split results are optimistic. Questions about topics with no article (home loans, transfer limits) still often return a loosely related article; keywords cannot tell "related" from "answers it". That needs the missing articles and a fixed "not covered" reply. Non-English questions match nothing in either store.

### 2 Nov 2026: client guidance articles (migration `0087`)

**What changed:**
- Seven new articles: compliance review statuses, review documents, uploading documents, answering the bank's questions, review reminders, updating personal details, and working with your RM.
- New versions of `support`, `security`, `statements` and `overseas-transfers`.
- The research behind them is in [ubs-practices.md](../research/ubs-practices.md); the article list is in [knowledge-articles.md](../knowledge-articles.md).

**Case set:**
- Six `gap` cases are now covered and were relabelled: proof of address, review status, file formats, reminders, update address, change phone.
- 21 new tune cases were added, plus 26 new held-out cases written before any tuning (24 covered or informal, 2 with no article).
- The set now has 135 English cases (114 covered, 21 uncovered) plus the 2 non-English ones.

**Results on the new case set:**

| Store | Articles | hit@1 | hit@4 | MRR | no-match |
|---|---|---|---|---|---|
| Memory | before 0087 | 0.57 | 0.61 | 0.59 | 0.67 |
| Memory | after (tune · held-out) | 0.94 (1.00 · 0.87) | 0.97 (1.00 · 0.94) | 0.96 | 0.67 (0.82 · 0.50) |
| Database | before 0087 | 0.57 | 0.62 | 0.59 | 0.48 |
| Database | after (tune · held-out) | 0.93 (0.95 · 0.90) | 0.99 (1.00 · 0.98) | 0.96 | 0.48 (0.64 · 0.30) |

**Reading the results:**
- **Held-out baseline.** On their first run, before any tuning, the new held-out cases scored hit@1 0.87 in memory and 0.92 in the database.
- **Old cases unchanged.** On the original 31 Oct cases, hit@1 and hit@4 did not move (memory 0.95 / 1.00, database 0.94 / 1.00).
- **No-match is unchanged, not worse.** It does not differ with or without `0087` on the same cases. It fell against the 31 Oct figures only because the six relabelled cases used to count as correct empty results. The no-match floors in `test_retrieval_eval.py` were lowered to sit under the new figures: memory 0.6, database 0.45.

**Tuning:**
- **Memory.** New keyword phrases were added, plus the spellings `addr`/`adress` → `address` in `typos.CHAT`.
- **Database.** The first draft lost hit@1 (0.89) because new headings and keywords contained broad words such as "bank", "ask", "mean", "get", "change", "account", "money", "last" and "charges". Section bodies that repeat "review" also outranked the intended section. Removing the broad words and trimming the repetition fixed the tune split.
- **Not purely tune-only.** This clean-up was driven partly by held-out gap and off-topic misses, including "Who won the football last night?", which matched "last year" in a keyword. The off-topic test requires that question to return nothing.

**Remaining held-out misses:**
- "Why is the bank reviewing my information again?" (memory)
- "A caller said he was from the bank and asked me to install an app" (login article ranks first)
- "wat counts as proof of my address" (memory)
- "I need more time to send my documents" (database)

### 3 Nov 2026: hybrid search (migration `0088`)

**What was compared.** Keyword search against hybrid search (keyword ranks fused with embedding similarity, `openai/text-embedding-3-small` through OpenRouter), each with the chat-spelling table (`KNOWLEDGE_RESPELL`) on and off, on a temporary local database with every migration:

```bash
cd backend && ../.venv/bin/python -m evaluation.retrieval --local --search keyword,hybrid --respell on,off --min-similarity 0.2,0.3,0.35,0.4,0.45,0.5 --embedding-cache .cache/embeddings.json
```

**New paraphrase batch.** Written before any hybrid run and never used for tuning (`batch: paraphrase`, all held out):
- 26 questions that share few words with their article ("The shop terminal keeps rejecting my plastic");
- 4 gap and 2 off-topic questions;
- 4 Chinese and Malay questions.

It is reported on its own, so the earlier figures and the test floors are unchanged.

**Choosing the similarity minimum (tune split only).** A section found only by similarity must reach `EMBEDDING_MIN_SIMILARITY`. 0.4 is the lowest value that keeps tune no-match at the keyword level (0.636), and it still finds the non-English tune questions:

| Minimum | 0.2 | 0.3 | 0.35 | **0.4** | 0.45 | 0.5 |
|---|---|---|---|---|---|---|
| Tune no-match | 0.182 | 0.364 | 0.545 | **0.636** | 0.636 | 0.636 |
| Tune non-English hit@4 | 1.0 | 1.0 | 1.0 | **1.0** | 0.5 | 0.0 |

Tune hit@1 is 0.968 at every value (keyword 0.952).

**Results (database store, k = 4):**

| Set-up | hit@1 | hit@4 | MRR | no-match | held-out hit@1 | informal hit@1 | paraphrase hit@1 | paraphrase hit@4 | non-English hit@4 (batch) |
|---|---|---|---|---|---|---|---|---|---|
| Keyword, spelling table on (today) | 0.930 | 0.991 | 0.958 | 0.476 | 0.904 | 0.941 | 0.577 | 0.808 | 0.25 |
| Keyword, spelling table off | 0.904 | 0.974 | 0.936 | 0.476 | 0.885 | 0.765 | 0.577 | 0.808 | 0.25 |
| **Hybrid 0.4, spelling table on** | **0.965** | **1.000** | **0.981** | **0.476** | **0.962** | **1.000** | **0.808** | **0.923** | **1.00** |
| Hybrid 0.4, spelling table off | 0.947 | 1.000 | 0.969 | 0.476 | 0.962 | 0.882 | 0.808 | 0.923 | 1.00 |

For reference, the memory store (keyword, spelling table on) scores 0.939 / 0.974 / 0.955 / 0.667 on the earlier cases and 0.308 hit@1 on the paraphrase batch.

**Timing.** No cache, so every question was a real embedding request:

| Step | p50 | p95 |
|---|---|---|
| Keyword search (database only) | 1.7 ms | 6 ms |
| Hybrid: embedding request | 870 ms | 1,089 ms |
| Hybrid: database query | about 12 ms | about 15 ms |
| Hybrid: total | 887 ms | 1,103 ms |

These times are from this machine to OpenRouter and to the local database. The app adds its Data API call. In the app, the `knowledge_search` log line and the LangSmith `kb.search` run record `embed_ms` and `sql_ms` for each search, so live timings can be compared by `mode`.

**Reading the results:**
- **Hybrid helps most where keywords fail.**
  - Paraphrase hit@1 rose from 0.577 to 0.808.
  - Non-English questions are found: batch hit@4 rose from 0.25 to 1.0, and the tune cases from 0 to 1.0.
  - Held-out hit@1 rose from 0.904 to 0.962.
  - On the earlier cases, the right article is now always in the top 4.
- **"Correctly returns nothing" is no worse, and no better.**
  - Earlier cases: 0.476 in both, and every earlier off-topic question still returns nothing.
  - Paraphrase batch: 0.0 in both. Its gap and off-topic questions already match keywords ("Write me a poem about cats" matches `bank-questions`), and hybrid keeps every keyword match.
  - Fixing that needs keyword clean-up, not embeddings.
- **The spelling table still pays its way.** Turning it off costs informal hit@1 in both modes: keyword 0.941 → 0.765, hybrid 1.000 → 0.882. Embeddings recover part of the loss, not all of it. So `KNOWLEDGE_RESPELL` stays on. The guardrail and routing rules keep their own use of the table (`normalise`) either way.
- **The cost is time.** Each hybrid search waits about 0.9 s for the embedding request; the vector search itself adds about 10 ms. In v2 the tasks of one turn search in parallel, so this is paid about once per turn, before the model call. A faster embedding endpoint (`EMBEDDING_BASE_URL`) would cut most of it.

**Decision:**
- `KNOWLEDGE_SEARCH` stays `keyword` in code, because hybrid needs an embedding key. Turn it on in `.env` when the extra ~0.9 s per turn is acceptable.
- `EMBEDDING_MIN_SIMILARITY` defaults to 0.4.
- The opt-in floors (`TEST_DATABASE_LOCAL=1 TEST_EMBEDDINGS_LIVE=1`) are:
  - earlier cases: hit@1 0.93, hit@4 0.97, no-match 0.45;
  - paraphrase batch: hit@1 0.7.
  - That check runs `services/kb.search` itself with real embeddings.

**Limits:**
- The developers wrote both case sets. The paraphrase batch is small (26 covered questions).
- The sweep printed held-out figures next to tune figures. The minimum was chosen by the tune rule above, but the held-out numbers were visible when it was chosen.
- Not yet run against the hosted project or through PostgREST (the native Data API suite needs the Mac). After applying `17_apply_0088_hosted.sql` and embedding the sections, check one hybrid chat turn and its `knowledge_search` log line.

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
