# Focused core results review

Reviewed on 14 October 2026 against the project's current knowledge summaries.
These are Codex's content checks, not a bank approval or a human sign-off. Raw
live reports deliberately retain `needs_human_review`. The team must decide
whether each complete backlog story meets its agreed scope.

## Completed in this round

The evaluation runner now identifies the selected model and transport. Offline
reports explicitly say that no model was used. Reports include the relevant
implementation fingerprint and keep provider errors separate from answers.

The offline runner previously dropped publisher details when constructing
evidence. As a result, it accepted a test statement calling MoneySense guidance
"our card policy", although the real answer pipeline rejected it. The runner
now passes the same publisher and scope fields as the application. This fixes
the evaluation, not a newly discovered failure in the live banking validator.

The expanded same-dataset comparison went from 18 passing cases and one failure
to 19 passing cases. It includes the valid counterexample: explicitly attributing
the guidance to MoneySense. These figures measure validator behaviour, not AI accuracy.

## Reviewed answers

| Check | Observed result | Assessment |
|---|---|---|
| Minimum payment | Luna explains unpaid-balance interest and possible interest on new purchases; cites `sg-card-payments`. | Matches the supplied summary. No invented rate or bank-specific payment terms. |
| Unknown Platinum fee | Luna says the exact annual fee cannot be confirmed without a matching product record; cites `cards`. | Honest limitation; no amount, cross-bank substitution or promised lookup. |
| Publisher scope | Luna says MoneySense guidance is general education, not DemoBank's policy. | Correct scope. The browser source panel also displays that distinction. |
| Fixed deposit and follow-up | The answer says interest may be reduced or removed and a fee may apply. Its shorter follow-up preserves uncertainty. | Supported by `sg-accounts`; does not turn a possible outcome into a guarantee. |
| Typo and correction | The bot asks about the failed card use and error; after a correction, it explains how to find statements. | Follows the latest message without repeating card troubleshooting. |
| Abuse, apology and off-topic text | Short replies with no banking citations for abuse/apology; redirects a cake request to banking. | Does not paste knowledge instructions or resume an old question. |
| Chinese loan sample | Explains collateral, possible repossession and generally higher unsecured interest; labels the content general MoneySense guidance. | Preserves the source meaning in this sample. Initial timeout remains recorded. |
| Malay loan sample | Distinguishes collateral and its possible sale on non-payment; says unsecured interest is usually higher. | Matches the source meaning in this sample; does not establish eligibility. |
| Tamil loan sample | Explains security, possible repossession and generally higher unsecured interest; keeps a general-information limit. | Matches the source meaning in this sample; wider language review remains useful. |

## Test evidence

- 151 backend tests and 19 offline validator cases pass on the current code.
- The unchanged frontend utilities and build last passed in the Luna integration round: 21 utility tests and the production build.
- The earlier browser run passed 17 workflows, failed the Chinese case on timeout and skipped optional unlinked sign-in. Chinese then passed a targeted rerun.
- One new two-question browser workflow passed, covering unavailable fees, publisher scope and the source panel. There are now 19 distinct non-login workflows with passing evidence across these runs, not one clean 19-test run.
- Three focused backend model requests completed with real AI answers. Live output is reviewed above; it is not converted into an automated accuracy percentage.
- No released prompt version was changed. These results use `v2+88f886d0` and GPT-5.6 Luna through the Codex CLI.

Evidence is saved under [evidence/core-2026-10-14](evidence/core-2026-10-14):
[before](evidence/core-2026-10-14/offline-before.json),
[after](evidence/core-2026-10-14/offline-after.json),
[focused live results](evidence/core-2026-10-14/luna-focused.json) and
[captured browser answers](evidence/core-2026-10-14/browser-answers.json).
These files contain synthetic test questions, never customer records or credentials.

## What remains outside this completion round

The two linked live Supabase customers and broad complex-query reliability stay
deferred. Saved conversations, the UBS mockup and multi-capability architecture
remain separate work. Approved product details, staffed support, full language
coverage and team backlog decisions are still needed before claiming all original
stories complete. The optional unlinked-login/sign-out browser fixture was not
rerun; no database users or permissions were changed.

Luna results do not verify the OpenRouter model intended for showcase day. Use
the focused suite and real browser checks again after switching providers.

## Workflow 1 focused run (24 October 2026)

One live run of `python -m evaluation.run --live --suite focused` (now six cases: the three source questions plus the public Workflow 1 questions), model `openrouter/free` through the API transport, public pipeline. All six were AI answers that passed the basic source checks and cited the expected articles (`savings-fees`, `app-login-recovery`, `car-loans` for the new cases). Manual review: the savings-fee and app-lockout answers match their sources and ask for no secret. The car-loan answer says the bank makes the final decision and claims no approval, but adds an unsupported condition ("whether you are an existing DemoBank customer"); the figure and citation checks cannot catch this. One run is not a reliability measure, and the signed-in three-problem flow (section order, block offer) is not covered by this suite. Its end-to-end spec (`frontend/tests/workflow1.spec.js`) was not run in this round: the native runner needs a PostgREST build that the project does not pin for Windows, and the hosted run was deliberately skipped to avoid writing to the shared project.
