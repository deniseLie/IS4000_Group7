# Prompt iteration log

One file per released prompt version, under `<agent>/`. Prompt files live in `backend/app/prompts/<agent>/<version>/` and a released version is never edited (see `backend/app/services/prompts.py`); a change means a new prompt directory plus a new file here.

## Index (newest first)

| Version | Date | Change | Offline | Live (manual) | Decision |
|---|---|---|---|---|---|
| [answer v2](answer/v2.md) | 2026-10-13 | Real AI conversation and source separation | 17 direct validator checks | See real AI review | Integrated; last live recheck pending |
| [answer v1](answer/v1.md) | 2026-10-12 | Baseline | 17/17 pass | 8 pass, 1 partial, 1 fail, 1 error (of 11) | Adopted as baseline |

## How to record a version

1. Copy [TEMPLATE.md](TEMPLATE.md) to `<agent>/v<N>.md` and fill every field. Write "not measured" instead of leaving a field blank.
2. Run `python -m evaluation.run --output evaluation-results.json` (offline) and, when a key is available, `--live`. Use `--compare <previous.json>` against the same dataset and mode. On Windows set `PYTHONUTF8=1` first.
3. Copy `prompt_version` (`v<N>+<hash>`), `dataset_sha256` and the summary from the report into the file.
4. Add a row to the index above and the new id to `RELEASED` in `backend/tests/test_prompts.py`. Keep rejected versions.

Scope: this covers the LLM answer step only. Safety-critical cases (secrets, lost/stolen card, human-support request, guest asking about "my" balance) are fixed no-LLM responses, so prompt changes do not affect them.
