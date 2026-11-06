# Prompt iteration log

One file per released prompt version, under `<agent>/`. Prompt files live in `backend/app/prompts/<agent>/<version>/` and a released version is never edited (see `backend/app/services/prompts.py`); a change means a new prompt directory plus a new file here.

## Where the notes are

Each `<agent>/` folder holds one note per released version (`v<N>.md`), for every model prompt: the answer agents, the client and RM planners, the runner, the RM assistant, talking points, the document checks and the meeting side's minutes writer. The admin dashboard's Prompts page lists them next to each version's live and evaluation use (`prompts.notes()`), and `prompts.in_use()` names the versions the code runs now.

## How to record a version

1. Copy [TEMPLATE.md](TEMPLATE.md) to `<agent>/v<N>.md` and fill every field. Write "not measured" instead of leaving a field blank.
2. Run `python -m evaluation.run --output evaluation-results.json` (offline) and, when a key is available, `--live`. Use `--compare <previous.json>` against the same dataset and mode. On Windows set `PYTHONUTF8=1` first.
3. Copy `prompt_version` (`v<N>+<hash>`), `dataset_sha256` and the summary from the report into the file.
4. Add the new id to `RELEASED` in `backend/tests/test_prompts.py`. Keep rejected versions.

Scope: every prompt sent to a model, client and RM side. Safety-critical cases (secrets, lost/stolen card, human-support request, guest asking about "my" balance) and the other rule answers are fixed no-LLM responses, so prompt changes do not affect them.
