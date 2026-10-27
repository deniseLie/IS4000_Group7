# Prompt iteration log

One file per released prompt version, under `<agent>/`. Prompt files live in `backend/app/prompts/<agent>/<version>/` and a released version is never edited (see `backend/app/services/prompts.py`); a change means a new prompt directory plus a new file here.

## Index (newest first)

| Version | Date | Change | Offline | Live (manual) | Decision |
|---|---|---|---|---|---|
| [fraud v4](fraud/v4.md) | 2026-10-27 | Card-testing pattern evidence, block and dispute offers awaiting review, no internal ids | Validator checks pass | See Workflow 1 Part B | Workflow 1 Part B; v3 retained |
| [fraud v2](fraud/v2.md) | 2026-10-19 | Personal fraud evidence and bounded proposals | Validator and native workflow checks pass | Provider error; accuracy unverified | First personal workflow; public v1 retained |
| [planner v1](planner/v1.md) | 2026-10-19 | Initial public v2 issue planning | Schema/routing checks pass | v2 browser checks pass | Initial foundation |
| [domain common v1](domain_common/v1.md) | 2026-10-19 | Shared output contract and bounded source revision | Validator checks pass | Five real agents checked | Initial foundation |
| [retail v1](retail/v1.md) | 2026-10-19 | Everyday Banking role | Validator checks pass | Real provider + browser | Public evidence only |
| [fraud v1](fraud/v1.md) | 2026-10-19 | Fraud and Security role | Validator checks pass | Real provider + browser | Guidance; no actions |
| [compliance v1](compliance/v1.md) | 2026-10-19 | Compliance Review role | Validator checks pass | Real provider checked | No case tools yet |
| [document v1](document/v1.md) | 2026-10-19 | Document Check role | Validator checks pass | Real provider checked | No uploads yet |
| [relationship v1](relationship/v1.md) | 2026-10-19 | Relationship role | Validator checks pass | Real provider checked | No personal tools yet |
| [answer v2](answer/v2.md) | 2026-10-13 | Real AI conversation and source separation | 17 direct validator checks | See real AI review | Integrated; last live recheck pending |
| [answer v1](answer/v1.md) | 2026-10-12 | Baseline | 17/17 pass | 8 pass, 1 partial, 1 fail, 1 error (of 11) | Adopted as baseline |

## How to record a version

1. Copy [TEMPLATE.md](TEMPLATE.md) to `<agent>/v<N>.md` and fill every field. Write "not measured" instead of leaving a field blank.
2. Run `python -m evaluation.run --output evaluation-results.json` (offline) and, when a key is available, `--live`. Use `--compare <previous.json>` against the same dataset and mode. On Windows set `PYTHONUTF8=1` first.
3. Copy `prompt_version` (`v<N>+<hash>`), `dataset_sha256` and the summary from the report into the file.
4. Add a row to the index above and the new id to `RELEASED` in `backend/tests/test_prompts.py`. Keep rejected versions.

Scope: this covers the LLM answer step only. Safety-critical cases (secrets, lost/stolen card, human-support request, guest asking about "my" balance) are fixed no-LLM responses, so prompt changes do not affect them.
